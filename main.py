import os
import sys
import time
import sqlite3
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

MEDIA_GOLOS_DEFAULT = 2.6
LIGA_MIN_JOGOS = 15
LIGA_MEDIA_MIN = 1.8
LIGA_MEDIA_MAX = 3.6
K_ENCOLHIMENTO = 3

TEAM_STATS_DELAY = float(os.getenv("TEAM_STATS_DELAY", "0.5"))
TEAM_STATS_MAX_CALLS = int(os.getenv("TEAM_STATS_MAX_CALLS", "120"))
TEAM_STATS_JANELA_HORAS = 36


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

from database import init_db, salvar_previsoes_db, DB_NAME, carregar_team_stats
from adjusted_xg import calcular_adjusted_xg

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

def extrair_team_id(match, campo):
    equipa = match.get(campo)
    if isinstance(equipa, dict):
        tid = equipa.get('id') or equipa.get('team_id')
        if tid is not None:
            return tid

    alt_key = f"{campo}_id"
    return match.get(alt_key)

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

def calcular_medias_liga():
    """
    Le os jogos ja liquidados na predictions.db e devolve:
    - medias_por_liga: dict liga -> media de golos totais (home+away),
      so para ligas com >= LIGA_MIN_JOGOS liquidados, limitada a
      [LIGA_MEDIA_MIN, LIGA_MEDIA_MAX].
    - media_global: media de golos totais de todos os jogos liquidados
      (fallback para ligas com poucos jogos), ou MEDIA_GOLOS_DEFAULT se
      a base ainda nao tiver nenhum jogo liquidado.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT league, AVG(home_score + away_score), COUNT(*)
        FROM predictions
        WHERE status = 'finished' AND home_score IS NOT NULL
        GROUP BY league
    """)
    por_liga = cursor.fetchall()

    cursor.execute("""
        SELECT AVG(home_score + away_score), COUNT(*)
        FROM predictions
        WHERE status = 'finished' AND home_score IS NOT NULL
    """)
    media_global_bruta, total_global = cursor.fetchone()
    conn.close()

    medias_por_liga = {}
    for liga, media, n in por_liga:
        if n >= LIGA_MIN_JOGOS and media is not None:
            medias_por_liga[liga] = max(LIGA_MEDIA_MIN, min(LIGA_MEDIA_MAX, media))

    if not total_global:
        media_global = MEDIA_GOLOS_DEFAULT
    else:
        media_global = media_global_bruta

    return medias_por_liga, media_global


def obter_match_ids_existentes():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT match_id FROM predictions")
    ids = {row[0] for row in cursor.fetchall()}
    conn.close()
    return ids


def garantir_colunas_auditoria_h2h():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(predictions)")
    colunas = {row[1] for row in cursor.fetchall()}

    for nome, tipo in (
        ("n_h2h", "INTEGER"),
        ("media_h2h_bruta", "REAL"),
        ("media_liga_usada", "REAL"),
    ):
        if nome not in colunas:
            cursor.execute(f"ALTER TABLE predictions ADD COLUMN {nome} {tipo}")

    conn.commit()
    conn.close()


def gravar_auditoria_h2h(jogos, ids_ja_existentes):
    """
    Preenche n_h2h/media_h2h_bruta/media_liga_usada so para previsoes
    novas nesta corrida (match_id que nao existia antes do
    salvar_previsoes_db). Linhas ja existentes ficam intocadas.
    """
    novos = [j for j in jogos if j["match_id"] not in ids_ja_existentes]
    if not novos:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.executemany(
        """
        UPDATE predictions
        SET n_h2h = ?, media_h2h_bruta = ?, media_liga_usada = ?
        WHERE match_id = ?
        """,
        [
            (j["n_h2h"], j["media_h2h_bruta"], j["media_liga_usada"], j["match_id"])
            for j in novos
        ],
    )
    conn.commit()
    conn.close()


def garantir_coluna_origem_xg():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(predictions)")
    colunas = {row[1] for row in cursor.fetchall()}

    if "origem_xg" not in colunas:
        cursor.execute("ALTER TABLE predictions ADD COLUMN origem_xg TEXT")

    conn.commit()
    conn.close()


def gravar_origem_xg(jogos, ids_ja_existentes):
    """
    Preenche origem_xg ('adjusted' ou 'h2h') so para previsoes novas
    nesta corrida -- mesmo criterio e mesma forma que
    gravar_auditoria_h2h. Linhas ja existentes ficam intocadas.
    """
    novos = [j for j in jogos if j["match_id"] not in ids_ja_existentes]
    if not novos:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.executemany(
        """
        UPDATE predictions
        SET origem_xg = ?
        WHERE match_id = ?
        """,
        [(j["origem_xg"], j["match_id"]) for j in novos],
    )
    conn.commit()
    conn.close()


