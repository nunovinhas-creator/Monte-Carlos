"""Additive schedule migration. The caller owns transaction and backup."""


def migrate_schedule(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS kickoff_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL REFERENCES predictions(match_id),
        detected_at TEXT NOT NULL CHECK(unixepoch(detected_at) IS NOT NULL),
        old_kickoff INTEGER,
        new_kickoff INTEGER,
        source TEXT NOT NULL,
        change_kind TEXT NOT NULL CHECK(change_kind IN ('postponement','advancement','data_correction','unknown')),
        state TEXT NOT NULL CHECK(state IN ('applied','quarantined')),
        reason TEXT NOT NULL,
        previous_revision_id INTEGER REFERENCES kickoff_revisions(id),
        CHECK(state!='applied' OR (new_kickoff IS NOT NULL AND unixepoch(detected_at)<new_kickoff))
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_kickoff_match ON kickoff_revisions(match_id,id)')
    conn.execute('''CREATE VIEW IF NOT EXISTS operational_schedule AS
        SELECT p.match_id,
            CASE WHEN r.state='quarantined' THEN NULL ELSE COALESCE(r.new_kickoff,p.timestamp) END AS operational_kickoff,
            r.id AS revision_id, COALESCE(r.state,'original') AS schedule_state,
            COALESCE((SELECT k.new_kickoff FROM kickoff_revisions k
                WHERE k.match_id=p.match_id AND k.state='applied' ORDER BY k.id DESC LIMIT 1),p.timestamp) AS last_confirmed_kickoff
        FROM predictions p LEFT JOIN kickoff_revisions r ON r.id=(
            SELECT MAX(k.id) FROM kickoff_revisions k WHERE k.match_id=p.match_id)''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS predictions_original_times_immutable
        BEFORE UPDATE OF timestamp,event_date,created_at ON predictions
        WHEN NEW.timestamp IS NOT OLD.timestamp OR NEW.event_date IS NOT OLD.event_date
            OR NEW.created_at IS NOT OLD.created_at
        BEGIN SELECT RAISE(ABORT,'original prediction times are immutable'); END''')
    for table, additions in {
        'prediction_snapshots': [('kickoff_revision_id','INTEGER REFERENCES kickoff_revisions(id)')],
        'odds_snapshots': [('kickoff_revision_id','INTEGER REFERENCES kickoff_revisions(id)'),
                           ('kickoff_at_capture','INTEGER')],
    }.items():
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        for name, kind in additions:
            if name not in columns:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
    conn.execute('''CREATE TABLE IF NOT EXISTS legacy_prediction_flags (
        match_id TEXT PRIMARY KEY REFERENCES predictions(match_id),
        classification TEXT NOT NULL,
        reason TEXT NOT NULL
    )''')
    conn.execute('''INSERT OR IGNORE INTO legacy_prediction_flags
        SELECT match_id,'legacy_unverifiable','created_at_at_or_after_original_kickoff'
        FROM predictions WHERE unixepoch(created_at)>=timestamp''')
    conn.execute('''CREATE TABLE IF NOT EXISTS odds_capture_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at TEXT NOT NULL,
        match_id TEXT NOT NULL REFERENCES predictions(match_id),
        kickoff_revision_id INTEGER,
        decision TEXT NOT NULL,
        queried INTEGER NOT NULL CHECK(queried IN (0,1)),
        detail TEXT,
        UNIQUE(run_at,match_id)
    )''')
    for table in ('kickoff_revisions','legacy_prediction_flags','odds_capture_audit'):
        for action in ('UPDATE','DELETE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table} BEGIN
                SELECT RAISE(ABORT,'immutable audit record'); END''')
    # A quote belongs to one schedule revision. No existing row is rewritten.
    index = conn.execute("SELECT sql FROM sqlite_master WHERE name='idx_one_valid_odds'").fetchone()
    if index and 'kickoff_revision_id' not in index[0]:
        conn.execute('DROP INDEX idx_one_valid_odds')
    conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_one_valid_odds
        ON odds_snapshots(match_id,fonte,COALESCE(kickoff_revision_id,0)) WHERE estado='ok' ''')
    triggers = {
        'snapshot_pre_match_only': '''BEFORE INSERT ON prediction_snapshots
            WHEN NOT EXISTS (SELECT 1 FROM predictions p JOIN operational_schedule o USING(match_id)
                WHERE p.match_id=NEW.match_id AND p.status='pending'
                AND p.result_o25 IS NULL AND p.result_btts IS NULL
                AND o.schedule_state!='quarantined' AND NEW.kickoff=o.operational_kickoff
                AND NEW.kickoff_revision_id IS o.revision_id
                AND unixepoch(NEW.predicted_at)<o.operational_kickoff
                AND (o.revision_id IS NULL OR julianday(NEW.predicted_at)>=(
                    SELECT julianday(detected_at) FROM kickoff_revisions WHERE id=o.revision_id)))
            BEGIN SELECT RAISE(ABORT,'snapshot requires pending pre-match prediction'); END''',
        'odds_pre_match_only': '''BEFORE INSERT ON odds_snapshots WHEN NEW.estado='ok'
            BEGIN SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM predictions p JOIN operational_schedule o USING(match_id)
                WHERE p.match_id=NEW.match_id AND p.status='pending'
                AND p.result_o25 IS NULL AND p.result_btts IS NULL
                AND o.schedule_state!='quarantined' AND NEW.kickoff_revision_id IS o.revision_id
                AND COALESCE(NEW.kickoff_at_capture,p.timestamp)=o.operational_kickoff
                AND unixepoch(NEW.capturado_em)<o.operational_kickoff
                AND unixepoch(NEW.quote_at)<=unixepoch(NEW.capturado_em)
                AND unixepoch(NEW.capturado_em)-unixepoch(NEW.quote_at) BETWEEN 0 AND 21600
                AND o.operational_kickoff-unixepoch(NEW.capturado_em) BETWEEN 1 AND 21600
                AND NEW.odd BETWEEN 1.01 AND 100
                AND (o.revision_id IS NULL OR (NEW.prediction_snapshot_id IS NOT NULL
                    AND julianday(NEW.quote_at)>=(SELECT julianday(detected_at) FROM kickoff_revisions WHERE id=o.revision_id)))
            ) THEN RAISE(ABORT,'invalid odds chronology') END; END''',
        'odds_snapshot_link': '''BEFORE INSERT ON odds_snapshots
            WHEN NEW.prediction_snapshot_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM prediction_snapshots s WHERE s.id=NEW.prediction_snapshot_id
                AND s.match_id=NEW.match_id AND s.kickoff_revision_id IS NEW.kickoff_revision_id
                AND s.kickoff=NEW.kickoff_at_capture
                AND unixepoch(s.predicted_at)<=unixepoch(NEW.capturado_em)
                AND s.modelo IS NEW.modelo_no_instante AND s.prob_o25 IS NEW.prob_o25_no_instante
                AND s.prob_btts IS NEW.prob_btts_no_instante
                AND s.prob_o25_base IS NEW.prob_o25_base_no_instante
                AND s.calibracao_o25_id IS NEW.calibracao_o25_no_instante
                AND s.baixa_confianca IS NEW.baixa_confianca_no_instante)
            BEGIN SELECT RAISE(ABORT,'odds snapshot link mismatch'); END''',
    }
    for name, sql in triggers.items():
        existing = conn.execute('SELECT sql FROM sqlite_master WHERE type=\'trigger\' AND name=?',(name,)).fetchone()
        desired = f'CREATE TRIGGER {name} {sql}'
        if existing and existing[0] == desired:
            continue
        conn.execute(f'DROP TRIGGER IF EXISTS {name}')
        conn.execute(desired)
    # Evidence imported from the six explicit warnings in run #820. That log
    # omitted the received dates: retain NULL rather than inventing a schedule.
    # Only the exact audited rows are affected; never overwrite later revisions.
    from datetime import datetime
    from pipeline.kickoffs import observe
    conflicts = {
        '649924': (1794574800,'2026-11-13T12:50:25.425867+00:00'),
        '604616': (1794754800,'2026-11-13T12:50:25.426004+00:00'),
        '606209': (1794600000,'2026-11-13T12:50:25.426114+00:00'),
        '654308': (1794661200,'2026-11-13T12:50:25.426229+00:00'),
        '604619': (1794754800,'2026-11-13T12:50:25.426351+00:00'),
        '604620': (1794754800,'2026-11-13T12:50:25.426490+00:00'),
    }
    for match_id,(timestamp,detected) in conflicts.items():
        if conn.execute('SELECT 1 FROM predictions WHERE match_id=? AND timestamp=?',(match_id,timestamp)).fetchone() and not conn.execute(
                'SELECT 1 FROM kickoff_revisions WHERE match_id=?',(match_id,)).fetchone():
            observe(conn,match_id,None,detected_at=datetime.fromisoformat(detected),
                source='github:actions/runs/43428215133:job/131894823856:bsd_warning',
                reason='run_820_conflict_new_time_not_logged')
