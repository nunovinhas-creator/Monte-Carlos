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
MAX_REDIRECIONAMENTOS = 5  # limite de saltos em cadeias de replaced_by
STALE_APOS_HORAS = 48  # 'notstarted'/'unresolved' com event_date mais antigo que isto -> stale
IDADE_AVISO_HORAS = 72  # pendente sem progresso ha mais de isto -> AVISO de saude


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

STATUS_NAO_INICIADO = {"notstarted"}

# 'unresolved': API respondeu 200 mas sem period/minuto/golos -- visto em
# jogos cujo event_date ja passou ha muito e que nunca vao ganhar dados
# (ex: 219704 Fulham-VfB Stuttgart, 587661 Marseille-Atletico Madrid).
# Tratado como STATUS_NAO_INICIADO para efeitos de "envelhece -> stale":
# recente pode ainda resolver-se, antigo nunca mais vai.
STATUS_UNRESOLVED = {"unresolved"}

# HTTP 404: resposta valida da API a dizer "este evento nao existe" (id
# removido, ou nunca chegou a existir) -- nao e uma falha de
# infraestrutura como um 500/502, por isso tem tratamento proprio,
# distinto do bloco http_* generico (ver _obter_payload). Sentinela
# interna devolvida por consultar_resultado_api como "status" para que
# resolver_jogos aplique a mesma logica de idade que ja usa para
# notstarted/unresolved.
STATUS_HTTP_404 = "__http_404__"

# Motivos de nao-liquidacao que sao benignos: o jogo simplesmente ainda nao
# aconteceu (ou ainda nao tem resultado disponivel), nao e um sinal de
# pipeline partido.
MOTIVOS_BENIGNOS = {
    "ainda_nao_comecou", "por_resolver",
    "nao_existe_na_api", "ainda_nao_propagado",
}

# Motivos de nao-liquidacao que sao sinal de erro real (rede, HTTP, parsing,
# cadeia replaced_by anomala/circular). So estes disparam o sys.exit(1)
# final. Qualquer motivo fora desta lista e fora de MOTIVOS_BENIGNOS (ex:
# status_desconhecido:* ainda nao classificado) fica registado no Counter
# mas nao interrompe o workflow sozinho.
MOTIVOS_DUROS_FIXOS = {
    "excecao_rede", "json_invalido", "golos_nao_encontrados", "id_nao_numerico",
    "cadeia_replaced_by_excedida",
}


def _motivo_e_duro(motivo):
    if motivo in MOTIVOS_BENIGNOS:
        return False
    if motivo in MOTIVOS_DUROS_FIXOS:
        return True
    return motivo.startswith("http_")


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


def _extrair_replaced_by(data):
    """
    A BSD por vezes substitui um evento por outro (ex: reagendamento com novo
    id). O payload do evento antigo traz 'replaced_by' com o id do novo.
    """
    val = data.get("replaced_by")

    if isinstance(val, dict):
        val = val.get("id") or val.get("match_id") or val.get("event_id")

    if val is None:
        return None

    return str(val)


_HTTP_404 = object()  # sentinela devolvida por _obter_payload num 404


