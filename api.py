import os
import requests

API_TOKEN = os.getenv("BSD_API_TOKEN")
BASE_URL = "https://sports.bzzoiro.com/api/v2"

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json"
}


def api_get(endpoint, params=None):
    """
    Efetua uma chamada GET à BSD API.
    """

    url = f"{BASE_URL}{endpoint}"

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, dict):

            if "results" in data:
                return data["results"]

            if "data" in data:
                return data["data"]

        return data

    except Exception as e:
        print(f"❌ Erro API ({endpoint}): {e}")
        return []


# ------------------------------------------------------------------
# EVENTOS
# ------------------------------------------------------------------

def obter_eventos(params=None):
    return api_get("/events/", params)


def obter_evento(event_id):
    return api_get(f"/events/{event_id}/")


def obter_evento_stats(event_id):
    return api_get(f"/events/{event_id}/stats/")


def obter_evento_h2h(event_id):
    return api_get(f"/events/{event_id}/h2h/")


def obter_evento_odds(event_id):
    return api_get(f"/events/{event_id}/odds/")


# ------------------------------------------------------------------
# EQUIPAS
# ------------------------------------------------------------------

def obter_equipa(team_id):
    return api_get(f"/teams/{team_id}/")


def obter_equipa_stats(team_id):
    return api_get(f"/teams/{team_id}/stats/")


def obter_equipa_fixtures(team_id, limit=10):

    return api_get(
        f"/teams/{team_id}/fixtures/",
        {
            "limit": limit
        }
    )


# ------------------------------------------------------------------
# PREDIÇÕES BSD
# ------------------------------------------------------------------

def obter_prediction(event_id):
    return api_get(f"/predictions/{event_id}/")
