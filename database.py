import sqlite3
from datetime import datetime, timezone, timedelta

DB_NAME = "predictions.db"


def get_connection():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = get_connection()
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

    conn.commit()
    conn.close()


def salvar_previsoes_db(jogos):
    if not jogos:
        return

    conn = get_connection()
    cursor = conn.cursor()
    agora_iso = datetime.now(timezone.utc).isoformat()

    for j in jogos:
        match_id = str(j.get("id") or f"{j['home']}_{j['away']}_{j['timestamp']}")

        cursor.execute("""
            INSERT OR IGNORE INTO predictions (
                match_id,event_date,timestamp,league,home_team,away_team,
                xg_home,xg_away,prob_o25,prob_btts,created_at,status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            match_id, j["data_str"], j["timestamp"], j["liga"],
            j["home"], j["away"], j["xg_home"], j["xg_away"],
            round(j["o25"], 2), round(j["btts"], 2), agora_iso
        ))

    conn.commit()
    conn.close()
    print(f"💾 Registos guardados em '{DB_NAME}'.")


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