def _obter_payload(match_id, motivos, ts=None, agora_ts=None):
    """
    Faz o pedido a API para um match_id, com pausa fixa e retry/backoff em
    429. Devolve o payload (dict, envelope ja desembrulhado), a sentinela
    _HTTP_404 num 404, ou None se falhar de outra forma -- o motivo da
    falha fica registado em `motivos`. `ts`/`agora_ts` (epoch UTC), quando
    dados, permitem distinguir um 404 recente (id pode nao ter propagado
    ainda) de um 404 antigo (evento provavelmente removido/nunca existiu).
    """
    if not str(match_id).isdigit():
        motivos["id_nao_numerico"] += 1
        if DEBUG:
            print(f"DEBUG {match_id}: id nao numerico, pedido nao foi feito.")
        return None

    url = f"{BASE_URL}/events/{match_id}/"

    time.sleep(DELAY)

    tentativa = 0
    while True:
        try:
            res = requests.get(url, headers=HEADERS, timeout=30)
        except Exception as e:
            motivos["excecao_rede"] += 1
            print(f"AVISO {match_id}: erro de rede: {e}")
            return None

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

    if res.status_code == 404:
        # Resposta valida: "este evento nao existe". Distingue-se dos
        # outros http_* (esses sao falhas de infraestrutura). A idade do
        # event_date decide se e so um id que ainda nao propagou (recente,
        # benigno, fica pending) ou um evento que nunca vai existir
        # (antigo, marcado stale em resolver_jogos).
        antigo = (
            ts is not None and agora_ts is not None
            and (agora_ts - ts) > STALE_APOS_HORAS * 3600
        )
        motivos["nao_existe_na_api" if antigo else "ainda_nao_propagado"] += 1
        if DEBUG:
            print(f"DEBUG {match_id}: HTTP 404 ({'antigo' if antigo else 'recente'}), "
                  f"body={res.text[:500]!r}")
        return _HTTP_404

    if res.status_code != 200:
        motivos[f"http_{res.status_code}"] += 1
        if DEBUG:
            print(f"DEBUG {match_id}: HTTP {res.status_code}, body={res.text[:500]!r}")
        return None

    try:
        data = res.json()
    except Exception:
        motivos["json_invalido"] += 1
        if DEBUG:
            print(f"DEBUG {match_id}: JSON invalido, body={res.text[:500]!r}")
        return None

    # A API pode devolver o evento dentro de um envelope
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if isinstance(data, dict) and isinstance(data.get("result"), dict) and "status" not in data:
        data = data["result"]

    return data


