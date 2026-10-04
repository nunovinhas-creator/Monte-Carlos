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

    cursor.execute("PRAGMA table_info(predictions)")
    colunas_predictions = {row[1] for row in cursor.fetchall()}
    if "league_id" not in colunas_predictions:
        cursor.execute("ALTER TABLE predictions ADD COLUMN league_id INTEGER")

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_predictions_league_id
        ON predictions (league_id)
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


# Colunas opcionais (criadas por main.py) que tambem sao reescritas quando
# uma previsao pending e recalculada.
COLUNAS_EXTRA_UPDATE = (
    "origem_xg", "baixa_confianca", "media_liga_usada", "n_h2h", "media_h2h_bruta",
)


def salvar_previsoes_db(jogos):
    """
    INSERT para match_id novo; UPDATE de xG/probs (e colunas extra, se
    existirem) para match_id ja existente que ainda esteja pending, sem
    resultado e cujo jogo ainda nao comecou. Linhas finished/void/stale
    ou com result_o25/result_btts preenchidos nunca sao alteradas.
    created_at nao e alterado.
    """
    if not jogos:
        return

    conn = get_connection()
    cursor = conn.cursor()
    agora = datetime.now(timezone.utc)
    agora_iso = agora.isoformat()
    agora_ts = int(agora.timestamp())

    cursor.execute("PRAGMA table_info(predictions)")
    colunas = {row[1] for row in cursor.fetchall()}
    extras = [c for c in COLUNAS_EXTRA_UPDATE if c in colunas]

    n_inseridas = n_actualizadas = n_intocadas = 0

    for j in jogos:
        match_id = str(j.get("id") or f"{j['home']}_{j['away']}_{j['timestamp']}")

        cursor.execute("""
            INSERT OR IGNORE INTO predictions (
                match_id,event_date,timestamp,league,league_id,home_team,away_team,
                xg_home,xg_away,prob_o25,prob_btts,created_at,status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            match_id, j["data_str"], j["timestamp"], j["liga"], j.get("league_id"),
            j["home"], j["away"], j["xg_home"], j["xg_away"],
            round(j["o25"], 2), round(j["btts"], 2), agora_iso
        ))
        if cursor.rowcount:
            n_inseridas += 1
            continue

        extras_jogo = [c for c in extras if c in j]
        sets = ["xg_home = ?", "xg_away = ?", "prob_o25 = ?", "prob_btts = ?"]
        sets += [f"{c} = ?" for c in extras_jogo]
        valores = [j["xg_home"], j["xg_away"], round(j["o25"], 2), round(j["btts"], 2)]
        valores += [j[c] for c in extras_jogo]

        cursor.execute(f"""
            UPDATE predictions
            SET {", ".join(sets)}
            WHERE match_id = ?
              AND (status = 'pending' OR status IS NULL)
              AND result_o25 IS NULL AND result_btts IS NULL
              AND timestamp > ?
        """, (*valores, match_id, agora_ts))
        if cursor.rowcount:
            n_actualizadas += 1
        else:
            n_intocadas += 1

    conn.commit()
    conn.close()
    print(f"💾 Registos guardados em '{DB_NAME}': {n_inseridas} inserida(s), "
          f"{n_actualizadas} pending actualizada(s) com o modelo actual, "
          f"{n_intocadas} intocada(s) (ja comecou/liquidada/void/stale).")


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
