import os
import sqlite3
import requests
from datetime import datetime, timezone, timedelta

API_TOKEN = os.getenv("BSD_API_TOKEN")
BASE_URL = "https://sports.bzzoiro.com/api/v2"

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json"
}

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

def obter_jogos_pendentes_passados():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Seleciona jogos 'pending' onde o timestamp do evento seja inferior ao timestamp atual menos 2.5 horas
    agora_ts = int((datetime.now(timezone.utc) - timedelta(hours=2, minutes=30)).timestamp())
    
    cursor.execute("""
        SELECT match_id, home_team, away_team, timestamp 
        FROM predictions 
        WHERE status = 'pending' AND timestamp <= ?
    """, (agora_ts,))
    
    jogos = cursor.fetchall()
    conn.close()
    return jogos

def consultar_resultado_api(match_id):
    # Procura pelo ID direto se for numérico
    if str(match_id).isdigit():
        url = f"{BASE_URL}/events/{match_id}/"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                data = res.json()
                status = str(data.get('status', '')).lower()
                if status in ['finished', 'ft', 'ended', 'completed', '2']:
                    scores = data.get('scores', {})
                    home_s = scores.get('home') if isinstance(scores, dict) else data.get('home_score')
                    away_s = scores.get('away') if isinstance(scores, dict) else data.get('away_score')
                    if home_s is not None and away_s is not None:
                        return int(home_s), int(away_s)
        except Exception as e:
            print(f"⚠️ Erro ao consultar jogo {match_id}: {e}")

    return None, None

def resolver_jogos():
    init_db()
    pendentes = obter_jogos_pendentes_passados()
    
    if not pendentes:
        print("ℹ️ Nenhum jogo pendente elegível para liquidação de resultados.")
        return

    print(f"🔍 A verificar resultados para {len(pendentes)} jogos pendentes...")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    resolvidos = 0

    for match_id, home, away, ts in pendentes:
        home_score, away_score = consultar_resultado_api(match_id)
        
        if home_score is not None and away_score is not None:
            res_o25 = 1 if (home_score + away_score) > 2.5 else 0
            res_btts = 1 if (home_score > 0 and away_score > 0) else 0

            cursor.execute("""
                UPDATE predictions
                SET home_score = ?,
                    away_score = ?,
                    result_o25 = ?,
                    result_btts = ?,
                    status = 'finished'
                WHERE match_id = ?
            """, (home_score, away_score, res_o25, res_btts, str(match_id)))
            
            resolvidos += 1
            print(f"✅ Liquidado: {home} {home_score}-{away_score} {away} | Over2.5: {res_o25} | BTTS: {res_btts}")

    conn.commit()
    conn.close()
    print(f"🏁 Processo concluído: {resolvidos} de {len(pendentes)} jogos resolvidos.")

if __name__ == "__main__":
    resolver_jogos()
