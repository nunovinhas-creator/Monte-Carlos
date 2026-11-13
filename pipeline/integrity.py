"""Read-only historical invariants and publication gate (no BSD calls)."""
import argparse
import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from project_paths import DB_PATH


def state(db_path):
    with sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro', uri=True) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {'counts': {}, 'tables': {}}
        for table, key in [('predictions', 'match_id'), ('odds_snapshots', 'id'), ('prediction_snapshots', 'id')]:
            if table not in tables:
                continue
            cols = [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
            result['tables'][table] = {'columns': cols, 'rows': {
                str(row[cols.index(key)]): list(row)
                for row in db.execute(f'SELECT * FROM {table} ORDER BY {key}')}}
        for table in tables:
            result['counts'][table] = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return result


def compare(before, after):
    errors = []
    for table, count in before['counts'].items():
        if after['counts'].get(table, 0) < count:
            errors.append(f'{table}: count decreased')
    for table, old in before['tables'].items():
        new = after['tables'].get(table, {'columns': [], 'rows': {}})
        if not set(old['columns']).issubset(new['columns']):
            errors.append(f'{table}: columns lost')
            continue
        for key, old_row in old['rows'].items():
            if key not in new['rows']:
                errors.append(f'{table}/{key}: row lost')
                continue
            a = dict(zip(old['columns'], old_row))
            b = dict(zip(new['columns'], new['rows'][key]))
            locked = table != 'predictions' or a.get('status') == 'finished' or a.get('result_o25') is not None or a.get('result_btts') is not None
            columns = old['columns'] if locked else ['match_id', 'created_at', 'timestamp', 'event_date', 'home_team', 'away_team', 'league', 'league_id']
            if any(a.get(c) != b.get(c) for c in columns):
                errors.append(f'{table}/{key}: historical values changed')
    return errors


def audit(db_path, now=None):
    now = now or datetime.now(timezone.utc)
    errors, warnings = [], []
    with sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            errors.append('SQLite integrity_check failed')
        checks = {
            'duplicate match IDs': 'SELECT COUNT(*) FROM (SELECT match_id FROM predictions GROUP BY match_id HAVING COUNT(*)>1)',
            'contradictory results': '''SELECT COUNT(*) FROM predictions WHERE status='finished' AND
                (home_score IS NULL OR away_score IS NULL OR home_score<0 OR away_score<0 OR
                 result_o25 IS NULL OR result_btts IS NULL OR
                 result_o25 != (home_score+away_score>2) OR result_btts != (home_score>0 AND away_score>0))''',
            'invalid probabilities': '''SELECT COUNT(*) FROM predictions WHERE
                prob_o25 NOT BETWEEN 0 AND 100 OR prob_btts NOT BETWEEN 0 AND 100''',
            'invalid valid-odds chronology': '''SELECT COUNT(*) FROM odds_snapshots s LEFT JOIN predictions p USING(match_id)
                WHERE s.estado='ok' AND (p.match_id IS NULL OR s.odd NOT BETWEEN 1.01 AND 100 OR
                unixepoch(s.capturado_em) IS NULL OR unixepoch(s.quote_at) IS NULL OR
                unixepoch(s.capturado_em)>=p.timestamp OR unixepoch(s.quote_at)>unixepoch(s.capturado_em) OR
                unixepoch(s.capturado_em)-unixepoch(s.quote_at) NOT BETWEEN 0 AND 21600 OR
                p.timestamp-unixepoch(s.capturado_em) NOT BETWEEN 1 AND 21600)''',
            'duplicate valid odds': "SELECT COUNT(*) FROM (SELECT match_id,fonte FROM odds_snapshots WHERE estado='ok' GROUP BY match_id,fonte HAVING COUNT(*)>1)",
        }
        for label, sql in checks.items():
            n = db.execute(sql).fetchone()[0]
            if n:
                errors.append(f'{label}: {n}')
        late = db.execute("SELECT COUNT(*) FROM predictions WHERE status='finished' AND unixepoch(created_at)>=timestamp").fetchone()[0]
        if late:
            warnings.append(f'{late} legacy predictions created at/after kickoff; no first/last reconstruction')
        start = int(now.timestamp()) - 7 * 86400
        n, quoted = db.execute('''SELECT COUNT(*), COALESCE(SUM(EXISTS (
            SELECT 1 FROM odds_snapshots s WHERE s.match_id=p.match_id AND s.estado='ok'
            AND unixepoch(s.capturado_em)<p.timestamp)),0)
            FROM predictions p WHERE modelo='v2' AND status='finished' AND timestamp>=? AND timestamp<?''',
            (start, int(now.timestamp()))).fetchone()
        coverage = quoted / n if n else None
        return {'errors': errors, 'warnings': warnings, 'coverage_7d': coverage,
                'coverage_n': n, 'coverage_quoted': quoted, 'db_sha256': hashlib.sha256(Path(db_path).read_bytes()).hexdigest()}


def published(directory):
    """Validate generated documents and their local links; no stale file shortcut."""
    from html.parser import HTMLParser
    class Document(HTMLParser):
        def __init__(self):
            super().__init__(); self.links = []; self.title = False; self.body = False
        def handle_starttag(self, tag, attrs):
            self.title |= tag == 'title'; self.body |= tag == 'body'
            self.links += [v for k, v in attrs if k in ('href', 'src')]
    errors = []
    directory = Path(directory)
    for name in ('index.html', 'backtest.html', 'roi.html'):
        path = directory / name
        if not path.exists():
            errors.append(f'missing {name}'); continue
        text = path.read_text(encoding='utf-8')
        doc = Document(); doc.feed(text)
        if not doc.title or not doc.body or '</html>' not in text.lower():
            errors.append(f'incomplete {name}')
        for link in doc.links:
            if not link or link.startswith(('https:', 'http:', '#', 'data:', 'mailto:')):
                continue
            if not (directory / link.split('#')[0].split('?')[0]).exists():
                errors.append(f'{name}: broken {link}')
    path = directory / 'roi_jogos.csv'
    if not path.exists():
        errors.append('missing ROI CSV')
    else:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        if rows and len({r['match_id'] for r in rows}) != len(rows):
            errors.append('duplicate ROI CSV match IDs')
    return errors


def cli():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', default=DB_PATH)
    ap.add_argument('--write-state')
    ap.add_argument('--compare')
    ap.add_argument('--published')
    ap.add_argument('--min-coverage', type=float, default=0)
    args = ap.parse_args()
    result = audit(args.db)
    if args.compare:
        result['errors'] += compare(json.loads(Path(args.compare).read_text()), state(args.db))
    if args.published:
        result['errors'] += published(args.published)
    if result['coverage_n'] >= 20 and result['coverage_7d'] < args.min_coverage:
        result['errors'].append(f'odds coverage below registered minimum {args.min_coverage:.0%}')
    if args.write_state:
        Path(args.write_state).write_text(json.dumps(state(args.db), ensure_ascii=False)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    cli()
