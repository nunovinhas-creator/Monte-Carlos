"""
adjusted_xg.py

Calcula lambdas (xG esperado) para o Monte Carlo.

Modelo (Maher / Dixon-Coles simplificado):
  lambda_home = media_por_equipa * (att_casa / media_ref) * (def_fora / media_ref) * home_adv * form
  lambda_away = media_por_equipa * (att_fora / media_ref) * (def_casa / media_ref) * form

media_ref é uma referência estável (~1.35) para os ratings absolutos de
ataque/defesa vindos de team_stats (golos/jogo), evitando inflação quando
a média da liga é baixa.

Melhorias vs versão original:
- Multiplicativo em vez de média ponderada ad-hoc
- Vantagem de casa explícita
- Shrinkage bayesiano (equipas com poucos jogos)
- Escala ao nível de golos da liga (corrige subestimação ~2.54 vs 2.91)
- Forma como multiplicador suave
"""

from __future__ import annotations

HOME_ADVANTAGE = 1.10
FORM_WEIGHT = 0.08
ATTACK_PRIOR = 1.30
DEFENSE_PRIOR = 1.30
PRIOR_GAMES = 5
# Referência estável para ratings absolutos (≈ média global golos/equipa)
MEDIA_REF = 1.35
MIN_LAMBDA = 0.25
MAX_LAMBDA = 3.80


def limitar(valor: float, minimo: float = MIN_LAMBDA, maximo: float = MAX_LAMBDA) -> float:
    return max(minimo, min(maximo, valor))


def _shrink(observed: float, n_games: int, prior: float, prior_n: int = PRIOR_GAMES) -> float:
    n = max(0, int(n_games or 0))
    return (n * observed + prior_n * prior) / (n + prior_n)


def _form_multiplier(form) -> float:
    f = max(0.0, min(1.0, float(form if form is not None else 0.5)))
    return 1.0 + FORM_WEIGHT * (f - 0.5) * 2.0


def calcular_adjusted_xg(
    home_stats: dict,
    away_stats: dict,
    home_xg_api=None,
    away_xg_api=None,
    media_liga: float = 2.70,
):
    """
    Returns (lambda_home, lambda_away).

    media_liga: média de golos totais (home+away) da liga.
    """
    media_liga = max(1.80, min(3.80, float(media_liga or 2.70)))
    media_por_equipa = media_liga / 2.0

    h_games = int(home_stats.get("games") or 0)
    a_games = int(away_stats.get("games") or 0)

    h_att = _shrink(float(home_stats.get("attack") or ATTACK_PRIOR), h_games, ATTACK_PRIOR)
    a_att = _shrink(float(away_stats.get("attack") or ATTACK_PRIOR), a_games, ATTACK_PRIOR)
    h_def = _shrink(float(home_stats.get("defense") or DEFENSE_PRIOR), h_games, DEFENSE_PRIOR)
    a_def = _shrink(float(away_stats.get("defense") or DEFENSE_PRIOR), a_games, DEFENSE_PRIOR)

    # Forças relativas a uma referência estável (não à média da liga),
    # para não inflacionar ligas de poucos golos.
    h_att_r = h_att / MEDIA_REF
    a_att_r = a_att / MEDIA_REF
    h_def_r = h_def / MEDIA_REF
    a_def_r = a_def / MEDIA_REF

    h_form = _form_multiplier(home_stats.get("form"))
    a_form = _form_multiplier(away_stats.get("form"))

    lambda_home = media_por_equipa * h_att_r * a_def_r * HOME_ADVANTAGE * h_form
    lambda_away = media_por_equipa * a_att_r * h_def_r * a_form

    # Mistura leve com sinal H2H/liga se existir
    if home_xg_api is not None:
        try:
            api_h = float(home_xg_api)
            if api_h > 0:
                lambda_home = 0.80 * lambda_home + 0.20 * api_h
        except (TypeError, ValueError):
            pass
    if away_xg_api is not None:
        try:
            api_a = float(away_xg_api)
            if api_a > 0:
                lambda_away = 0.80 * lambda_away + 0.20 * api_a
        except (TypeError, ValueError):
            pass

    # Suavizar total para perto da média da liga (âncora)
    total = lambda_home + lambda_away
    if total > 0.15:
        # Mistura 70% modelo + 30% âncora da liga → reduz viés sistemático
        alvo = media_liga
        escala = (0.70 * total + 0.30 * alvo) / total
        lambda_home *= escala
        lambda_away *= escala

    return (
        round(limitar(lambda_home), 3),
        round(limitar(lambda_away), 3),
    )
