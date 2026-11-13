from project_paths import ROOT, DB_PATH
from contextlib import closing
import json
import hashlib
from functools import lru_cache
import os
import sqlite3
from datetime import datetime, timezone, timedelta

from pipeline.migrations import backup_before_migration, migrate
from pipeline.kickoffs import current, observe

DB_NAME = DB_PATH

# Versao do modelo que gera as previsoes gravadas por este codigo.
MODELO_ACTUAL = "v2"
# Momento do merge do modelo v2 (UTC). Previsoes criadas antes sao v1.
DATA_MODELO_V2 = "2026-10-04T13:47:00"
# match_id criados antes de DATA_MODELO_V2 mas ja reescritos com o v2.
MODELO_V2_BACKFILL_JSON = os.path.join(
    str(ROOT), "modelo_v2_backfill.json"
)


def get_connection():
    return sqlite3.connect(DB_NAME)


def init_db(db_path=None):
    path = db_path or DB_NAME
    backup_before_migration(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _init_schema(conn)


def _init_schema(conn):
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            match_id TEXT PRIMARY KEY,
            event_date TEXT,
            timestamp INTEGER,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            xg_home REAL,
            xg_away REAL,
            prob_o25 REAL,
            prob_btts REAL,
            created_at TEXT,
            status TEXT DEFAULT 'pending',
            home_score INTEGER,
            away_score INTEGER,
            result_o25 INTEGER,
            result_btts INTEGER
        )
    """)

    cursor.execute("PRAGMA table_info(predictions)")
    colunas_predictions = {row[1] for row in cursor.fetchall()}
    if "league_id" not in colunas_predictions:
        cursor.execute("ALTER TABLE predictions ADD COLUMN league_id INTEGER")

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_predictions_league_id
        ON predictions (league_id)
    """)

    if "modelo" not in colunas_predictions:
        cursor.execute("ALTER TABLE predictions ADD COLUMN modelo TEXT")
    for nome, tipo in (("prob_o25_base", "REAL"), ("calibracao_o25_id", "TEXT"),
                       ("baixa_confianca", "INTEGER")):
        if nome not in colunas_predictions:
            cursor.execute(f"ALTER TABLE predictions ADD COLUMN {nome} {tipo}")
    _backfill_modelo(cursor)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            capturado_em TEXT NOT NULL,
            quote_at TEXT,
            fonte TEXT NOT NULL DEFAULT 'bsd_consensus',
            mercado TEXT NOT NULL DEFAULT 'over_25_goals',
            odd REAL,
            estado TEXT NOT NULL,
            detalhe TEXT,
            prob_o25_no_instante REAL,
            prob_o25_base_no_instante REAL,
            calibracao_o25_no_instante TEXT,
            prob_btts_no_instante REAL,
            baixa_confianca_no_instante INTEGER,
            UNIQUE(match_id, capturado_em, fonte)
        )
    """)
    cursor.execute("PRAGMA table_info(odds_snapshots)")
    odds_cols = {row[1] for row in cursor.fetchall()}
    for nome, tipo in (("prob_o25_no_instante", "REAL"),
                       ("prob_o25_base_no_instante", "REAL"),
                       ("calibracao_o25_no_instante", "TEXT"),
                       ("prob_btts_no_instante", "REAL"),
                       ("baixa_confianca_no_instante", "INTEGER")):
        if nome not in odds_cols:
            cursor.execute(f"ALTER TABLE odds_snapshots ADD COLUMN {nome} {tipo}")

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_odds_match_source
        ON odds_snapshots(match_id, fonte, capturado_em)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS team_stats (
            team_id INTEGER PRIMARY KEY,
            team_name TEXT,
            attack REAL,
            defense REAL,
            goals_for REAL,
            goals_against REAL,
            over25 REAL,
            btts REAL,
            form REAL,
            updated_at TEXT
        )
    """)

    cursor.execute("PRAGMA table_info(team_stats)")
    colunas_team_stats = {row[1] for row in cursor.fetchall()}
    for nome, tipo in (
        ("origem", "TEXT"),
        ("games", "INTEGER"),
        ("jogos_truncados", "INTEGER"),
    ):
        if nome not in colunas_team_stats:
            cursor.execute(f"ALTER TABLE team_stats ADD COLUMN {nome} {tipo}")

    for name, kind in (("origem_xg", "TEXT"), ("n_h2h", "INTEGER"),
                       ("media_liga_usada", "REAL"), ("media_h2h_bruta", "REAL")):
        if name not in colunas_predictions:
            cursor.execute(f"ALTER TABLE predictions ADD COLUMN {name} {kind}")
    migrate(conn)


# Colunas opcionais (criadas por main.py) que tambem sao reescritas quando
# uma previsao pending e recalculada.
COLUNAS_EXTRA_UPDATE = (
    "origem_xg", "baixa_confianca", "media_liga_usada", "n_h2h", "media_h2h_bruta",
)


