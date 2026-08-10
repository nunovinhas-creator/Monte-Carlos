"""
settlement.py

Liquida jogos pendentes com o resultado final da BSD.

Mudancas face a versao anterior:
- Modo diagnostico (SETTLEMENT_DEBUG=1) que imprime a forma real do payload.
- Extracao de status e de golos tolerante a varias formas de resposta.
- Contadores por motivo de falha. Se 100% falharem, o script sai != 0,
  para o workflow nao dar verde sobre um estado quebrado.
"""

import os
import sqlite3
import sys
import json
import time
from collections import Counter
from datetime import datetime, timezone, timedelta

import requests

API_TOKEN = os.getenv("BSD_API_TOKEN")
BASE_URL = "https://sports.bzzoiro.com/api/v2"
DB_NAME = os.getenv("PREDICTIONS_DB", "predictions.db")
DEBUG = os.getenv("SETTLEMENT_DEBUG", "0") == "1"
DELAY = float(os.getenv("SETTLEMENT_DELAY", "0.4"))

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json",
}

RATE_LIMIT_BACKOFF = [2, 4, 8]  # segundos, por tentativa de retry apos 429


class RateLimitPersistente(Exception):
    """429 persistiu apos todas as tentativas de backoff."""


STATUS_FINAL = {
    "finished", "ft", "ended", "completed", "complete", "closed",
    "full-time", "fulltime", "after extra time", "aet",
    "after penalties", "ap", "pen", "2", "3", "100",
}

STATUS_NAO_JOGADO = {
    "cancelled", "canceled", "postponed", "abandoned",
    "suspended", "awarded", "walkover", "interrupted", "deleted",
}


def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute(
        """
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
        """
    )
    conn.commit()
    conn.close()