def garantir_coluna_baixa_confianca():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(predictions)")
    colunas = {row[1] for row in cursor.fetchall()}

    if "baixa_confianca" not in colunas:
        cursor.execute("ALTER TABLE predictions ADD COLUMN baixa_confianca INTEGER")

    conn.commit()
    conn.close()


def gravar_baixa_confianca(jogos, ids_ja_existentes):
    """
    Preenche baixa_confianca (0/1) so para previsoes novas nesta corrida
    -- mesmo criterio e mesma forma que gravar_origem_xg. Linhas ja
    existentes ficam intocadas.

    baixa_confianca = 1 quando n_h2h == 0 (nunca se defrontaram) E
    origem_xg == 'h2h' (nenhuma das equipas tinha stats em cache, o
    lambda caiu no fallback simetrico da media da liga). Sem H2H e sem
    stats, o modelo nao tem sinal nenhum sobre o nivel competitivo do
    jogo (ex.: clube profissional vs equipa universitaria em taca).
    """
    novos = [j for j in jogos if j["match_id"] not in ids_ja_existentes]
    if not novos:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.executemany(
        """
        UPDATE predictions
        SET baixa_confianca = ?
        WHERE match_id = ?
        """,
        [(j["baixa_confianca"], j["match_id"]) for j in novos],
    )
    conn.commit()
    conn.close()


def recolher_estatisticas_equipas(jogos):
    """
    Para cada equipa distinta nos jogos desta corrida, chama
    obter_estatisticas_equipa() so para preencher/renovar a cache em
    team_stats (database.py) -- o resultado nao e usado em mais nada
    aqui, so inspecionado para o resumo de cobertura no final. Nao pode
    impedir o dashboard nem o settlement: qualquer falha fica contida
    dentro desta funcao.

    obter_estatisticas_equipa() agora devolve None quando a API falhou
    (distinto de stats com games=0, que e um sucesso legitimo sem
    historico) -- nesse caso nao escreve nada em team_stats. Aqui
    contamos os tres casos em separado: dados reais / 0 jogos / falha.

    O universo de equipas fica limitado aos jogos que se realizam nas
    proximas TEAM_STATS_JANELA_HORAS (36h) -- jogos_processados cobre uma
    janela de dias para as previsoes, mas isso da ~515 equipas distintas,
    o que nunca fecha contra o tecto de chamadas reais por corrida (a
    cache de 24h expira antes de as apanhar todas). 36h da uma equipas
    ~100-150, que cabe no tecto. Isto so filtra QUEM entra na recolha de
    team_stats -- jogos_processados (previsoes/dashboard) fica intocado.

    Reutiliza o padrao de rate limit do settlement.py: pausa entre
    chamadas reais (TEAM_STATS_DELAY) e um tecto de chamadas reais por
    corrida (TEAM_STATS_MAX_CALLS) -- o retry em 429 com Retry-After
    fica em api.py (_get), reutilizado por baixo de obter_equipa_fixtures.
    Ao atingir o tecto, para o ciclo: a cache de 24h apanha o resto na
    corrida seguinte.
    """
    inicio = time.time()

    try:
        from team_stats import obter_estatisticas_equipa
        from database import team_stats_expiradas
    except Exception as e:
        print(f"⚠️ AVISO: recolha de estatisticas de equipa desativada (import falhou): {e}")
        return

    limite = datetime.now(timezone.utc) + timedelta(hours=TEAM_STATS_JANELA_HORAS)
    jogos_na_janela = [j for j in jogos if j['dt_obj'] <= limite]

    equipas = {}
    for j in jogos_na_janela:
        for tid, nome in (
            (j.get('home_team_id'), j['home']),
            (j.get('away_team_id'), j['away']),
        ):
            if tid is not None and tid not in equipas:
                equipas[tid] = nome

    if not equipas:
        print(f"ℹ️ Recolha de estatisticas de equipa: nenhuma equipa com id valido nas "
              f"proximas {TEAM_STATS_JANELA_HORAS}h.")
        return

    print(f"ℹ️ Recolha de estatisticas de equipa: {len(equipas)} equipas distintas nas "
          f"proximas {TEAM_STATS_JANELA_HORAS}h (de {len(jogos_na_janela)}/{len(jogos)} "
          f"jogos) (pausa={TEAM_STATS_DELAY}s, tecto={TEAM_STATS_MAX_CALLS} chamadas reais).")

    com_dados_reais = 0
    com_zero_jogos = 0
    falhas = 0
    em_cache = 0
    chamadas_feitas = 0
    tecto_atingido = False
    por_processar = []

    itens = list(equipas.items())
    for idx, (team_id, team_name) in enumerate(itens):
        try:
            if not team_stats_expiradas(team_id):
                em_cache += 1
                obter_estatisticas_equipa(team_id, team_name)
                continue

            if chamadas_feitas >= TEAM_STATS_MAX_CALLS:
                tecto_atingido = True
                por_processar = [nome for _, nome in itens[idx:]]
                break

            time.sleep(TEAM_STATS_DELAY)
            stats = obter_estatisticas_equipa(team_id, team_name)
            chamadas_feitas += 1

            if stats is None:
                falhas += 1
            elif stats.get('games', 0) > 0:
                com_dados_reais += 1
            else:
                com_zero_jogos += 1

        except Exception as e:
            falhas += 1
            print(f"⚠️ AVISO: falha ao obter estatisticas de '{team_name}' (id={team_id}): {e}")

    duracao = time.time() - inicio

    resumo = (
        f"📊 Estatisticas de equipa: {com_dados_reais} com dados reais, "
        f"{com_zero_jogos} com 0 jogos, {falhas} falharam, {em_cache} em cache, "
        f"{duracao:.1f}s."
    )
    if tecto_atingido:
        resumo += (
            f" Tecto de {TEAM_STATS_MAX_CALLS} chamadas reais atingido -- "
            f"{len(por_processar)} equipa(s) por processar, apanhadas na proxima "
            f"corrida via cache de 24h."
        )
    print(resumo)

    if chamadas_feitas > 0:
        problematicas = falhas + com_zero_jogos
        taxa = problematicas / chamadas_feitas
        if taxa > 0.5:
            print("=" * 70)
            print(
                f"🚨 AVISO: {problematicas}/{chamadas_feitas} chamadas reais "
                f"({taxa * 100:.0f}%) falharam ou vieram com 0 jogos. Sinal "
                f"provavel de que o filtro team_id em /events/ nao funciona "
                f"como esperado -- confirmar antes de usar team_stats para "
                f"seja o que for."
            )
            print("=" * 70)


