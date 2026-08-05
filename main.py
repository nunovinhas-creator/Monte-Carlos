import os
import json
import requests

API_TOKEN = os.getenv("BSD_API_TOKEN")
BASE_URL = "https://sports.bzzoiro.com/api/v2"

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json"
}

def debug_api():
    url = f"{BASE_URL}/events/"
    response = requests.get(url, headers=HEADERS, timeout=15)
    
    if response.status_code == 200:
        data = response.json()
        matches = data.get('results', data.get('data', data)) if isinstance(data, dict) else data

        if matches and len(matches) > 0:
            print("=== ESTRUTURA DO PRIMEIRO JOGO (KEYS DISPONÍVEIS) ===")
            print(json.dumps(matches[0], indent=2, ensure_ascii=False))
        else:
            print("Nenhum jogo encontrado no JSON.")
    else:
        print(f"Erro na API: {response.status_code}")

if __name__ == "__main__":
    debug_api()
