"""
backtest.py

Relatorio de calibracao do modelo sobre jogos ja liquidados.

Invariante: nenhum estado de erro pode parecer sucesso.
Se as colunas nao existirem ou a query falhar, o script sai com codigo != 0.
Amostra vazia NAO e erro (e um estado valido no arranque), mas e sinalizada
de forma explicita no HTML.

O relatorio divide a leitura em dois blocos -- "Desde o corte" (DATA_CORTE)
e "Historico completo" -- sem nunca filtrar ou apagar dados na base.
"""

import json
import math
import os
import sqlite3
import sys
from html import escape as escape_html

DB_NAME = os.getenv("PREDICTIONS_DB", "predictions.db")

DATA_CORTE = "2026-09-15"

REQUIRED_COLS = [
    "prob_o25",
    "prob_btts",
    "status",
    "result_o25",
    "result_btts",
    "timestamp",
]

BRACKETS = [
    ("0% - 39%", 0.0, 40.0),
    ("40% - 49%", 40.0, 50.0),
    ("50% - 59%", 50.0, 60.0),
    ("60% - 69%", 60.0, 70.0),
    ("70% - 79%", 70.0, 80.0),
    ("80%+", 80.0, 101.0),
]

# Mapa codificado (id -> nome), usado apenas como fallback quando
# ligas_bsd.json nao existe ou esta corrompido -- mesma lista de main.py.
LEAGUE_MAP_FALLBACK = {
    1: "Premier League", 2: "Liga Portugal Betclic", 3: "La Liga",
    4: "Serie A", 5: "Bundesliga", 6: "Ligue 1", 7: "Champions League",
    8: "Europa League", 9: "Brasileirao Serie A", 10: "Eredivisie",
    11: "Trendyol Super Lig", 12: "Championship", 13: "Scottish Premiership",
    14: "Jupiler Pro League", 15: "Swiss Super League", 17: "Saudi Pro League",
    18: "MLS", 19: "Liga MX", 22: "Parva Liga", 23: "Superliga Romena",
    24: "Super League Grecia", 25: "Ekstraklasa", 26: "Allsvenskan",
    28: "Nigeria Premier Football League", 29: "CAF Champions League",
    32: "Copa Libertadores", 33: "Copa Sudamericana", 34: "Brasileirao Serie B",
    35: "Copa do Brasil", 36: "Liga F", 38: "Segunda Division", 39: "FA Cup",
    40: "Carabao Cup", 42: "Coppa Italia", 43: "DFB Pokal", 46: "Puchar Polski",
    47: "Tunisia Ligue 1", 49: "J1 League", 50: "K League 1",
    51: "Japao - copa/escaloes inferiores", 52: "Chinese Super League",
    54: "Eliteserien", 55: "Veikkausliiga", 56: "Suomen Cup",
    57: "USL Championship", 70: "NPL Queensland", 72: "NWSL",
    79: "Amigaveis de clubes", 80: "Categoria Primera A", 81: "Copa Colombia",
    82: "Liga 3", 83: "Conference League", 84: "Danish Superliga",
    85: "Liga Profesional Argentina", 86: "League One", 87: "League Two",
    88: "Liga Portugal 2", 89: "Ligue 2", 90: "UEFA Super Cup",
    91: "National League", 92: "Taca de Portugal", 93: "Taca da Liga",
    94: "2. Bundesliga", 95: "Campeonato de Portugal",
    96: "Austrian Bundesliga", 97: "Challenger Pro League",
}

LIGAS_BSD_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ligas_bsd.json")

LIGA_MIN_N = 50