def analisar():
    init_db()
    garantir_colunas_auditoria_h2h()
    garantir_coluna_origem_xg()
    garantir_coluna_baixa_confianca()

    try:
        matches = obter_jogos_proximos_dias()
    except ErroObtencaoJogos as e:
        print(f"❌ ERRO: {e}")
        print("A manter o dashboard e a base de dados como estao -- "
              "nao vou substituir por um vazio.")
        sys.exit(1)

    agora_utc = datetime.now(timezone.utc)

    medias_por_liga, media_global = calcular_medias_liga()
    ids_ja_existentes = obter_match_ids_existentes()

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
        home_team_id = extrair_team_id(match, 'home_team')
        away_team_id = extrair_team_id(match, 'away_team')

        data_str = dt_obj.strftime('%d/%m/%Y %H:%M')
        data_dia = dt_obj.strftime('%d/%m/%Y')
        timestamp = int(dt_obj.timestamp())

        h2h = match.get('head_to_head') or {}
        n_h2h = (h2h.get('home_wins') or 0) + (h2h.get('draws') or 0) + (h2h.get('away_wins') or 0)

        media_liga_usada = medias_por_liga.get(liga_name, media_global)

        if n_h2h > 0:
            golos_h2h = (h2h.get('home_goals') or 0) + (h2h.get('away_goals') or 0)
            media_h2h_bruta = golos_h2h / n_h2h
            media_final = (
                n_h2h * media_h2h_bruta + K_ENCOLHIMENTO * media_liga_usada
            ) / (n_h2h + K_ENCOLHIMENTO)
        else:
            media_h2h_bruta = None
            media_final = media_liga_usada

        avg_goals = media_final / 2.0

        home_stats = carregar_team_stats(home_team_id) if home_team_id is not None else None
        away_stats = carregar_team_stats(away_team_id) if away_team_id is not None else None

        if home_stats is not None and away_stats is not None:
            try:
                xg_home, xg_away = calcular_adjusted_xg(
                    home_stats, away_stats,
                    home_xg_api=avg_goals,
                    away_xg_api=avg_goals,
                )
                origem_xg = 'adjusted'
            except Exception as e:
                print(f"⚠️ AVISO: calcular_adjusted_xg falhou para {home_name} vs "
                      f"{away_name} (a manter o caminho H2H): {e}")
                xg_home = avg_goals
                xg_away = avg_goals
                origem_xg = 'h2h'
        else:
            xg_home = avg_goals
            xg_away = avg_goals
            origem_xg = 'h2h'

        p_o25, p_btts = monte_carlo_sim(xg_home, xg_away)

        baixa_confianca = 1 if (n_h2h == 0 and origem_xg == 'h2h') else 0

        match_id = str(match.get('id') or f"{home_name}_{away_name}_{timestamp}")

        jogos_processados.append({
            'id': match.get('id'),
            'match_id': match_id,
            'data_str': data_str,
            'data_dia': data_dia,
            'timestamp': timestamp,
            'dt_obj': dt_obj,
            'liga': liga_name,
            'home': home_name,
            'away': away_name,
            'home_team_id': home_team_id,
            'away_team_id': away_team_id,
            'xg_home': xg_home,
            'xg_away': xg_away,
            'origem_xg': origem_xg,
            'o25': p_o25,
            'btts': p_btts,
            'n_h2h': n_h2h,
            'media_h2h_bruta': media_h2h_bruta,
            'media_liga_usada': media_liga_usada,
            'baixa_confianca': baixa_confianca
        })

    jogos_processados.sort(key=lambda x: x['dt_obj'])

    salvar_previsoes_db(jogos_processados)
    gravar_auditoria_h2h(jogos_processados, ids_ja_existentes)
    gravar_origem_xg(jogos_processados, ids_ja_existentes)
    gravar_baixa_confianca(jogos_processados, ids_ja_existentes)

    n_adjusted = sum(1 for j in jogos_processados if j['origem_xg'] == 'adjusted')
    n_h2h_path = len(jogos_processados) - n_adjusted
    print(f"🎯 Origem do xG: {n_adjusted} jogo(s) com adjusted_xg (stats de ambas as "
          f"equipas em cache), {n_h2h_path} jogo(s) com o caminho H2H/liga (stats em "
          f"falta para uma ou ambas).")

    n_baixa_confianca = sum(1 for j in jogos_processados if j['baixa_confianca'])
    print(f"⚠️ {n_baixa_confianca} previsao(oes) marcada(s) como baixa confianca "
          f"(sem H2H e sem stats de ambas as equipas).")

    try:
        recolher_estatisticas_equipas(jogos_processados)
    except Exception as e:
        print(f"⚠️ AVISO: passo de recolha de estatisticas de equipa falhou (nao fatal): {e}")

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
        baixa_confianca = j.get('baixa_confianca', 0)

        if baixa_confianca:
            cor_o25 = "#6c757d"
            cor_btts = "#6c757d"
        else:
            cor_o25 = "#28a745" if j['o25'] >= 60 else "#212529"
            cor_btts = "#28a745" if j['btts'] >= 60 else "#212529"

        tag_baixa_confianca = (
            '<span class="tag-baixa-confianca" '
            'title="Sem histórico entre as equipas e sem estatísticas de ambas — '
            'previsão pouco fiável">⚠️ sem dados</span>'
        ) if baixa_confianca else ""

        linhas_tabela += f"""
        <tr data-liga="{j['liga']}" data-data="{j['data_dia']}" data-baixa-confianca="{baixa_confianca}">
            <td data-value="{j['timestamp']}"><b>{j['data_str']}</b></td>
            <td><span class="badge-liga">{j['liga']}</span></td>
            <td>{j['home']} vs {j['away']} {tag_baixa_confianca}</td>
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

            .tag-baixa-confianca {{
                display: inline-block; margin-left: 4px; padding: 1px 6px; border-radius: 10px;
                background: #e9ecef; color: #6c757d; font-size: 0.78em; font-weight: 600;
                border: 1px solid #ced4da; cursor: help; white-space: nowrap;
            }}

            .checkbox-container {{ display: flex; align-items: center; gap: 6px; flex: 1; min-width: 220px; font-size: 0.9em; color: #495057; }}
            .checkbox-container input {{ flex: none; width: auto; padding: 0; }}
            .checkbox-container label {{ cursor: pointer; user-select: none; }}
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
            <div class="checkbox-container">
                <input type="checkbox" id="ocultarBaixaConfiancaFilter" onchange="filtrarTabela()">
                <label for="ocultarBaixaConfiancaFilter">Ocultar previsões de baixa confiança</label>
            </div>
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
                var ocultarBaixaConfianca = document.getElementById("ocultarBaixaConfiancaFilter").checked;
                var rows = document.querySelectorAll("#matchesTable tbody tr");

                rows.forEach(function(row) {{
                    var dataRow = (row.getAttribute("data-data") || "").toLowerCase();
                    var ligaRow = (row.getAttribute("data-liga") || "").toLowerCase();
                    var textoRow = row.innerText.toLowerCase();
                    var baixaConfiancaRow = row.getAttribute("data-baixa-confianca") === "1";

                    var bateData = (dataSelec === "" || dataRow === dataSelec);
                    var bateLiga = (ligaSelec === "" || ligaRow === ligaSelec);
                    var bateBusca = (termoBusca === "" || textoRow.includes(termoBusca));
                    var bateConfianca = (!ocultarBaixaConfianca || !baixaConfiancaRow);

                    if (bateData && bateLiga && bateBusca && bateConfianca) {{
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
