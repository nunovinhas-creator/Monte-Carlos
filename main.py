import os
import sys
import time
import numpy as np
import requests
from datetime import datetime, timezone, timedelta

API_TOKEN = os.getenv("BSD_API_TOKEN")
BASE_URL = "https://sports.bzzoiro.com/api/v2"

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json"
}

MAX_TENTATIVAS_API = 2
ESPERA_RETRY_SEGUNDOS = 5


class ErroObtencaoJogos(Exception):
    """Falha ao obter jogos da API (timeout, HTTP != 200, excecao de rede) --
    distinta de uma resposta 200 legitima com 0 jogos."""

LEAGUE_MAP = {
    1: "Premier League",
    3: "LaLiga",
    8: "Premier League",
    9: "Championship",
    35: "Brasileirão Série A",
    38: "LaLiga 2",
    39: "LaLiga",
    40: "EFL Championship / Cup",
    49: "J1 League",
    52: "Chinese Super League",
    70: "A-League",
    79: "Tercera RFEF",
    80: "Liga BetPlay",
    83: "UEFA Conference League",
    85: "Liga Argentina",
    94: "Liga Portugal",
    168: "Ligue 1",
    181: "Bundesliga",
    201: "Serie A"
}

from database import init_db, salvar_previsoes_db

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
    for campo in ['event_date', 'date', 'starting_at', 'match_date']:
        val = match.get(campo)
        if val:
            try:
                return datetime.fromisoformat(str(val).replace('Z', '+00:00'))
            except Exception:
                pass
    return None

def extrair_nome_equipa(match, campo):
    equipa = match.get(campo)
    if isinstance(equipa, dict):
        return equipa.get('name') or equipa.get('team_name') or 'Desconhecido'
    if isinstance(equipa, str) and equipa.strip():
        return equipa.strip()
    
    alt_key = f"{campo}_name"
    if match.get(alt_key):
        return str(match.get(alt_key)).strip()
        
    return 'Desconhecido'

def extrair_nome_liga(match):
    # Procura em dicionários aninhados
    for key in ['league', 'competition', 'tournament', 'category']:
        obj = match.get(key)
        if isinstance(obj, dict):
            nome = obj.get('name') or obj.get('title') or obj.get('label')
            if nome and str(nome).strip():
                return str(nome).strip()
        elif isinstance(obj, str) and obj.strip():
            return obj.strip()

    # Procura em campos de texto simples
    for campo in ['league_name', 'competition_name', 'tournament_name', 'category_name', 'country_name']:
        val = match.get(campo)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    # Mapeamento via ID
    league_id = match.get('league_id') or match.get('competition_id')
    if league_id:
        return LEAGUE_MAP.get(league_id, f"Liga ID {league_id}")

    return "Outras Ligas"

def obter_jogos_proximos_dias():
    hoje = datetime.now(timezone.utc).date()
    data_inicio_str = (hoje - timedelta(days=1)).isoformat()
    data_fim_str = (hoje + timedelta(days=4)).isoformat()

    url = f"{BASE_URL}/events/"
    params = {
        "status": "upcoming",
        "date_from": data_inicio_str,
        "date_to": data_fim_str,
        "limit": 100
    }

    todos_jogos = []
    print(f"A solicitar API: {url} | Params: {params}")

    while url and len(todos_jogos) < 600:
        ultimo_erro = None
        res = None

        for tentativa in range(1, MAX_TENTATIVAS_API + 1):
            try:
                # Timeout aumentado de 15 para 45 segundos
                res = requests.get(url, headers=HEADERS, params=params, timeout=45)
            except Exception as e:
                ultimo_erro = e
                res = None
            else:
                if res.status_code == 200:
                    break
                ultimo_erro = f"HTTP {res.status_code}: {res.text[:300]}"
                res = None

            if tentativa < MAX_TENTATIVAS_API:
                print(f"⚠️ Tentativa {tentativa}/{MAX_TENTATIVAS_API} falhou ({ultimo_erro}), "
                      f"a repetir em {ESPERA_RETRY_SEGUNDOS}s...")
                time.sleep(ESPERA_RETRY_SEGUNDOS)

        if res is None:
            raise ErroObtencaoJogos(
                f"falha ao obter jogos apos {MAX_TENTATIVAS_API} tentativas: {ultimo_erro}"
            )

        params = None

        try:
            data = res.json()
        except Exception as e:
            raise ErroObtencaoJogos(f"JSON invalido na resposta: {e}") from e

        if isinstance(data, dict):
            matches = data.get('results', data.get('data', []))
            url = data.get('next')
        elif isinstance(data, list):
            matches = data
            url = None
        else:
            break

        todos_jogos.extend(matches)
        if not matches or not url:
            break

    print(f"✅ {len(todos_jogos)} jogos obtidos no total (com paginação).")
    return todos_jogos

