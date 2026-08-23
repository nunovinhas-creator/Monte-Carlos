import os
import time
import requests

BASE_URL = "https://sports.bzzoiro.com/api/v2"

API_TOKEN = os.getenv("BSD_API_TOKEN")

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json"
}

CACHE = {}
CACHE_TIME = 300

RATE_LIMIT_BACKOFF = [2, 4, 8]  # segundos, por tentativa de retry apos 429


def _get(endpoint, params=None):

    key = endpoint + str(params)

    now = time.time()

    if key in CACHE:
        if now - CACHE[key]["time"] < CACHE_TIME:
            return CACHE[key]["data"]

    tentativa = 0

    while True:
        try:

            r = requests.get(
                BASE_URL + endpoint,
                headers=HEADERS,
                params=params,
                timeout=15
            )

            if r.status_code == 429 and tentativa < len(RATE_LIMIT_BACKOFF):
                retry_after = r.headers.get("Retry-After")
                try:
                    espera = float(retry_after) if retry_after is not None else RATE_LIMIT_BACKOFF[tentativa]
                except ValueError:
                    espera = RATE_LIMIT_BACKOFF[tentativa]

                print(f"Aviso BSD 429: {endpoint} (tentativa {tentativa + 1}/"
                      f"{len(RATE_LIMIT_BACKOFF)}), a aguardar {espera}s...")
                time.sleep(espera)
                tentativa += 1
                continue

            if r.status_code != 200:
                print(f"Erro BSD {r.status_code}: {endpoint}")
                return None

            data = r.json()

            CACHE[key] = {
                "time": now,
                "data": data
            }

            return data

        except Exception as e:

            print("Erro API:", e)
            return None


####################################################
# Próximos jogos
####################################################

def obter_eventos(params):

    data = _get("/events/", params)

    if not data:
        return []

    return data.get("results", [])


####################################################
# Jogos de uma equipa
####################################################

def obter_equipa_fixtures(team_id, limit=10):
    """
    Devolve None se o pedido falhou (erro de rede, HTTP != 200, JSON
    invalido -- ver _get) -- distinto de [] quando o pedido teve sucesso
    mas a equipa nao tem jogos 'finished' nesse periodo.
    """

    data = _get("/events/", {
        "team_id": team_id,
        "status": "finished",
        "limit": limit
    })

    if data is None:
        return None

    return data.get("results", [])


####################################################
# Estatísticas do jogo
####################################################

def obter_stats_evento(event_id):

    data = _get(f"/events/{event_id}/stats/")

    if not data:
        return {}

    return data


####################################################
# Predição oficial BSD
####################################################

def obter_prediction(event_id):

    data = _get(f"/events/{event_id}/prediction/")

    if not data:
        return {}

    return data


####################################################
# Odds
####################################################

def obter_odds(event_id):

    data = _get(f"/events/{event_id}/odds/")

    if not data:
        return []

    return data


####################################################
# Head to Head
####################################################

def obter_h2h(event_id):

    data = _get(f"/events/{event_id}/h2h/")

    if not data:
        return {}

    return data


####################################################
# Lineups
####################################################

def obter_lineups(event_id):

    data = _get(f"/events/{event_id}/lineups/")

    if not data:
        return {}

    return data


####################################################
# Incidents
####################################################

def obter_incidents(event_id):

    data = _get(f"/events/{event_id}/incidents/")

    if not data:
        return {}

    return data
