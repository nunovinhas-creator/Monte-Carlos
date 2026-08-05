import os
import numpy as np
import requests
from datetime import datetime, timedelta

API_TOKEN = os.getenv("BSD_API_TOKEN")
BASE_URL = "https://sports.bzzoiro.com/api/v2"

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json"
}

LEAGUE_MAP = {
    1: "Premier League",
    3: "LaLiga",
    38: "LaLiga 2",
    39: "LaLiga",
    8: "Premier League",
    9: "Championship",
    94: "Liga Portugal",
    201: "Serie A",
    181: "Bundesliga",
    168: "Ligue 1"
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

def extrair_data_hora(match):
    event_date = match.get('event_date')
    if event_date:
        try:
            dt = datetime.fromisoformat(str(event_date).replace('Z', '+00:00'))
            return dt
        except Exception:
            return None
    return None

def analisar():
    hoje = datetime.now()
    limite = hoje + timedelta(days=7)
    
    # Passa filtros de data na API para evitar jogos de 2027
    params = {
        'from': hoje.strftime('%Y-%m-%d'),
        'to': limite.strftime('%Y-%m-%d')
    }
    
    url = f"{BASE_URL}/events/"
    print(f"A ligar ao endpoint: {url} com datas entre {params['from']} e {params['to']}")

    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            matches = data.get('results', data.get('data', data)) if isinstance(data, dict) else data

            jogos_processados = []

            for match in matches:
                dt_obj = extrair_data_hora(match)
                
                # Filtrar programaticamente caso a API ignore os parâmetros de URL
                if dt_obj and dt_obj.year > hoje.year + 1:
                    continue  # Ignores jogos em anos futuros distantes (ex: 2027+)

                home_name = match.get('home_team', 'Desconhecido')
                away_name = match.get('away_team', 'Desconhecido')
                
                league_id = match.get('league_id')
                liga_name = LEAGUE_MAP.get(league_id, f"Liga ID {league_id}") if league_id else "Outras Ligas"
                
                data_str = dt_obj.strftime('%d/%m/%Y %H:%M') if dt_obj else 'Data N/D'

                h2h = match.get('head_to_head', {})
                avg_goals = h2h.get('avg_total_goals', 2.4) / 2.0 if h2h else 1.3
                
                xg_home = match.get('home_xg', avg_goals)
                xg_away = match.get('away_xg', avg_goals)

                p_o25, p_btts = monte_carlo_sim(xg_home, xg_away)

                jogos_processados.append({
                    'data_str': data_str,
                    'dt_obj': dt_obj or datetime.max,
                    'liga': liga_name,
                    'home': home_name,
                    'away': away_name,
                    'o25': p_o25,
                    'btts': p_btts
                })

            jogos_processados.sort(key=lambda x: x['dt_obj'])

            gerar_dashboard_html(jogos_processados)
            print(f"✅ Dashboard gerado com {len(jogos_processados)} jogos recentes!")
            
        else:
            print(f"❌ Erro na API: {response.status_code}")

    except Exception as e:
        print(f"❌ Erro de execução: {e}")

def gerar_dashboard_html(jogos):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    ligas_unicas = sorted(list(set(j['liga'] for j in jogos)))
    options_ligas = '<option value="">Todas as Ligas</option>'
    for l in ligas_unicas:
        options_ligas += f'<option value="{l}">{l}</option>'

    linhas_tabela = ""
    for j in jogos:
        cor_o25 = "#28a745" if j['o25'] >= 60 else "#212529"
        cor_btts = "#28a745" if j['btts'] >= 60 else "#212529"

        linhas_tabela += f"""
        <tr data-liga="{j['liga']}">
            <td><b>{j['data_str']}</b></td>
            <td><span class="badge-liga">{j['liga']}</span></td>
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
        <title>Previsões Monte Carlo</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 10px; background: #f8f9fa; color: #212529; }}
            h2 {{ text-align: center; color: #0d6efd; margin-bottom: 5px; }}
            p.update {{ text-align: center; font-size: 0.8em; color: #6c757d; margin-top: 0; margin-bottom: 15px; }}
            
            .filter-container {{ display: flex; gap: 8px; margin-bottom: 15px; flex-wrap: wrap; }}
            .filter-container select, .filter-container input {{
                flex: 1; min-width: 140px; padding: 10px; border: 1px solid #ced4da; border-radius: 6px; font-size: 0.9em;
            }}

            .table-container {{ overflow-x: auto; background: white; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.08); }}
            table {{ width: 100%; border-collapse: collapse; text-align: left; }}
            th, td {{ padding: 10px 8px; border-bottom: 1px solid #dee2e6; font-size: 0.85em; white-space: nowrap; }}
            th {{ background: #0d6efd; color: white; position: sticky; top: 0; }}
            tr:nth-child(even) {{ background: #f9f9f9; }}
            .badge-liga {{ background: #e9ecef; color: #495057; padding: 3px 6px; border-radius: 4px; font-size: 0.8em; font-weight: 500; }}
        </style>
    </head>
    <body>
        <h2>⚽ Previsões Monte Carlo</h2>
        <p class="update">Última atualização: {agora}</p>

        <div class="filter-container">
            <select id="ligaFilter" onchange="filtrarTabela()">
                {options_ligas}
            </select>
            <input type="text" id="searchFilter" onkeyup="filtrarTabela()" placeholder="Filtrar por data, equipa...">
        </div>

        <div class="table-container">
            <table id="matchesTable">
                <thead>
                    <tr>
                        <th>Data/Hora</th>
                        <th>Liga</th>
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

        <script>
            function filtrarTabela() {{
                var ligaSelec = document.getElementById("ligaFilter").value.toLowerCase();
                var termoBusca = document.getElementById("searchFilter").value.toLowerCase();
                var rows = document.querySelectorAll("#matchesTable tbody tr");

                rows.forEach(function(row) {{
                    var ligaRow = row.getAttribute("data-liga").toLowerCase();
                    var textoRow = row.innerText.toLowerCase();

                    var bateLiga = (ligaSelec === "" || ligaRow === ligaSelec);
                    var bateBusca = (termoBusca === "" || textoRow.includes(termoBusca));

                    if (bateLiga && bateBusca) {{
                        row.style.display = "";
                    }} else {{
                        row.style.display = "none";
                    }}
                }});
            }}
        </script>
    </body>
    </html>
    """
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    analisar()
