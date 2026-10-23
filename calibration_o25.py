"""Calibração temporal congelada do Over 2,5 na versão v2.

O ficheiro de parâmetros é gerado manualmente, testado num holdout posterior,
mas NUNCA retreinado nas GitHub Actions. Evita-se fuga de resultados futuros.
A probabilidade histórica (prob_o25) dos modelos anteriores é preservada.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

CONFIG = Path(__file__).with_name('calibracao_o25_v2.json')
DATA_V2 = '2026-10-04T13:47:00'
EPS = 1e-6


def sigmoid(value):
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    z = math.exp(value)
    return z / (1 + z)


def logit(prob):
    p = max(EPS, min(1 - EPS, float(prob)))
    return math.log(p / (1 - p))


def aplicar(prob_pct, config=None):
    """Recebe e devolve percentagem, ou devolve probabilidade original sem config."""
    if config is None:
        if not CONFIG.exists():
            return round(prob_pct, 2), None
        with CONFIG.open(encoding='utf-8') as file:
            config = json.load(file)
    if config.get('ativo') is not True or config.get('modelo') != 'v2':
        return round(prob_pct, 2), None
    a, b = float(config['a']), float(config['b'])
    if not all(math.isfinite(x) for x in (a, b)) or b <= 0:
        raise ValueError('Coeficientes da calibração inválidos')
    result = sigmoid(a + b * logit(float(prob_pct) / 100))
    return round(result * 100, 2), config['versao']


def fit(rows):
    """Logistic calibration com regularização em direção ao mapeamento identidade."""
    data = np.array(rows, dtype=float)
    probs = np.clip(data[:, 0] / 100, EPS, 1 - EPS)
    x = np.column_stack((np.ones(len(data)), np.log(probs / (1 - probs))))
    y = data[:, 1]
    beta = np.array([0., 1.])
    penalty = np.array([2., 2.])
    target = np.array([0., 1.])
    for _ in range(75):
        z = np.clip(x @ beta, -35, 35)
        p = 1 / (1 + np.exp(-z))
        w = np.maximum(p * (1 - p), 1e-9)
        h = x.T @ (w[:, None] * x) + np.diag(penalty)
        g = x.T @ (y - p) - penalty * (beta - target)
        delta = np.linalg.solve(h, g)
        beta += delta
        if np.max(np.abs(delta)) < 1e-9:
            break
    if beta[1] <= 0:
        raise ValueError('Ajuste não monótono, calibração recusada')
    return beta.tolist()


def brier(rows, beta=None):
    if not rows:
        return None
    if beta is None:
        return sum((p / 100 - y) ** 2 for p, y in rows) / len(rows)
    return sum((sigmoid(beta[0] + beta[1] * logit(p / 100)) - y) ** 2
               for p, y in rows) / len(rows)


def gerar(db_path='predictions.db'):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute('''
            SELECT prob_o25, result_o25, timestamp, match_id FROM predictions
            WHERE modelo = 'v2' AND status = 'finished'
              AND result_o25 IN (0, 1) AND prob_o25 BETWEEN 1 AND 99
              AND created_at >= ?
              AND timestamp > CAST(strftime('%s', substr(created_at, 1, 19)) AS INTEGER)
              AND (calibracao_o25_id IS NULL OR calibracao_o25_id = '')
            ORDER BY timestamp, match_id
        ''', (DATA_V2,)).fetchall()
    finally:
        conn.close()
    if len(rows) < 500:
        raise RuntimeError(f'Apenas {len(rows)} previsões v2 base para fit (mínimo 500)')
    split = int(len(rows) * 0.60)
    # Nunca dividir entre dois jogos no mesmo instante.
    while split < len(rows) - 1 and rows[split - 1][2] == rows[split][2]:
        split += 1
    treino = [(r[0], r[1]) for r in rows[:split]]
    teste = [(r[0], r[1]) for r in rows[split:]]
    if len(teste) < 150:
        raise RuntimeError('Holdout temporal insuficiente')
    beta_treino = fit(treino)
    raw = brier(teste)
    cal = brier(teste, beta_treino)
    # Só ativar se o holdout mostra melhoria sem fingir significância.
    ativar = cal < raw
    beta_final = fit(treino + teste)
    return {
        'versao': 'MC-O25-PLATT-20261023',
        'modelo': 'v2',
        'gerado_em': datetime.now(timezone.utc).isoformat(),
        'ativo': ativar,
        'a': round(beta_final[0], 9), 'b': round(beta_final[1], 9),
        'n_treino': len(treino), 'n_validacao': len(teste),
        'n_ajuste_final': len(rows),
        'ultimo_evento_treino_utc': datetime.fromtimestamp(rows[-1][2], tz=timezone.utc).isoformat(),
        'ultimo_evento_treino_ts': rows[-1][2],
        'brier_original_validacao': round(raw, 8),
        'brier_recalibrado_validacao': round(cal, 8),
        'validacao_metodo': 'primeiros 60% por kickoff -> últimos 40%; coeficientes finais ajustados em todos os pre-deploy',
        'advertencia': 'Melhoria de calibração não demonstra ROI positivo. Não retreinar automaticamente.'
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.getenv('PREDICTIONS_DB', 'predictions.db'))
    ap.add_argument('--write', action='store_true', help='Congelar parâmetros após avaliação')
    args = ap.parse_args()
    result = gerar(args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.write:
        CONFIG.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')