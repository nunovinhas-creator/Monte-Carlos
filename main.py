import os
import numpy as np
import requests

# Ler a chave configurada no GitHub Secrets
API_TOKEN = os.getenv("BSD_API_TOKEN")

# Base URL e Autenticação corretos da v2 da BSD Bezzoiro
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

def analisar():
    url = f"{BASE_URL}/events/"
    print(f"A ligar ao endpoint: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            # A API BSD v2 usa paginação (os jogos vêm na lista 'results')
            matches = data.get('results', data.get('data', data)) if isinstance(data, dict) else data

            print(f"✅ Sucesso! {len(matches)} jogos obtidos.")
            print("\n=== PREVISÕES MONTE CARLO (OVER 2.5 E BTTS) ===")
            print("-" * 50)

            for match in matches:
                # Mapeamento dos campos de equipa da v2
                home_team = match.get('home_team', {})
                away_team = match.get('away_team', {})
                
                home_name = home_team.get('name') if isinstance(home_team, dict) else match.get('home_name', 'Casa')
                away_name = away_team.get('name') if isinstance(away_team, dict) else match.get('away_name', 'Fora')

                # Extrair xG ou médias estatísticas do evento
                stats = match.get('stats', {})
                xg_home = stats.get('home_xg') or match.get('home_goals_avg', 1.35)
                xg_away = stats.get('away_xg') or match.get('away_goals_avg', 1.15)

                p_o25, p_btts = monte_carlo_sim(xg_home, xg_away)

                if p_o25 >= 60.0 or p_btts >= 60.0:
                    print(f"🎯 {home_name} vs {away_name}")
                    print(f"   ► Over 2.5: {p_o25:.1f}%")
                    print(f"   ► BTTS: {p_btts:.1f}%")
                    print("-" * 50)
        else:
            print(f"❌ Erro na API: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Erro de execução: {e}")

if __name__ == "__main__":
    analisar()