def obter_jogos_pendentes_passados(limite=200):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    agora_ts = int(
        (datetime.now(timezone.utc) - timedelta(hours=2, minutes=30)).timestamp()
    )

    cursor.execute(
        """
        SELECT match_id, home_team, away_team, timestamp
        FROM predictions
        WHERE status = 'pending' AND timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (agora_ts, limite),
    )

    jogos = cursor.fetchall()
    conn.close()
    return jogos


def _extrair_status(data):
    """
    O status pode vir como string, como int, ou aninhado num dict.
    """
    for chave in ("status", "state", "match_status", "event_status", "time_status"):
        val = data.get(chave)

        if isinstance(val, dict):
            for sub in ("type", "code", "name", "short_name", "description"):
                if val.get(sub) is not None:
                    return str(val[sub]).strip().lower()

        if val is not None:
            return str(val).strip().lower()

    return ""


def _extrair_golos(data):
    """
    Tenta varias formas conhecidas de representar o resultado final.
    Devolve (home, away) ou (None, None).
    """
    # Forma 1: dict 'scores' / 'score' / 'result'
    for chave in ("scores", "score", "result", "ft_score", "final_score"):
        bloco = data.get(chave)
        if isinstance(bloco, dict):
            for kh, ka in (
                ("home", "away"),
                ("home_score", "away_score"),
                ("localteam", "visitorteam"),
                ("ft_home", "ft_away"),
                ("full_time_home", "full_time_away"),
            ):
                h, a = bloco.get(kh), bloco.get(ka)
                if h is not None and a is not None:
                    try:
                        return int(h), int(a)
                    except (TypeError, ValueError):
                        pass

            # scores aninhado noutro nivel (ex: {"fulltime": {...}})
            for sub in bloco.values():
                if isinstance(sub, dict):
                    h, a = sub.get("home"), sub.get("away")
                    if h is not None and a is not None:
                        try:
                            return int(h), int(a)
                        except (TypeError, ValueError):
                            pass

    # Forma 2: campos planos no topo
    for kh, ka in (
        ("home_score", "away_score"),
        ("home_goals", "away_goals"),
        ("goals_home", "goals_away"),
        ("home_team_score", "away_team_score"),
        ("ft_home", "ft_away"),
    ):
        h, a = data.get(kh), data.get(ka)
        if h is not None and a is not None:
            try:
                return int(h), int(a)
            except (TypeError, ValueError):
                pass

    return None, None


def consultar_resultado_api(match_id, motivos):
    if not str(match_id).isdigit():
        motivos["id_nao_numerico"] += 1
        return None, None, None

    url = f"{BASE_URL}/events/{match_id}/"

    time.sleep(DELAY)

    tentativa = 0
    while True:
        try:
            res = requests.get(url, headers=HEADERS, timeout=30)
        except Exception as e:
            motivos["excecao_rede"] += 1
            print(f"AVISO {match_id}: erro de rede: {e}")
            return None, None, None

        if res.status_code != 429:
            break

        if tentativa >= len(RATE_LIMIT_BACKOFF):
            motivos["http_429"] += 1
            raise RateLimitPersistente(match_id)

        retry_after = res.headers.get("Retry-After")
        if retry_after is not None:
            try:
                espera = float(retry_after)
            except ValueError:
                espera = RATE_LIMIT_BACKOFF[tentativa]
        else:
            espera = RATE_LIMIT_BACKOFF[tentativa]

        print(
            f"AVISO {match_id}: HTTP 429 (tentativa {tentativa + 1}/"
            f"{len(RATE_LIMIT_BACKOFF)}), a aguardar {espera}s..."
        )
        time.sleep(espera)
        tentativa += 1

    if res.status_code != 200:
        motivos[f"http_{res.status_code}"] += 1
        return None, None, None

    try:
        data = res.json()
    except Exception:
        motivos["json_invalido"] += 1
        return None, None, None

    # A API pode devolver o evento dentro de um envelope
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if isinstance(data, dict) and isinstance(data.get("result"), dict) and "status" not in data:
        data = data["result"]

    status = _extrair_status(data)

    if DEBUG:
        print(f"DEBUG {match_id}: status='{status}' keys={sorted(data.keys())}")
        print(f"DEBUG {match_id}: payload={json.dumps(data, ensure_ascii=False)[:1200]}")

    if status in STATUS_NAO_JOGADO:
        motivos["nao_jogado"] += 1
        return None, None, status

    if status not in STATUS_FINAL:
        motivos[f"status_desconhecido:{status or 'vazio'}"] += 1
        return None, None, status

    home_s, away_s = _extrair_golos(data)

    if home_s is None or away_s is None:
        motivos["golos_nao_encontrados"] += 1
        print(f"AVISO {match_id}: status final '{status}' mas sem golos. keys={sorted(data.keys())}")
        return None, None, status

    return home_s, away_s, status


def resolver_jogos():
    if not API_TOKEN:
        print("ERRO: BSD_API_TOKEN nao definido.")
        sys.exit(1)

    init_db()
    pendentes = obter_jogos_pendentes_passados()

    if not pendentes:
        print("Nenhum jogo pendente elegivel para liquidacao.")
        return

    print(f"A verificar {len(pendentes)} jogos pendentes...")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    motivos = Counter()
    resolvidos = 0
    marcados_void = 0
    verificados = 0
    interrompido_por_rate_limit = False

    for match_id, home, away, _ts in pendentes:
        try:
            home_score, away_score, status = consultar_resultado_api(match_id, motivos)
        except RateLimitPersistente:
            interrompido_por_rate_limit = True
            print(
                f"\nAVISO: HTTP 429 persistente em {match_id} apos "
                f"{len(RATE_LIMIT_BACKOFF)} tentativas de backoff. "
                "A interromper o ciclo para nao continuar a queimar chamadas."
            )
            break

        verificados += 1

        if home_score is not None and away_score is not None:
            res_o25 = 1 if (home_score + away_score) > 2.5 else 0
            res_btts = 1 if (home_score > 0 and away_score > 0) else 0

            cursor.execute(
                """
                UPDATE predictions
                SET home_score = ?, away_score = ?,
                    result_o25 = ?, result_btts = ?,
                    status = 'finished'
                WHERE match_id = ?
                """,
                (home_score, away_score, res_o25, res_btts, str(match_id)),
            )

            resolvidos += 1
            print(
                f"OK {home} {home_score}-{away_score} {away} "
                f"| O2.5={res_o25} BTTS={res_btts}"
            )

        elif status in STATUS_NAO_JOGADO:
            # Nao deixar acumular pendentes eternos que nunca vao liquidar
            cursor.execute(
                "UPDATE predictions SET status = 'void' WHERE match_id = ?",
                (str(match_id),),
            )
            marcados_void += 1

    conn.commit()
    conn.close()

    print(f"\nConcluido: {resolvidos} liquidados, {marcados_void} anulados, "
          f"de {verificados} verificados (de {len(pendentes)} elegiveis).")

    if interrompido_por_rate_limit:
        por_verificar = len(pendentes) - verificados
        print(f"AVISO: ciclo interrompido por rate limit persistente. "
              f"{por_verificar} jogos ficaram por verificar nesta corrida.")

    if motivos:
        print("Motivos de nao-liquidacao:")
        for motivo, n in motivos.most_common():
            print(f"  {motivo}: {n}")

    # Invariante: se nada liquidou e nada foi anulado, o pipeline esta partido.
    # Mantem-se mesmo que a causa tenha sido rate limit persistente: se ja
    # houve liquidacoes ou anulacoes antes da interrupcao, este bloco nao
    # dispara e o script sai 0 normalmente.
    if resolvidos == 0 and marcados_void == 0:
        print("\nERRO: 0 jogos liquidados em {} tentativas. "
              "Correr com SETTLEMENT_DEBUG=1 para ver o payload real."
              .format(len(pendentes)))
        sys.exit(1)


if __name__ == "__main__":
    resolver_jogos()