def analisar():
    init_db()

    try:
        matches = obter_jogos_proximos_dias()
    except ErroObtencaoJogos as e:
        print(f"❌ ERRO: {e}")
        print("A manter o dashboard e a base de dados como estao -- "
              "nao vou substituir por um vazio.")
        sys.exit(1)

    agora_utc = datetime.now(timezone.utc)

    jogos_processados = []

    for match in matches:
        dt_obj = extrair_data_hora(match)
        if not dt_obj:
            dt_obj = agora_utc

        if dt_obj < (agora_utc - timedelta(hours=2)):
            continue

        home_name = extrair_nome_equipa(match, 'home_team')
        away_name = extrair_nome_equipa(match, 'away_team')
        liga_name = extrair_nome_liga(match)
        
        data_str = dt_obj.strftime('%d/%m/%Y %H:%M')
        data_dia = dt_obj.strftime('%d/%m/%Y')
        timestamp = int(dt_obj.timestamp())

        h2h = match.get('head_to_head') or {}
        n_h2h = (h2h.get('home_wins') or 0) + (h2h.get('draws') or 0) + (h2h.get('away_wins') or 0)

        if n_h2h > 0:
            golos_h2h = (h2h.get('home_goals') or 0) + (h2h.get('away_goals') or 0)
            media_h2h = golos_h2h / n_h2h
        else:
            media_h2h = 2.6

        avg_goals = media_h2h / 2.0

        xg_home = avg_goals
        xg_away = avg_goals

        p_o25, p_btts = monte_carlo_sim(xg_home, xg_away)

        jogos_processados.append({
            'id': match.get('id'),
            'data_str': data_str,
            'data_dia': data_dia,
            'timestamp': timestamp,
            'dt_obj': dt_obj,
            'liga': liga_name,
            'home': home_name,
            'away': away_name,
            'xg_home': xg_home,
            'xg_away': xg_away,
            'o25': p_o25,
            'btts': p_btts
        })

    jogos_processados.sort(key=lambda x: x['dt_obj'])

    salvar_previsoes_db(jogos_processados)
    gerar_dashboard_html(jogos_processados)
    print(f"✅ Dashboard gerado com {len(jogos_processados)} jogos próximos!")

def gerar_dashboard_html(jogos):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    datas_unicas = sorted(list(set(j['data_dia'] for j in jogos)), key=lambda d: datetime.strptime(d, '%d/%m/%Y'))
    options_datas = '<option value="">Todas as Datas</option>'
    for d in datas_unicas:
        options_datas += f'<option value="{d}">{d}</option>'

    ligas_unicas = sorted(list(set(j['liga'] for j in jogos)))
    options_ligas = '<option value="">Todas as Ligas</option>'
    for l in ligas_unicas:
        options_ligas += f'<option value="{l}">{l}</option>'

    linhas_tabela = ""
    for j in jogos:
        cor_o25 = "#28a745" if j['o25'] >= 60 else "#212529"
        cor_btts = "#28a745" if j['btts'] >= 60 else "#212529"

        linhas_tabela += f"""
        <tr data-liga="{j['liga']}" data-data="{j['data_dia']}">
            <td data-value="{j['timestamp']}"><b>{j['data_str']}</b></td>
            <td><span class="badge-liga">{j['liga']}</span></td>
            <td>{j['home']} vs {j['away']}</td>
            <td data-value="{j['o25']}" style="color: {cor_o25}; font-weight: bold;">{j['o25']:.1f}%</td>
            <td data-value="{j['btts']}" style="color: {cor_btts}; font-weight: bold;">{j['btts']:.1f}%</td>
        </tr>
        """

    if not linhas_tabela:
        linhas_tabela = '<tr><td colspan="5" style="text-align:center; padding: 20px;">Nenhum jogo agendado para os próximos dias.</td></tr>'

    html = f"""
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Previsões Monte Carlo</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 10px; background: #f8f9fa; color: #212529; }}
            
            .header-container {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; flex-wrap: wrap; gap: 10px; }}
            .header-title {{ text-align: left; }}
            h2 {{ color: #0d6efd; margin: 0 0 4px 0; }}
            p.update {{ font-size: 0.8em; color: #6c757d; margin: 0; }}
            
            .btn-backtest {{ background: #0d6efd; color: white; text-decoration: none; padding: 8px 14px; border-radius: 6px; font-size: 0.85em; font-weight: bold; display: inline-block; transition: background 0.2s; }}
            .btn-backtest:hover {{ background: #0b5ed7; }}

            .filter-container {{ display: flex; gap: 8px; margin-bottom: 15px; flex-wrap: wrap; }}
            .filter-container select, .filter-container input {{
                flex: 1; min-width: 130px; padding: 10px; border: 1px solid #ced4da; border-radius: 6px; font-size: 0.9em;
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
        <div class="header-container">
            <div class="header-title">
                <h2>⚽ Previsões Monte Carlo</h2>
                <p class="update">Última atualização: {agora}</p>
            </div>
            <div>
                <a href="backtest.html" class="btn-backtest">📊 Ver Backtest & Desempenho</a>
            </div>
        </div>

        <div class="filter-container">
            <select id="dataFilter" onchange="filtrarTabela()">
                {options_datas}
            </select>
            <select id="ligaFilter" onchange="filtrarTabela()">
                {options_ligas}
            </select>
            <input type="text" id="searchFilter" onkeyup="filtrarTabela()" placeholder="Filtrar equipa...">
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
                var dataSelec = document.getElementById("dataFilter").value.toLowerCase();
                var ligaSelec = document.getElementById("ligaFilter").value.toLowerCase();
                var termoBusca = document.getElementById("searchFilter").value.toLowerCase();
                var rows = document.querySelectorAll("#matchesTable tbody tr");

                rows.forEach(function(row) {{
                    var dataRow = (row.getAttribute("data-data") || "").toLowerCase();
                    var ligaRow = (row.getAttribute("data-liga") || "").toLowerCase();
                    var textoRow = row.innerText.toLowerCase();

                    var bateData = (dataSelec === "" || dataRow === dataSelec);
                    var bateLiga = (ligaSelec === "" || ligaRow === ligaSelec);
                    var bateBusca = (termoBusca === "" || textoRow.includes(termoBusca));

                    if (bateData && bateLiga && bateBusca) {{
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
