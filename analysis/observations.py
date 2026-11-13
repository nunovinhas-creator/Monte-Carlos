"""Separate, named observation definitions. Never synthesize earlier snapshots."""
import sqlite3
from pathlib import Path


def prediction_rows(db_path, definition):
    if definition not in ('first', 'last'):
        raise ValueError('definition must be first or last')
    order = 'ASC' if definition == 'first' else 'DESC'
    with sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='prediction_snapshots'").fetchone():
            return []
        return [dict(r) for r in db.execute(f'''
            WITH ranked AS (
              SELECT p.match_id,p.league,p.league_id,p.timestamp,p.result_o25,p.result_btts,
                s.modelo,s.prob_o25,s.prob_btts,s.prob_o25_base,s.predicted_at,s.kickoff,
                s.provenance_json,s.parameters_json,
                ROW_NUMBER() OVER (PARTITION BY p.match_id ORDER BY julianday(s.predicted_at) {order},s.id {order}) pos
              FROM prediction_snapshots s JOIN predictions p USING(match_id)
              WHERE p.status='finished' AND p.result_o25 IN (0,1) AND p.result_btts IN (0,1)
                AND unixepoch(s.predicted_at)<s.kickoff AND unixepoch(s.predicted_at)<p.timestamp
            ) SELECT * FROM ranked WHERE pos=1 ORDER BY timestamp,match_id
        ''')]
