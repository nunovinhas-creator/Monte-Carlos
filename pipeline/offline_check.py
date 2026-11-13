"""Regenerate reports and test migration on an isolated copy, without network."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline.integrity import state, compare, published
from project_paths import ROOT


def main():
    source_hash = (ROOT/'predictions.db').read_bytes()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db_path = root/'predictions.db'
        shutil.copyfile(ROOT/'predictions.db',db_path)
        shutil.copyfile(ROOT/'index.html',root/'index.html')
        shutil.copytree(ROOT/'research',root/'research')
        before = state(db_path)
        env = dict(os.environ,PREDICTIONS_DB=str(db_path),PYTHONPATH=str(ROOT))
        subprocess.run([sys.executable,'-c','from pipeline.database import init_db; init_db(); init_db()'],env=env,cwd=root,check=True)
        for script in ('backtest.py','roi_report.py'):
            subprocess.run([sys.executable,str(ROOT/script)],env=env,cwd=root,check=True)
        errors = compare(before,state(db_path))+published(root)
        if errors:
            raise RuntimeError('; '.join(errors))
    if source_hash != (ROOT/'predictions.db').read_bytes():
        raise RuntimeError('Source historical database changed')
    print('Offline pipeline: migrated copy, reports and preserved original history OK.')


if __name__ == '__main__':
    main()