def _backfill_modelo(cursor):
    """
    Preenche modelo so onde ainda esta NULL (idempotente; nunca toca em
    xG/probs/resultados):
      1. match_id listados em modelo_v2_backfill.json -> 'v2' (criados
         como v1 mas ja reescritos com o v2 antes desta coluna existir);
      2. created_at < DATA_MODELO_V2 -> 'v1';
      3. created_at >= DATA_MODELO_V2 -> 'v2'.
    """
    cursor.execute("SELECT COUNT(*) FROM predictions WHERE modelo IS NULL")
    if cursor.fetchone()[0] == 0:
        return

    try:
        with open(MODELO_V2_BACKFILL_JSON, encoding="utf-8") as f:
            ids_v2 = json.load(f).get("match_ids", [])
    except (OSError, ValueError) as e:
        print(f"⚠️ AVISO: nao foi possivel ler {MODELO_V2_BACKFILL_JSON} ({e}).")
        ids_v2 = []

    cursor.executemany(
        "UPDATE predictions SET modelo = 'v2' WHERE match_id = ? AND modelo IS NULL",
        [(str(m),) for m in ids_v2],
    )
    cursor.execute(
        "UPDATE predictions SET modelo = 'v1' WHERE modelo IS NULL AND created_at < ?",
        (DATA_MODELO_V2,),
    )
    cursor.execute(
        "UPDATE predictions SET modelo = 'v2' WHERE modelo IS NULL AND created_at >= ?",
        (DATA_MODELO_V2,),
    )


@lru_cache(maxsize=1)
def model_fingerprint():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ('src/predictor.py', 'src/adjusted_xg.py', 'src/main.py')}


def salvar_previsoes_db(jogos):
    """Save current pending view and append observed snapshot in one transaction.

    No historical import, result update or invented observation timestamp.
    """
    if not jogos:
        return set()
    now = datetime.now(timezone.utc)
    observed, now_ts = now.isoformat(), now.timestamp()
    saved_ids = set()
    with closing(get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        for j in jogos:
            match_id = str(j.get('id') or f"{j['home']}_{j['away']}_{j['timestamp']}")
            existing = conn.execute('SELECT status,result_o25,result_btts,timestamp FROM predictions WHERE match_id=?', (match_id,)).fetchone()
            if existing:
                observe(conn,match_id,j['timestamp'],detected_at=now,source='bsd:events:prediction_input')
            if j['timestamp'] <= now_ts:
                continue
            if existing and (existing[0] not in ('pending', None) or
                             existing[1] is not None or existing[2] is not None):
                continue
            if existing and current(conn,match_id)[2] == 'quarantined':
                continue
            conn.execute("""INSERT OR IGNORE INTO predictions
                (match_id,event_date,timestamp,league,league_id,home_team,away_team,created_at,status)
                VALUES (?,?,?,?,?,?,?,?,'pending')""",
                (match_id,j['data_str'],j['timestamp'],j['liga'],j.get('league_id'),j['home'],j['away'],observed))
            if conn.execute('SELECT 1 FROM prediction_snapshots WHERE match_id=? AND predicted_at=? AND modelo=?',
                            (match_id,observed,j.get('modelo',MODELO_ACTUAL))).fetchone():
                saved_ids.add(match_id)
                continue
            values = {'xg_home': j['xg_home'], 'xg_away': j['xg_away'],
                'prob_o25': round(j['o25'],2), 'prob_btts': round(j['btts'],2),
                'modelo': j.get('modelo',MODELO_ACTUAL),
                'prob_o25_base': j.get('o25_base',j['o25']),
                'calibracao_o25_id': j.get('calibracao_o25_id'),
                'prediction_updated_at': observed}
            values.update({key:j[key] for key in COLUNAS_EXTRA_UPDATE if key in j})
            conn.execute('UPDATE predictions SET '+','.join(key+'=?' for key in values)+' WHERE match_id=?', (*values.values(),match_id))
            kickoff, revision_id, _ = current(conn,match_id)
            row = conn.execute('SELECT modelo,prob_o25,prob_btts,prob_o25_base,baixa_confianca,calibracao_o25_id FROM predictions WHERE match_id=?',(match_id,)).fetchone()
            provenance = j.get('provenance', {'source':'bsd', 'origem_xg':j.get('origem_xg'),
                                            'n_h2h':j.get('n_h2h')})
            parameters = j.get('parameters', {'xg_home':j['xg_home'],'xg_away':j['xg_away'],
                                              'media_liga_usada':j.get('media_liga_usada')})
            parameters = dict(parameters, source_sha256=model_fingerprint())
            conn.execute("""INSERT INTO prediction_snapshots
                (match_id,predicted_at,kickoff,modelo,prob_o25,prob_btts,prob_o25_base,
                baixa_confianca,calibracao_o25_id,prob_btts_base,provenance_json,parameters_json,kickoff_revision_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (match_id,observed,kickoff,*row,j.get('btts_base'),
                 json.dumps(provenance,ensure_ascii=False,sort_keys=True,allow_nan=False),
                 json.dumps(parameters,ensure_ascii=False,sort_keys=True,allow_nan=False),revision_id))
            saved_ids.add(match_id)
    print(f"Snapshots pré-jogo guardados: {len(saved_ids)}; base compatível: {DB_NAME}")
    return saved_ids


def guardar_team_stats(team_id, team_name, stats):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO team_stats (
            team_id, team_name, attack, defense,
            goals_for, goals_against,
            over25, btts, form, updated_at, origem,
            games, jogos_truncados
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        team_id,
        team_name,
        stats["attack"],
        stats["defense"],
        stats["goals_for"],
        stats["goals_against"],
        stats["over25"],
        stats["btts"],
        stats["form"],
        datetime.now(timezone.utc).isoformat(),
        stats.get("origem", "default"),
        stats.get("games", 0),
        stats.get("jogos_truncados", 0)
    ))

    conn.commit()
    conn.close()


def carregar_team_stats(team_id):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM team_stats WHERE team_id = ?", (team_id,))
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def team_stats_expiradas(team_id, horas=24):
    stats = carregar_team_stats(team_id)
    if not stats:
        return True

    try:
        updated = datetime.fromisoformat(stats["updated_at"])
    except Exception:
        return True

    return datetime.now(timezone.utc) - updated > timedelta(hours=horas)
