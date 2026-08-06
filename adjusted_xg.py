"""
adjusted_xg.py

Calcula um xG ajustado para alimentar o modelo de Monte Carlo.

Versão 1:
- xG fornecido pela BSD
- Rating de ataque
- Rating de defesa
- Forma recente
- Fator casa

Futuras versões:
- Elo Rating
- xGA
- Odds
- Dixon-Coles
- Lesões
- Descanso
"""

HOME_ADVANTAGE = 0.15


def limitar(valor, minimo=0.20, maximo=3.50):
    """
    Impede valores absurdos de xG.
    """
    return max(minimo, min(maximo, valor))


def calcular_adjusted_xg(
    home_stats,
    away_stats,
    home_xg_api=None,
    away_xg_api=None
):
    """
    Calcula o xG ajustado para ambas as equipas.

    Parameters
    ----------
    home_stats : dict
    away_stats : dict
    home_xg_api : float | None
    away_xg_api : float | None

    Returns
    -------
    tuple(float,float)
    """

    # ---------- xG da API ----------

    home_api = float(home_xg_api) if home_xg_api is not None else home_stats["attack"]

    away_api = float(away_xg_api) if away_xg_api is not None else away_stats["attack"]

    # ---------- Ratings ----------

    home_attack = float(home_stats["attack"])
    away_attack = float(away_stats["attack"])

    home_defense = float(home_stats["defense"])
    away_defense = float(away_stats["defense"])

    home_form = float(home_stats["form"])
    away_form = float(away_stats["form"])

    # ---------- xG ajustado ----------

    home_xg = (
        home_api * 0.35
        + home_attack * 0.30
        + (2.0 - away_defense) * 0.20
        + home_form * 0.15
        + HOME_ADVANTAGE
    )

    away_xg = (
        away_api * 0.35
        + away_attack * 0.30
        + (2.0 - home_defense) * 0.20
        + away_form * 0.15
    )

    home_xg = limitar(home_xg)
    away_xg = limitar(away_xg)

    return (
        round(home_xg, 2),
        round(away_xg, 2)
    )