def carregar_league_map(caminho=LIGAS_BSD_JSON):
    """Constroi o mapa id -> nome a partir de ligas_bsd.json. Se o ficheiro
    nao existir, estiver corrompido ou nao tiver ligas validas, usa o
    LEAGUE_MAP_FALLBACK codificado (mesma logica de main.py)."""
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            ligas = json.load(f)

        mapa = {}
        for liga in ligas:
            lid = liga.get("id")
            nome = liga.get("name")
            if lid is None or not nome:
                continue
            mapa[int(lid)] = str(nome).strip()

        if not mapa:
            raise ValueError("ligas_bsd.json nao contem nenhuma liga valida")

        return mapa
    except Exception:
        return LEAGUE_MAP_FALLBACK


def calculate_brackets(data, prob_index, result_index):
    """
    Agrupa previsoes por intervalo de probabilidade.

    Devolve, por bracket: total, hits, taxa real e probabilidade media
    prevista pelo modelo (para comparar previsto vs realizado).
    """
    out = {
        label: {"total": 0, "hits": 0, "soma_prob": 0.0}
        for label, _, _ in BRACKETS
    }

    for row in data:
        prob = row[prob_index]
        hit = row[result_index]

        if prob is None or hit is None:
            continue

        prob_val = float(prob)

        for label, lo, hi in BRACKETS:
            if lo <= prob_val < hi:
                out[label]["total"] += 1
                out[label]["soma_prob"] += prob_val
                if int(hit) == 1:
                    out[label]["hits"] += 1
                break

    return out


def brier_stats(pares):
    """
    Brier score a partir de uma lista de pares (prob_0_a_1, resultado_0_ou_1).
    Menor e melhor. Baseline util: prever sempre a taxa base da amostra.
    """
    if not pares:
        return None, None, 0

    n = len(pares)
    brier = sum((p - y) ** 2 for p, y in pares) / n

    base = sum(y for _, y in pares) / n
    brier_base = sum((base - y) ** 2 for _, y in pares) / n

    return brier, brier_base, n


def brier_score(data, prob_index, result_index):
    pares = [
        (float(r[prob_index]) / 100.0, int(r[result_index]))
        for r in data
        if r[prob_index] is not None and r[result_index] is not None
    ]
    return brier_stats(pares)


def build_table_rows(brackets):
    html = ""

    for label, _, _ in BRACKETS:
        stats = brackets[label]
        total = stats["total"]
        hits = stats["hits"]

        if total == 0:
            html += (
                f'<tr class="text-muted"><td><strong>{label}</strong></td>'
                f"<td>0</td><td>0</td><td>&mdash;</td><td>&mdash;</td></tr>"
            )
            continue

        rate = hits / total * 100.0
        previsto = stats["soma_prob"] / total
        desvio = rate - previsto

        # Erro padrao binomial, para nao ler ruido como sinal
        se = math.sqrt(max(rate * (100.0 - rate), 0.0) / total)

        if total < 30:
            cor = "bg-secondary"
        elif abs(desvio) <= 2 * se:
            cor = "bg-success"
        else:
            cor = "bg-warning text-dark"

        html += f"""
        <tr>
            <td><strong>{label}</strong></td>
            <td>{total}</td>
            <td>{hits}</td>
            <td>{previsto:.1f}%</td>
            <td><span class="badge {cor}">{rate:.1f}% ({desvio:+.1f})</span></td>
        </tr>
        """

    return html


def celulas_mercado(pares):
    brier, base, n = brier_stats(pares)
    if brier is None:
        vazio = '<span class="text-muted">&mdash;</span>'
        return vazio, vazio, vazio

    skill = (1 - brier / base) * 100 if base and base > 0 else 0.0
    cor = "text-success" if skill > 0 else "text-danger"

    return f"{brier:.4f}", f"{base:.4f}", f'<span class="{cor}">{skill:+.1f}%</span>'


def linha_liga(nome, n_jogos, pares_o25, pares_btts):
    brier_o25, base_o25, skill_o25 = celulas_mercado(pares_o25)
    brier_btts, base_btts, skill_btts = celulas_mercado(pares_btts)

    return f"""
    <tr>
        <td>{escape_html(nome)}</td>
        <td>{n_jogos}</td>
        <td>{brier_o25}</td>
        <td>{base_o25}</td>
        <td>{skill_o25}</td>
        <td>{brier_btts}</td>
        <td>{base_btts}</td>
        <td>{skill_btts}</td>
    </tr>
    """


