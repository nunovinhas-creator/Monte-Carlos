import os
import numpy as np
import requests
from datetime import datetime, timezone, timedelta

API_TOKEN = os.getenv("BSD_API_TOKEN")
BASE_URL = "https://sports.bzzoiro.com/api/v2"

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json"
}

# Dicionário de fallback alargado caso a API só envie o ID numérico
LEAGUE_MAP = {
    1: "Premier League",
    3: "LaLiga",
    8: "Premier League",
    9: "Championship",
    38: "LaLiga 2",
    39: "LaLiga",
    40: "EFL Championship / Cup",
    79: "Tercera RFEF",
    80: "Liga BetPlay",
    83: "UEFA Conference League",
    85: "Liga Argentina",
    94: "Liga Portugal",
    168: "Ligue 1",
    181: "Bundesliga",
    201: "Serie A"
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
            return datetime.fromisoformat(str(event_date).replace('Z', '+00:00'))
        except Exception:
            return None
    return None

def extrair_nome_liga(match):
    """Extrai dinamicamente o nome da liga enviado pela API ou recorre ao LEAGUE_MAP."""
    league_info = match.get('league')
    if isinstance(league_info, dict):
        nome = league_info.get('name')
        if nome:
            return nome
    elif isinstance(league_info, str) and league_info.strip():
        return league_info.strip()

    for campo in ['league_name', 'competition_name', 'competition', 'tournament_name']:
        val = match.get(campo)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    league_id = match.get('league_id')
    if league_id:
        return LEAGUE_MAP.get(league_id, f"Liga ID {league_id}")

    return "Outras Ligas"

def obter_jogos_proximos_dias():
    agora_utc = datetime.now(timezone.utc)
    hoje_str = agora_utc.strftime('%Y-%m-%d')
    limite_str = (agora_utc + timedelta(days=3)).strftime('%Y-%m-%d')

    params = {
        "status": "upcoming",
        "date_from": hoje_str,
        "date_to": limite_str,
        "limit": 200
    }

    url = f"{BASE_URL}/events/"
    print(f"A solicitar API: {url} | Params: {params}")

    try:
        res = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if res.status_code == 200:
            data = res.json()
            matches = data.get('results', data.get('data', data)) if isinstance(data, dict) else data
            print(f"✅ {len(matches)} jogos obtidos diretamente da API para a janela {hoje_str} a {limite_str}.")
            return matches
        else:
            print(f"❌ Erro HTTP {res.status_code}: {res.text}")
    except Exception as e:
        print(f"❌ Erro na ligação: {e}")

    params_fallback = {"status": "upcoming", "limit": 200}
    try:
        res = requests.get(url, headers=HEADERS, params=params_fallback, timeout=15)
        if res.status_code == 200:
            data = res.json()
            return data.get('results', data.get('data', data)) if isinstance(data, dict) else data
    except Exception as e:
        print(f"❌ Erro no fallback: {e}")

    return []

def analisar():
    matches = obter_jogos_proximos_dias()
    
    agora_utc = datetime.now(timezone.utc)
    limite_3_dias = agora_utc + timedelta(days=3, hours=12)

    jogos_processados = []

    for match in matches:
        dt_obj = extrair_data_hora(match)
        if not dt_obj:
            continue

        if dt_obj < (agora_utc - timedelta(hours=3)) or dt_obj > limite_3_dias:
            continue

        home_name = match.get('home_team', 'Desconhecido')
        away_name = match.get('away_team', 'Desconhecido')
        liga_name = extrair_nome_liga(match)
        
        data_str = dt_obj.strftime('%d/%m/%Y %H:%M')
        timestamp = int(dt_obj.timestamp())

        h2h = match.get('head_to_head', {})
        avg_goals = h2h.get('avg_total_goals', 2.4) / 2.0 if h2h else 1.3
        
        xg_home = match.get('home_xg', avg_goals)
        xg_away = match.get('away_xg', avg_goals)

        p_o25, p_btts = monte_carlo_sim(xg_home, xg_away)

        jogos_processados.append({
            'data_str': data_str,
            'timestamp': timestamp,
            'dt_obj': dt_obj,
            'liga': liga_name,
            'home': home_name,
            'away': away_name,
            'o25': p_o25,
            'btts': p_btts
        })

    jogos_processados.sort(key=lambda x: x['dt_obj'])

    gerar_dashboard_html(jogos_processados)
    print(f"✅ Dashboard gerado com {len(jogos_processados)} jogos próximos!")

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
            <td data-value="{j['timestamp']}"><b>{j['data_str']}</b></td>
            <td><span class="badge-liga">{j['liga']}</span></td>
            <td>{j['home']} vs {j['away']}</td>
            <td data-value="{j['o25']}" style="color: {cor_o25}; font-weight: bold;">{j['o25']:.1f}%</td>
            <td data-value="{j['btts']}" style="color: {cor_btts}; font-weight: bold;">{j['btts']:.1f}%</td>
        </tr>
        """

    if not linhas_tabela:
        linhas_tabela = '<tr><td colspan="5" style="text-align:center; padding: 20px;">Nenhum jogo agendado para os próximos 3 dias.</td></tr>'

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
            
            th.sortable {{ cursor: pointer; user-select: none; }}
            th.sortable:hover {{ background: #0b5ed7; }}
            th.sortable::after {{ content: ' ⇅'; opacity: 0.5; font-size: 0.9em; }}
            th.sort-desc::after {{ content: ' ↓'; opacity: 1; }}
            th.sort-asc::after {{ content: ' ↑'; opacity: 1; }}

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
                        <th class="sortable" onclick="ordenarTabela(0)">Data/Hora</th>
                        <th>Liga</th>
                        <th>Jogo</th>
                        <th class="sortable" onclick="ordenarTabela(3)">Over 2.5</th>
                        <th class="sortable" onclick="ordenarTabela(4)">BTTS</th>
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
                    var ligaRow = row.getAttribute("data-liga");
                    if (!ligaRow) return;
                    ligaRow = ligaRow.toLowerCase();
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

            let sortDirection = {{}};

            function ordenarTabela(colIndex) {{
                const table = document.getElementById("matchesTable");
                const tbody = table.querySelector("tbody");
                const rows = Array.from(tbody.querySelectorAll("tr"));
                const headers = table.querySelectorAll("th.sortable");
                
                if (rows.length <= 1 && rows[0].cells.length === 1) return;

                // Define direção inicial: Data (col 0) começa Ascendente (mais próximo primeiro)
                // Over 2.5 e BTTS (col 3 e 4) começam Descendentes (maior % primeiro)
                if (sortDirection[colIndex] === undefined) {{
                    sortDirection[colIndex] = (colIndex === 0) ? 'asc' : 'desc';
                }} else {{
                    sortDirection[colIndex] = (sortDirection[colIndex] === 'asc') ? 'desc' : 'asc';
                }}

                const dir = sortDirection[colIndex];

                headers.forEach((th) => {{
                    th.classList.remove('sort-asc', 'sort-desc');
                    if (th.cellIndex === colIndex) {{
                        th.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
                    }}
                }});

                rows.sort((a, b) => {{
                    const cellA = parseFloat(a.cells[colIndex].getAttribute('data-value') || 0);
                    const cellB = parseFloat(b.cells[colIndex].getAttribute('data-value') || 0);

                    if (cellA < cellB) return dir === 'asc' ? -1 : 1;
                    if (cellA > cellB) return dir === 'asc' ? 1 : -1;
                    return 0;
                }});

                rows.forEach(row => tbody.appendChild(row));
            }}
        </script>
    </body>
    </html>
    """
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    analisar()
