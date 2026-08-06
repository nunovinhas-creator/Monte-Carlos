from api import obter_equipa_fixtures


def calcular_estatisticas_equipa(team_id):

    jogos = obter_equipa_fixtures(team_id, limit=10)

    if not jogos:
        return stats_default()

    golos_marcados = 0
    golos_sofridos = 0

    over25 = 0
    btts = 0

    pontos = 0

    jogos_validos = 0

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

        golos_marcados += marcados
        golos_sofridos += sofridos

        if marcados > 0 and sofridos > 0:
            btts += 1

        if marcados + sofridos >= 3:
            over25 += 1

    if jogos_validos == 0:
        return stats_default()

    media_marcados = golos_marcados / jogos_validos
    media_sofridos = golos_sofridos / jogos_validos

    attack = media_marcados
    defense = media_sofridos

    forma = pontos / (jogos_validos * 3)

    return {

        "games": jogos_validos,

        "goals_for": round(media_marcados, 2),

        "goals_against": round(media_sofridos, 2),

        "attack": round(attack, 2),

        "defense": round(defense, 2),

        "over25": round(over25 * 100 / jogos_validos, 1),

        "btts": round(btts * 100 / jogos_validos, 1),

        "form": round(forma, 2)

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

        "form": 0.50

              }
