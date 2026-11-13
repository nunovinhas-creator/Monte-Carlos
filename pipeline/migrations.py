"""Additive migration only. Existing observations are never reconstructed."""
import hashlib
import sqlite3
from pathlib import Path


def backup_before_migration(db_path):
    path = Path(db_path)
    if not path.exists():
        return None
    with sqlite3.connect(path) as source:
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='odds_capture_audit'").fetchone():
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        directory = path.parent / 'data' / 'backups'
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f'pre-kickoffs-{digest}.db'
        if not target.exists():
            with sqlite3.connect(target) as backup:
                source.backup(backup)
        return target


def migrate(conn):
    """Caller owns transaction; errors roll back DDL and data together."""
    for table, columns in {
        'predictions': [('settled_at', 'TEXT'), ('prediction_updated_at', 'TEXT')],
        'odds_snapshots': [('modelo_no_instante', 'TEXT'), ('prediction_snapshot_id', 'INTEGER')],
    }.items():
        existing = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        for name, kind in columns:
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
    conn.execute('''CREATE TABLE IF NOT EXISTS prediction_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL REFERENCES predictions(match_id),
        predicted_at TEXT NOT NULL,
        kickoff INTEGER NOT NULL,
        modelo TEXT NOT NULL,
        prob_o25 REAL NOT NULL CHECK(prob_o25 BETWEEN 0 AND 100),
        prob_btts REAL NOT NULL CHECK(prob_btts BETWEEN 0 AND 100),
        prob_o25_base REAL,
        prob_btts_base REAL,
        baixa_confianca INTEGER CHECK(baixa_confianca IN (0,1)),
        calibracao_o25_id TEXT,
        provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
        parameters_json TEXT NOT NULL CHECK(json_valid(parameters_json)),
        UNIQUE(match_id, predicted_at, modelo),
        CHECK(unixepoch(predicted_at) IS NOT NULL AND unixepoch(predicted_at) < kickoff)
    )''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_prediction_snapshot_time
        ON prediction_snapshots(match_id, predicted_at, id)''')
    conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_one_valid_odds
        ON odds_snapshots(match_id, fonte) WHERE estado='ok' ''')
    for table in ('prediction_snapshots', 'odds_snapshots'):
        for action in ('UPDATE', 'DELETE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table} BEGIN
                SELECT RAISE(ABORT, 'immutable snapshot'); END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS predictions_no_delete
        BEFORE DELETE ON predictions BEGIN
        SELECT RAISE(ABORT, 'historical prediction cannot be deleted'); END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS predictions_settled_immutable
        BEFORE UPDATE ON predictions
        WHEN OLD.status = 'finished' OR OLD.result_o25 IS NOT NULL OR OLD.result_btts IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'settled prediction is immutable'); END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS odds_pre_match_only
        BEFORE INSERT ON odds_snapshots WHEN NEW.estado = 'ok'
        BEGIN
          SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM predictions p WHERE p.match_id=NEW.match_id
              AND unixepoch(NEW.capturado_em) < p.timestamp
              AND unixepoch(NEW.quote_at) <= unixepoch(NEW.capturado_em)
              AND unixepoch(NEW.capturado_em)-unixepoch(NEW.quote_at) BETWEEN 0 AND 21600
              AND p.timestamp-unixepoch(NEW.capturado_em) BETWEEN 1 AND 21600
              AND NEW.odd BETWEEN 1.01 AND 100
          ) THEN RAISE(ABORT, 'invalid odds chronology') END;
        END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS snapshot_pre_match_only
        BEFORE INSERT ON prediction_snapshots
        WHEN NOT EXISTS (SELECT 1 FROM predictions p WHERE p.match_id=NEW.match_id
            AND p.status='pending' AND p.result_o25 IS NULL AND p.result_btts IS NULL
            AND unixepoch(NEW.predicted_at) < p.timestamp)
        BEGIN SELECT RAISE(ABORT, 'snapshot requires pending pre-match prediction'); END''')
    from pipeline.schedule_migration import migrate_schedule
    migrate_schedule(conn)