def consultar_resultado_api(match_id, motivos, redirecionamentos=None, ts=None, agora_ts=None):
    id_original = str(match_id)
    id_atual = id_original

    for _ in range(MAX_REDIRECIONAMENTOS):
        data = _obter_payload(id_atual, motivos, ts=ts, agora_ts=agora_ts)
        if data is _HTTP_404:
            return None, None, STATUS_HTTP_404
        if data is None:
            return None, None, None

        etiqueta = id_atual if id_atual == id_original else f"{id_original}->{id_atual}"
        status = _extrair_status(data)

        # DEBUG dump ANTES do desvio por replaced_by: cada payload obtido em
        # cada salto da cadeia fica registado, incluindo o evento original
        # substituido (senao o seu payload nunca era visto no log).
        if DEBUG:
            print(f"DEBUG {etiqueta}: status='{status}' keys={sorted(data.keys())}")
            print(f"DEBUG {etiqueta}: payload={json.dumps(data, ensure_ascii=False)[:1200]}")

        replaced_by = _extrair_replaced_by(data)
        if replaced_by and replaced_by != id_atual:
            print(f"INFO {etiqueta}: evento substituido (replaced_by) -> a seguir para {replaced_by}")
            if redirecionamentos is not None:
                redirecionamentos[id_original] = replaced_by
            id_atual = replaced_by
            continue

        if status in STATUS_NAO_JOGADO:
            motivos["nao_jogado"] += 1
            return None, None, status

        if status not in STATUS_FINAL:
            if status in STATUS_NAO_INICIADO:
                # Estado normal, nao e falha: o jogo ainda nao comecou
                # segundo a API (atraso de kickoff, fuso, etc.). So passa a
                # 'stale' se o event_date for muito antigo (ver resolver_jogos).
                motivos["ainda_nao_comecou"] += 1
            elif status in STATUS_UNRESOLVED:
                # Sem period/minuto/golos ainda. Recente pode resolver-se
                # mais tarde; antigo passa a 'stale' (ver resolver_jogos).
                motivos["por_resolver"] += 1
            else:
                motivos[f"status_desconhecido:{status or 'vazio'}"] += 1
            return None, None, status

        home_s, away_s = _extrair_golos(data)

        if home_s is None or away_s is None:
            motivos["golos_nao_encontrados"] += 1
            print(f"AVISO {etiqueta}: status final '{status}' mas sem golos. keys={sorted(data.keys())}")
            return None, None, status

        return home_s, away_s, status

    motivos["cadeia_replaced_by_excedida"] += 1
    print(f"AVISO {id_original}: cadeia de replaced_by excedeu {MAX_REDIRECIONAMENTOS} saltos, a desistir.")
    return None, None, None


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
    redirecionamentos = {}
    resolvidos = 0
    marcados_void = 0
    marcados_stale = 0
    verificados = 0
    pendentes_envelhecidos = 0
    interrompido_por_rate_limit = False
    agora_ts = int(datetime.now(timezone.utc).timestamp())

    for match_id, home, away, ts in pendentes:
        try:
            home_score, away_score, status = consultar_resultado_api(
                match_id, motivos, redirecionamentos, ts=ts, agora_ts=agora_ts
            )
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

        elif (
            status in STATUS_NAO_INICIADO or status in STATUS_UNRESOLVED
            or status == STATUS_HTTP_404
        ) and ts is not None and (agora_ts - ts) > STALE_APOS_HORAS * 3600:
            # 'notstarted'/'unresolved'/404 com event_date ja passado ha
            # muito nunca vai liquidar sozinho -- distinto de void (nao e
            # um adiamento real).
            cursor.execute(
                "UPDATE predictions SET status = 'stale' WHERE match_id = ?",
                (str(match_id),),
            )
            marcados_stale += 1

        elif ts is not None and (agora_ts - ts) > IDADE_AVISO_HORAS * 3600:
            # Nao liquidou, nao foi void, nao foi (ainda) marcado stale, mas
            # ja passou bastante mais tempo que o limiar de stale -- sinal
            # de saude a vigiar (ex: API a devolver 'notstarted'/'unresolved'
            # para tudo e o pipeline silenciosamente parado).
            pendentes_envelhecidos += 1

    conn.commit()
    conn.close()

    print(f"\nConcluido: {resolvidos} liquidados, {marcados_void} anulados, "
          f"{marcados_stale} marcados stale (nao iniciados ha mais de "
          f"{STALE_APOS_HORAS}h), de {verificados} verificados "
          f"(de {len(pendentes)} elegiveis).")

    if redirecionamentos:
        print(f"Redirecionamentos seguidos (replaced_by): {len(redirecionamentos)}")
        for original, destino in redirecionamentos.items():
            print(f"  {original} -> {destino}")

    if interrompido_por_rate_limit:
        por_verificar = len(pendentes) - verificados
        print(f"AVISO: ciclo interrompido por rate limit persistente. "
              f"{por_verificar} jogos ficaram por verificar nesta corrida.")

    if motivos:
        print("Motivos de nao-liquidacao:")
        for motivo, n in motivos.most_common():
            print(f"  {motivo}: {n}")

    if pendentes_envelhecidos:
        print(f"AVISO: {pendentes_envelhecidos} pendente(s) com event_date ha "
              f"mais de {IDADE_AVISO_HORAS}h que nao liquidaram nem foram "
              f"marcados stale nesta corrida. Vale a pena investigar com "
              f"SETTLEMENT_DEBUG=1.")

    # Invariante: se nada liquidou, nada foi anulado, nada foi marcado stale
    # E existe pelo menos um motivo duro (MOTIVOS_DUROS_FIXOS ou http_*), o
    # pipeline esta partido (erro de rede, HTTP, JSON, golos em falta, id
    # invalido). Motivos benignos (ainda_nao_comecou) ou ainda nao
    # classificados (ex: status_desconhecido:*) nao contam para isto -- um
    # backlog limpo, com poucos ou nenhuns jogos elegiveis para liquidar
    # agora, nao e uma falha do pipeline.
    motivos_duros = sum(n for motivo, n in motivos.items() if _motivo_e_duro(motivo))

    if resolvidos == 0 and marcados_void == 0 and marcados_stale == 0:
        if motivos_duros == 0:
            print("\nNenhuma liquidacao nesta corrida: nada elegivel para "
                  "liquidar agora (sem sinal de erro duro). "
                  "A tentar novamente na proxima corrida.")
            return

        print("\nERRO: 0 jogos liquidados em {} tentativas. "
              "Correr com SETTLEMENT_DEBUG=1 para ver o payload real."
              .format(len(pendentes)))
        sys.exit(1)


if __name__ == "__main__":
    resolver_jogos()
