import os
import numpy as np
import requests
from datetime import datetime

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

def extrair_data(match):
    # Procura o campo de data comum na BSD v2
    data_str = match.get('starting_at') or match.get('date') or match.get('datetime') or ''
    if data_str:
        try:
            # Formata ISO para Leitura Limpa (DD/MM HH:MM)
            dt = datetime.fromisoformat(data_str.replace('Z', '+00:00'))
            return dt.strftime('%d/%m %H:%M'), dt
        except Exception:
            return data_str[:16].replace('T', ' '), datetime.min
    return 'N/D', datetime.min

def analisar():
    url = f"{BASE_URL}/events/"
    print(f"A ligar ao endpoint: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            matches = data.get('results', data.get('data', data)) if isinstance(data, dict) else data

            jogos_processados = []

            for match in matches:
                home_name = extrair_nome_equipa(match, 'home_team')
                away_name = extrair_nome_equipa(match, 'away_team')
                data_formatada, dt_obj = extrair_data(match)

                stats = match.get('stats', {})
                xg_home = stats.get('home_xg') or match.get('home_xg') or match.get('home_goals_avg', 1.40)
                xg_away = stats.get('away_xg') or match.get('away_xg') or match.get('away_goals_avg', 1.20)

                p_o25, p_btts = monte_carlo_sim(xg_home, xg_away)

                jogos_processados.append({
                    'data_str': data_formatada,
                    'dt_obj': dt_obj,
                    'home': home_name,
                    'away': away_name,
                    'o25': p_o25,
                    'btts': p_btts
                })

            # Ordenar jogos cronologicamente pela data/hora
            jogos_processados.sort(key=lambda x: x['dt_obj'])

            # Gerar Dashboard HTML Simples
            gerar_dashboard_html(jogos_processados)
            print("✅ Dashboard gerado com sucesso em index.html!")
            
        else:
            print(f"❌ Erro na API: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Erro de execução: {e}")

def gerar_dashboard_html(jogos):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    linhas_tabela = ""
    for j in jogos:
        # Destacar com cor percentagens >= 60%
        cor_o25 = "#28a745" if j['o25'] >= 60 else "#333"
        cor_btts = "#28a745" if j['btts'] >= 60 else "#333"

        linhas_tabela += f"""
        <tr>
            <td><b>{j['data_str']}</b></td>
            <td>{j['home']} vs {j['away']}</td>
            <td style="color: {cor_o25}; font-weight: bold;">{j['o25']:.1f}%</td>
            <td style="color: {cor_btts}; font-weight: bold;">{j['btts']:.1f}%</td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Monte Carlo Predictions</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 15px; background: #f8f9fa; color: #212529; }}
            h2 {{ text-align: center; color: #0d6efd; }}
            p.update {{ text-align: center; font-size: 0.85em; color: #6c757d; }}
            .table-container {{ overflow-x: auto; background: white; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            table {{ width: 100%; border-collapse: collapse; text-align: left; }}
            th, td {{ padding: 12px 10px; border-bottom: 1px solid #dee2e6; font-size: 0.9em; }}
            th {{ background: #0d6efd; color: white; position: sticky; top: 0; }}
            tr:nth-child(even) {{ background: #f2f2f2; }}
        </style>
    </head>
    <body>
        <h2>⚽ Previsões Monte Carlo</h2>
        <p class="update">Última atualização: {agora}</p>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Data/Hora</th>
                        <th>Jogo</th>
                        <th>Over 2.5</th>
                        <th>BTTS</th>
                    </tr>
                </thead>
                <tbody>
                    {linhas_tabela}
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    analisar()