def build_liga_rows(rows, league_map, min_n=LIGA_MIN_N):
    """
    Agrega por league_id (index 5 nas rows), mantendo os mercados
    Over 2.5 e BTTS separados. O n de cada liga e o numero de jogos
    liquidados (nao o numero de observacoes prob/resultado, que seria
    o dobro). Ordenado por n descendente; ligas com n abaixo de min_n
    sao somadas numa linha "Outras".
    """
    por_liga = {}

    for row in rows:
        league_id = row[5]
        grupo = por_liga.setdefault(league_id, {"n": 0, "o25": [], "btts": []})
        grupo["n"] += 1

        if row[0] is not None and row[2] is not None:
            grupo["o25"].append((float(row[0]) / 100.0, int(row[2])))
        if row[1] is not None and row[3] is not None:
            grupo["btts"].append((float(row[1]) / 100.0, int(row[3])))

    principais = []
    outras = {"n": 0, "o25": [], "btts": []}

    for league_id, grupo in por_liga.items():
        if grupo["n"] >= min_n:
            principais.append((league_id, grupo))
        else:
            outras["n"] += grupo["n"]
            outras["o25"].extend(grupo["o25"])
            outras["btts"].extend(grupo["btts"])

    principais.sort(key=lambda item: item[1]["n"], reverse=True)

    html = ""
    for league_id, grupo in principais:
        if league_id is None:
            nome = "Sem liga atribuida"
        else:
            nome = league_map.get(league_id, f"Liga ID {league_id}")
        html += linha_liga(nome, grupo["n"], grupo["o25"], grupo["btts"])

    if outras["n"] > 0:
        html += linha_liga("Outras", outras["n"], outras["o25"], outras["btts"])

    return html


def pares_comparativo(rows, idx_prob, idx_result, idx_trivial):
    """
    Extrai, em paralelo, os pares (prob, resultado) do modelo actual
    e do modelo trivial (walk-forward), so para jogos onde a
    previsao trivial existe (isto e, ja havia historico da liga).
    """
    pares_atual = []
    pares_trivial = []

    for r in rows:
        trivial = r[idx_trivial]
        prob = r[idx_prob]
        result = r[idx_result]

        if trivial is None or prob is None or result is None:
            continue

        y = int(result)
        pares_atual.append((float(prob) / 100.0, y))
        pares_trivial.append((float(trivial), y))

    return pares_atual, pares_trivial


def comparativo_global_texto(nome, pares_atual, pares_trivial):
    brier_a, base, n = brier_stats(pares_atual)

    if brier_a is None:
        return (
            f'<p class="small text-muted mb-3"><strong>{escape_html(nome)}</strong>: '
            "sem jogos com historico previo na liga para avaliar o modelo trivial.</p>"
        )

    brier_t, _, _ = brier_stats(pares_trivial)

    skill_a = (1 - brier_a / base) * 100 if base and base > 0 else 0.0
    skill_t = (1 - brier_t / base) * 100 if base and base > 0 else 0.0
    cor_a = "text-success" if skill_a > 0 else "text-danger"
    cor_t = "text-success" if skill_t > 0 else "text-danger"

    return f"""
    <p class="mb-1"><strong>{escape_html(nome)}</strong>
        <span class="text-muted">(n={n}, baseline {base:.4f})</span></p>
    <p class="small mb-1">
        Modelo actual &nbsp; Brier <strong>{brier_a:.4f}</strong> |
        <span class="{cor_a}">skill {skill_a:+.1f}%</span>
    </p>
    <p class="small mb-3">
        Trivial (taxa base da liga) &nbsp; Brier <strong>{brier_t:.4f}</strong> |
        <span class="{cor_t}">skill {skill_t:+.1f}%</span>
    </p>
    """


