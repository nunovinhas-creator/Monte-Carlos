import sqlite3
from datetime import datetime, timezone

DB_NAME = "predictions.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
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

    conn.commit()
    conn.close()


def salvar_previsoes_db(jogos):

    if not jogos:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    agora_iso = datetime.now(timezone.utc).isoformat()

    for j in jogos:

        match_id = str(
            j.get("id")
            or f"{j['home']}_{j['away']}_{j['timestamp']}"
        )

        cursor.execute("""
            INSERT OR IGNORE INTO predictions (
                match_id,
                event_date,
                timestamp,
                league,
                home_team,
                away_team,
                xg_home,
                xg_away,
                prob_o25,
                prob_btts,
                created_at,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (

            match_id,
            j["data_str"],
            j["timestamp"],
            j["liga"],
            j["home"],
            j["away"],
            j["xg_home"],
            j["xg_away"],
            round(j["o25"], 2),
            round(j["btts"], 2),
            agora_iso

        ))

    conn.commit()
    conn.close()

    print(f"💾 Registos guardados em '{DB_NAME}'.")
