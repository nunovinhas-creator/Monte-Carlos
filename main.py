import os
import numpy as np
import requests

API_TOKEN = os.getenv("BSD_API_TOKEN")
BASE_URL = "https://sports.bzzoiro.com/api/v2"

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json"
}

def monte_carlo_sim(lambda_home, lambda_away, simulations=50000):
    lambda_home = max(float(lambda_home or 1.2), 0.2)
    lambda_away = max(float(lambda_away or 1.0), 0.2)

    home_goals = np.random.poisson(lambda_home, simulations)
    away_goals = np.random.poisson(lambda_away, simulations)
    total_goals = home_goals + away_goals

    prob_o25 = (np.sum(total_goals > 2.5) / simulations) * 100
    prob_btts = (np.sum((home_goals > 0) & (away_goals > 0)) / simulations) * 100

    return prob_o25, prob_btts

def extrair_nome_equipa(match, key):
    obj = match.get(key)
    if isinstance(obj, dict):
        return obj.get('name', 'Desconhecido')
    elif isinstance(obj, str):
        return obj
    return match.get(f"{key}_name", 'Desconhecido')

def analisar():
    url = f"{BASE_URL}/events/"
    print(f"A ligar ao endpoint: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            matches = data.get('results', data.get('data', data)) if isinstance(data, dict) else data

            print(f"✅ Sucesso! {len(matches)} jogos obtidos.")
            print("\n=== PREVISÕES MONTE CARLO ===")
            print("-" * 55)

            for match in matches:
                home_name = extrair_nome_equipa(match, 'home_team')
                away_name = extrair_nome_equipa(match, 'away_team')

                # Extrair xG ou médias (ajustado a múltiplos formatos)
                stats = match.get('stats', {})
                xg_home = stats.get('home_xg') or match.get('home_xg') or match.get('home_goals_avg', 1.40)
                xg_away = stats.get('away_xg') or match.get('away_xg') or match.get('away_goals_avg', 1.20)

                p_o25, p_btts = monte_carlo_sim(xg_home, xg_away)

                # Mostra todos os jogos (sem filtro rígido)
                print(f"⚽ {home_name} vs {away_name}")
                print(f"   ► Over 2.5: {p_o25:.1f}% | BTTS: {p_btts:.1f}%")
                print("-" * 55)
        else:
            print(f"❌ Erro na API: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Erro de execução: {e}")

if __name__ == "__main__":
    analisar()