def formatar_brier_skill(pares, base):
    brier, _, n = brier_stats(pares)
    if brier is None or base is None:
        vazio = '<span class="text-muted">&mdash;</span>'
        return vazio, vazio

    skill = (1 - brier / base) * 100 if base > 0 else 0.0
    cor = "text-success" if skill > 0 else "text-danger"
    return f"{brier:.4f}", f'<span class="{cor}">{skill:+.1f}%</span>'


def linha_comparativo(nome, grupo):
    _, base_o25, _ = brier_stats(grupo["o25_atual"])
    brier_o25_a, skill_o25_a = formatar_brier_skill(grupo["o25_atual"], base_o25)
    brier_o25_t, skill_o25_t = formatar_brier_skill(grupo["o25_trivial"], base_o25)

    _, base_btts, _ = brier_stats(grupo["btts_atual"])
    brier_btts_a, skill_btts_a = formatar_brier_skill(grupo["btts_atual"], base_btts)
    brier_btts_t, skill_btts_t = formatar_brier_skill(grupo["btts_trivial"], base_btts)

    vazio = '<span class="text-muted">&mdash;</span>'
    base_o25_str = f"{base_o25:.4f}" if base_o25 is not None else vazio
    base_btts_str = f"{base_btts:.4f}" if base_btts is not None else vazio

    return f"""
    <tr>
        <td>{escape_html(nome)}</td>
        <td>{grupo['n']}</td>
        <td>{base_o25_str}</td>
        <td>{brier_o25_a}</td>
        <td>{skill_o25_a}</td>
        <td>{brier_o25_t}</td>
        <td>{skill_o25_t}</td>
        <td>{base_btts_str}</td>
        <td>{brier_btts_a}</td>
        <td>{skill_btts_a}</td>
        <td>{brier_btts_t}</td>
        <td>{skill_btts_t}</td>
    </tr>
    """


def build_comparativo_liga_rows(rows, league_map, min_n=LIGA_MIN_N):
    """
    Por liga: compara o modelo actual com o modelo trivial (taxa base
    da liga, walk-forward), mercados Over 2.5 e BTTS separados. Usa
    so jogos com previsao trivial disponivel (isto e, com pelo menos
    um jogo anterior na liga). N e esse subconjunto, nao o total de
    jogos liquidados na liga. So ligas com n >= min_n; as restantes
    somam-se em "Outras".
    """
    por_liga = {}

    for row in rows:
        league_id = row[5]
        trivial_o25 = row[7]
        trivial_btts = row[8]

        if trivial_o25 is None and trivial_btts is None:
            continue

        grupo = por_liga.setdefault(
            league_id,
            {"n": 0, "o25_atual": [], "o25_trivial": [], "btts_atual": [], "btts_trivial": []},
        )
        grupo["n"] += 1

        if trivial_o25 is not None and row[0] is not None and row[2] is not None:
            y = int(row[2])
            grupo["o25_atual"].append((float(row[0]) / 100.0, y))
            grupo["o25_trivial"].append((float(trivial_o25), y))

        if trivial_btts is not None and row[1] is not None and row[3] is not None:
            y = int(row[3])
            grupo["btts_atual"].append((float(row[1]) / 100.0, y))
            grupo["btts_trivial"].append((float(trivial_btts), y))

    principais = []
    outras = {"n": 0, "o25_atual": [], "o25_trivial": [], "btts_atual": [], "btts_trivial": []}

    for league_id, grupo in por_liga.items():
        if grupo["n"] >= min_n:
            principais.append((league_id, grupo))
        else:
            outras["n"] += grupo["n"]
            for chave in ("o25_atual", "o25_trivial", "btts_atual", "btts_trivial"):
                outras[chave].extend(grupo[chave])

    principais.sort(key=lambda item: item[1]["n"], reverse=True)

    html = ""
    for league_id, grupo in principais:
        if league_id is None:
            nome = "Sem liga atribuida"
        else:
            nome = league_map.get(league_id, f"Liga ID {league_id}")
        html += linha_comparativo(nome, grupo)

    if outras["n"] > 0:
        html += linha_comparativo("Outras", outras)

    return html


