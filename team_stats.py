from api import obter_equipa_fixtures
from database import (
    carregar_team_stats,
    guardar_team_stats,
    team_stats_expiradas
)

CAP_GOLOS_POR_JOGO = 4


def obter_estatisticas_equipa(team_id, team_name=""):
    """
    Devolve estatísticas da equipa usando cache SQLite. Devolve None se a
    chamada a API falhou (distinto de sucesso com 0 jogos) -- nesse caso
    nao escreve nada na cache: uma linha em falta e melhor que uma linha
    de defaults que parece dados reais.
    """

    if not team_stats_expiradas(team_id):
        stats = carregar_team_stats(team_id)
        if stats:
            return stats

    stats = calcular_estatisticas_equipa(team_id)

    if stats is None:
        return None

    guardar_team_stats(team_id, team_name, stats)

    return stats


def calcular_estatisticas_equipa(team_id):
    """
    Devolve None em caso de falha da API (obter_equipa_fixtures devolveu
    None), distinto de stats_default() quando a API respondeu mas a
    equipa nao tem jogos 'finished' validos.
    """

    jogos = obter_equipa_fixtures(team_id, limit=10)

    if jogos is None:
        return None

    if not jogos:
        return stats_default()

    golos_marcados = 0
    golos_sofridos = 0

    over25 = 0
    btts = 0
    pontos = 0
    jogos_validos = 0
    jogos_truncados = 0

    for jogo in jogos:

        home = jogo.get("home_team_id")
        away = jogo.get("away_team_id")

        hg = jogo.get("home_score")
        ag = jogo.get("away_score")

        if hg is None or ag is None:
            continue

        jogos_validos += 1

        if team_id == home:
            marcados = hg
            sofridos = ag

            if hg > ag:
                pontos += 3
            elif hg == ag:
                pontos += 1

        else:
            marcados = ag
            sofridos = hg

            if ag > hg:
                pontos += 3
            elif ag == hg:
                pontos += 1

        # Cap por jogo so para attack/defense -- um resultado tipo 8-0 na
        # Taca contra um amador nao deve dominar a media de forca da
        # equipa. btts/over25/form usam os golos reais (sao sobre o
        # resultado do jogo, nao sobre estimativa de forca ofensiva).
        marcados_capados = min(marcados, CAP_GOLOS_POR_JOGO)
        sofridos_capados = min(sofridos, CAP_GOLOS_POR_JOGO)

        if marcados_capados != marcados or sofridos_capados != sofridos:
            jogos_truncados += 1

        golos_marcados += marcados_capados
        golos_sofridos += sofridos_capados

        if marcados > 0 and sofridos > 0:
            btts += 1

        if marcados + sofridos >= 3:
            over25 += 1

    if jogos_validos == 0:
        return stats_default()

    media_marcados = golos_marcados / jogos_validos
    media_sofridos = golos_sofridos / jogos_validos

    return {
        "games": jogos_validos,
        "goals_for": round(media_marcados, 2),
        "goals_against": round(media_sofridos, 2),
        "attack": round(media_marcados, 2),
        "defense": round(media_sofridos, 2),
        "over25": round(over25 * 100 / jogos_validos, 1),
        "btts": round(btts * 100 / jogos_validos, 1),
        "form": round(pontos / (jogos_validos * 3), 2),
        "origem": "api",
        "jogos_truncados": jogos_truncados
    }


def stats_default():
    return {
        "games": 0,
        "goals_for": 1.20,
        "goals_against": 1.20,
        "attack": 1.20,
        "defense": 1.20,
        "over25": 50.0,
        "btts": 50.0,
        "form": 0.50,
        "origem": "default",
        "jogos_truncados": 0
    }
