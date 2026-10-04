"""
predictor.py

Motor de simulação e calibração.

- Monte Carlo com Poisson independente + correção Dixon-Coles (rho)
  para a dependência em resultados 0-0, 1-0, 0-1, 1-1.
- Calibração Platt (logística) opcional a partir de recalibracao.json.
- Shrinkage das probabilidades extremas para a taxa base da liga
  (reduz overconfidence nos brackets 70%+).
"""

from __future__ import annotations

import json
import math
import os
from functools import lru_cache

import numpy as np

RECALIBRACAO_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "recalibracao.json"
)

# Correlação Dixon-Coles típica em futebol (~ -0.10 a -0.13)
DIXON_COLES_RHO = -0.12

EPS = 1e-6


def _logit(p: float) -> float:
    p = min(max(p, EPS), 1.0 - EPS)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


@lru_cache(maxsize=1)
def _carregar_recalibracao():
    """Carrega coeficientes Platt se existirem e estiverem marcados para uso."""
    try:
        with open(RECALIBRACAO_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Só aplicar se o ficheiro tiver coeficientes válidos.
        # 'aplicado' pode ser False enquanto se valida fora de amostra;
        # aqui permitimos aplicação controlada via env APPLY_RECAL=1
        # ou se aplicado==True.
        aplicar = data.get("aplicado") is True or os.getenv("APPLY_RECAL", "") == "1"
        if not aplicar:
            return None
        return data
    except Exception:
        return None


def _platt(prob_0_1: float, a: float, b: float) -> float:
    return _sigmoid(a + b * _logit(prob_0_1))


def _tau_dixon_coles(hg: int, ag: int, lh: float, la: float, rho: float) -> float:
    """Fator de correção Dixon-Coles para (0,0), (0,1), (1,0), (1,1)."""
    if hg == 0 and ag == 0:
        return 1.0 - lh * la * rho
    if hg == 0 and ag == 1:
        return 1.0 + lh * rho
    if hg == 1 and ag == 0:
        return 1.0 + la * rho
    if hg == 1 and ag == 1:
        return 1.0 - rho
    return 1.0


def monte_carlo(
    lambda_home: float,
    lambda_away: float,
    simulations: int = 50000,
    rho: float = DIXON_COLES_RHO,
):
    """
    Simula resultados e devolve (prob_o25_pct, prob_btts_pct).

    Usa Poisson independente e repondera as contagens 0-0/1-0/0-1/1-1
    com o fator Dixon-Coles (aproximação por importance weights nas
    simulações que caem nesses scores).
    """
    lh = max(float(lambda_home or 1.2), 0.2)
    la = max(float(lambda_away or 1.0), 0.2)
    n = max(int(simulations), 1000)

    home_goals = np.random.poisson(lh, n)
    away_goals = np.random.poisson(la, n)

    # Pesos Dixon-Coles
    weights = np.ones(n, dtype=np.float64)
    if abs(rho) > 1e-9:
        mask_00 = (home_goals == 0) & (away_goals == 0)
        mask_01 = (home_goals == 0) & (away_goals == 1)
        mask_10 = (home_goals == 1) & (away_goals == 0)
        mask_11 = (home_goals == 1) & (away_goals == 1)
        weights[mask_00] = max(1.0 - lh * la * rho, 0.05)
        weights[mask_01] = max(1.0 + lh * rho, 0.05)
        weights[mask_10] = max(1.0 + la * rho, 0.05)
        weights[mask_11] = max(1.0 - rho, 0.05)

    total_w = weights.sum()
    total_goals = home_goals + away_goals
    o25 = (weights * (total_goals > 2)).sum() / total_w
    btts = (weights * ((home_goals > 0) & (away_goals > 0))).sum() / total_w

    return o25 * 100.0, btts * 100.0


def calibrar_probabilidades(
    prob_o25_pct: float,
    prob_btts_pct: float,
    base_o25: float | None = None,
    base_btts: float | None = None,
    shrink_strength: float = 0.15,
):
    """
    1) Aplica Platt scaling se recalibracao.json estiver activo.
    2) Faz shrinkage suave das probabilidades para a taxa base da liga
       (reduz overconfidence nos extremos).

    base_* devem ser taxas 0..1 (ex.: 0.55). Se None, usa 0.52/0.52.
    shrink_strength: 0 = sem shrink, 0.15 = mistura 15% com a base.
    """
    p_o = max(1.0, min(99.0, float(prob_o25_pct))) / 100.0
    p_b = max(1.0, min(99.0, float(prob_btts_pct))) / 100.0

    rec = _carregar_recalibracao()
    if rec:
        o = rec.get("over_25") or {}
        b = rec.get("btts") or {}
        if o.get("a") is not None and o.get("b") is not None:
            p_o = _platt(p_o, float(o["a"]), float(o["b"]))
        if b.get("a") is not None and b.get("b") is not None:
            p_b = _platt(p_b, float(b["a"]), float(b["b"]))

    base_o = float(base_o25) if base_o25 is not None else 0.52
    base_b = float(base_btts) if base_btts is not None else 0.52
    base_o = max(0.30, min(0.75, base_o))
    base_b = max(0.30, min(0.75, base_b))
    s = max(0.0, min(0.50, float(shrink_strength)))

    p_o = (1.0 - s) * p_o + s * base_o
    p_b = (1.0 - s) * p_b + s * base_b

    return round(p_o * 100.0, 2), round(p_b * 100.0, 2)


def calcular_value(probabilidade_pct: float, odd: float) -> float | None:
    """Expected value: prob * odd - 1."""
    if odd is None or odd <= 1.0:
        return None
    p = float(probabilidade_pct) / 100.0
    return round(p * float(odd) - 1.0, 4)


def calcular_kelly(probabilidade_pct: float, odd: float, fracao: float = 0.25) -> float | None:
    """Kelly fracionário (default 1/4 Kelly)."""
    if odd is None or odd <= 1.0:
        return None
    p = float(probabilidade_pct) / 100.0
    b = float(odd) - 1.0
    q = 1.0 - p
    if b <= 0:
        return None
    k = (b * p - q) / b
    if k <= 0:
        return 0.0
    return round(k * fracao * 100.0, 2)  # em % da banca


def calcular_confidence(mc_prob: float, bsd_prob: float | None = None) -> str:
    """Heurística simples de confiança com base na distância ao 50%."""
    p = float(mc_prob)
    dist = abs(p - 50.0)
    if dist >= 25:
        return "alta"
    if dist >= 12:
        return "media"
    return "baixa"