def carregar_dados():
    if not os.path.exists(DB_NAME):
        print(f"ERRO: base de dados '{DB_NAME}' nao encontrada.")
        sys.exit(1)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(predictions);")
    columns = [col[1] for col in cursor.fetchall()]

    if not columns:
        print("ERRO: tabela 'predictions' nao existe.")
        conn.close()
        sys.exit(1)

    em_falta = [c for c in REQUIRED_COLS if c not in columns]
    if em_falta:
        print(f"ERRO: colunas em falta no schema: {em_falta}")
        print(f"Colunas existentes: {columns}")
        conn.close()
        sys.exit(1)

    cursor.execute("SELECT COUNT(*) FROM predictions")
    total_registos = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM predictions WHERE status = 'finished'")
    total_finished = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT prob_o25, prob_btts, result_o25, result_btts, created_at, league_id, timestamp
        FROM predictions
        WHERE status = 'finished'
          AND result_o25 IS NOT NULL
          AND result_btts IS NOT NULL
        ORDER BY timestamp ASC
        """
    )
    rows = cursor.fetchall()
    conn.close()

    return rows, total_registos, total_finished


def calcular_trivial_walk_forward(rows):
    """
    Para cada jogo (rows ja ordenadas por timestamp ascendente -- ver
    carregar_dados), calcula a previsao trivial = taxa de acerto
    historica da liga (Over 2.5 e BTTS, em separado), usando SO jogos
    anteriores dessa liga. Walk-forward, sem look-ahead: o primeiro
    jogo de cada liga fica sem previsao trivial (None), porque ainda
    nao ha historico.

    Devolve uma nova lista de tuplos: cada row original com dois
    campos extra no fim -- (trivial_o25, trivial_btts) -- cada um
    None ou um float em [0, 1].
    """
    estado = {}
    saida = []

    for row in rows:
        league_id = row[5]
        s = estado.setdefault(
            league_id, {"o25_hits": 0, "o25_n": 0, "btts_hits": 0, "btts_n": 0}
        )

        trivial_o25 = s["o25_hits"] / s["o25_n"] if s["o25_n"] > 0 else None
        trivial_btts = s["btts_hits"] / s["btts_n"] if s["btts_n"] > 0 else None

        saida.append(row + (trivial_o25, trivial_btts))

        result_o25 = row[2]
        result_btts = row[3]
        if result_o25 is not None:
            s["o25_n"] += 1
            s["o25_hits"] += int(result_o25)
        if result_btts is not None:
            s["btts_n"] += 1
            s["btts_hits"] += int(result_btts)

    return saida


def bloco_brier(rows, idx_prob, idx_res):
    brier, base, n = brier_score(rows, idx_prob, idx_res)

    if brier is None:
        return '<span class="text-muted">sem amostra</span>'

    skill = (1 - brier / base) * 100 if base and base > 0 else 0.0
    cor = "text-success" if skill > 0 else "text-danger"

    return (
        f"Brier <strong>{brier:.4f}</strong> | "
        f"baseline {base:.4f} | "
        f'<span class="{cor}">skill {skill:+.1f}%</span> (n={n})'
    )


def render_bloco(titulo, rows, league_map, destaque=False):
    n = len(rows)
    titulo_classe = "text-primary" if destaque else "text-secondary"

    if n == 0:
        return f"""
        <div class="mb-5">
            <h3 class="fw-bold {titulo_classe} mb-3">
                {titulo} <span class="badge bg-secondary">n={n}</span>
            </h3>
            <div class="alert alert-secondary mb-0">Sem jogos liquidados neste periodo.</div>
        </div>
        """

    vazio = (
        '<tr><td colspan="5" class="text-center text-muted py-3">'
        "Sem jogos liquidados.</td></tr>"
    )
    liga_vazio = (
        '<tr><td colspan="8" class="text-center text-muted py-3">'
        "Sem ligas com dados suficientes.</td></tr>"
    )
    comparativo_vazio = (
        '<tr><td colspan="12" class="text-center text-muted py-3">'
        "Sem ligas com dados suficientes.</td></tr>"
    )

    over25_rows = build_table_rows(calculate_brackets(rows, 0, 2))
    btts_rows = build_table_rows(calculate_brackets(rows, 1, 3))
    brier_o25 = bloco_brier(rows, 0, 2)
    brier_btts = bloco_brier(rows, 1, 3)
    liga_rows = build_liga_rows(rows, league_map)

    pares_o25_atual, pares_o25_trivial = pares_comparativo(rows, 0, 2, 7)
    pares_btts_atual, pares_btts_trivial = pares_comparativo(rows, 1, 3, 8)
    comparativo_o25_texto = comparativo_global_texto("Over 2.5", pares_o25_atual, pares_o25_trivial)
    comparativo_btts_texto = comparativo_global_texto("BTTS", pares_btts_atual, pares_btts_trivial)
    comparativo_liga_rows = build_comparativo_liga_rows(rows, league_map)

    return f"""
    <div class="mb-5">
        <h3 class="fw-bold {titulo_classe} mb-3">
            {titulo} <span class="badge bg-secondary">n={n}</span>
        </h3>

        <div class="row g-4">
            <div class="col-md-6">
                <div class="card p-3">
                    <h5 class="card-title fw-bold text-primary mb-1">Mais de 2.5 Golos</h5>
                    <p class="small text-muted mb-3">{brier_o25}</p>
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead class="table-light">
                                <tr>
                                    <th>Intervalo</th>
                                    <th>N</th>
                                    <th>Acertos</th>
                                    <th>Previsto</th>
                                    <th>Real (desvio)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {over25_rows if over25_rows else vazio}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div class="col-md-6">
                <div class="card p-3">
                    <h5 class="card-title fw-bold text-primary mb-1">Ambas Marcam (BTTS)</h5>
                    <p class="small text-muted mb-3">{brier_btts}</p>
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead class="table-light">
                                <tr>
                                    <th>Intervalo</th>
                                    <th>N</th>
                                    <th>Acertos</th>
                                    <th>Previsto</th>
                                    <th>Real (desvio)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {btts_rows if btts_rows else vazio}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <div class="card p-3 mt-4">
            <h5 class="card-title fw-bold text-primary mb-1">Resultados por Liga</h5>
            <p class="small text-muted mb-3">
                Brier, baseline e skill por liga (league_id), com Over 2.5 e BTTS em
                colunas separadas. N e o numero de jogos liquidados na liga (nao
                duplicado por mercado). So ligas com n &ge; {LIGA_MIN_N}; as
                restantes somam-se em "Outras".
            </p>
            <div class="table-responsive">
                <table class="table table-hover align-middle">
                    <thead class="table-light">
                        <tr>
                            <th rowspan="2" class="align-middle">Liga</th>
                            <th rowspan="2" class="align-middle">N</th>
                            <th colspan="3" class="text-center">Over 2.5</th>
                            <th colspan="3" class="text-center">BTTS</th>
                        </tr>
                        <tr>
                            <th>Brier</th>
                            <th>Baseline</th>
                            <th>Skill</th>
                            <th>Brier</th>
                            <th>Baseline</th>
                            <th>Skill</th>
                        </tr>
                    </thead>
                    <tbody>
                        {liga_rows if liga_rows else liga_vazio}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="card p-3 mt-4">
            <h5 class="card-title fw-bold text-primary mb-1">Modelo Actual vs. Trivial (taxa base da liga)</h5>
            <p class="small text-muted mb-3">
                O modelo trivial ignora as equipas e preve sempre a taxa de acerto
                historica da propria liga, em walk-forward: para cada jogo usa so
                jogos anteriores dessa liga (sem olhar para o futuro). Jogos sem
                historico previo (o primeiro de cada liga) ficam de fora da
                comparacao -- por isso o n aqui pode ser menor que o das outras
                tabelas. So ligas com n &ge; {LIGA_MIN_N}; as restantes somam-se
                em "Outras".
            </p>

            <div class="row g-3 mb-2">
                <div class="col-md-6">{comparativo_o25_texto}</div>
                <div class="col-md-6">{comparativo_btts_texto}</div>
            </div>

            <div class="table-responsive">
                <table class="table table-hover align-middle">
                    <thead class="table-light">
                        <tr>
                            <th rowspan="2" class="align-middle">Liga</th>
                            <th rowspan="2" class="align-middle">N</th>
                            <th colspan="5" class="text-center">Over 2.5</th>
                            <th colspan="5" class="text-center">BTTS</th>
                        </tr>
                        <tr>
                            <th>Baseline</th>
                            <th>Brier actual</th>
                            <th>Skill actual</th>
                            <th>Brier trivial</th>
                            <th>Skill trivial</th>
                            <th>Baseline</th>
                            <th>Brier actual</th>
                            <th>Skill actual</th>
                            <th>Brier trivial</th>
                            <th>Skill trivial</th>
                        </tr>
                    </thead>
                    <tbody>
                        {comparativo_liga_rows if comparativo_liga_rows else comparativo_vazio}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    """


def main():
    rows, total_registos, total_finished = carregar_dados()
    rows = calcular_trivial_walk_forward(rows)
    league_map = carregar_league_map()

    n = len(rows)

    if n == 0:
        aviso = (
            '<div class="alert alert-warning mb-4">'
            f"<strong>Amostra vazia.</strong> {total_registos} previsoes na base, "
            f"{total_finished} com status 'finished', 0 com resultado liquidado. "
            "O settlement nao esta a resolver jogos &mdash; ver logs do workflow."
            "</div>"
        )
        blocos_html = ""
    else:
        aviso = (
            '<div class="alert alert-info mb-4">'
            f"<strong>Amostra:</strong> {n} jogos liquidados "
            f"(de {total_registos} previsoes registadas)."
            "</div>"
        )

        rows_corte = [r for r in rows if r[4] is not None and r[4] >= DATA_CORTE]

        blocos_html = render_bloco(
            f"Desde o corte ({DATA_CORTE})", rows_corte, league_map, destaque=True
        ) + render_bloco("Historico completo", rows, league_map, destaque=False)

    html_content = f"""<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backtest &amp; Calibracao Monte Carlo</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ background-color: #f8f9fa; font-family: system-ui, -apple-system, sans-serif; }}
        .card {{ border-radius: 12px; border: none; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        .badge {{ font-size: 0.85rem; padding: 0.45em 0.6em; }}
        td, th {{ font-size: 0.9rem; }}
    </style>
</head>
<body class="py-4">
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h2 class="fw-bold mb-0">Relatorio de Backtest</h2>
                <p class="text-muted mb-0">Calibracao do modelo em jogos liquidados</p>
            </div>
            <a href="index.html" class="btn btn-outline-primary">Ver Previsoes</a>
        </div>

        {aviso}

        {blocos_html}

        <p class="text-muted small mt-4">
            Verde = desvio dentro de 2 erros padrao (calibrado). Amarelo = desvio
            significativo. Cinzento = amostra abaixo de 30, sem leitura possivel.
            Skill positivo significa que o modelo bate a taxa base da propria amostra.
        </p>
    </div>
</body>
</html>
"""

    with open("backtest.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"OK: backtest.html gerado. Amostra liquidada: {n} jogos.")


if __name__ == "__main__":
    main()
