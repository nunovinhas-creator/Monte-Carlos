"""Proper probability scores and uncertainty for explicitly selected universes."""
import math
from collections import defaultdict

import numpy as np

BINS = (0, .4, .5, .6, .7, .8, 1.0000001)


def wilson(wins, n):
    if not n:
        return None
    z = 1.959963984540054
    p = wins / n
    center = (p + z*z/(2*n)) / (1+z*z/n)
    half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [max(0, center-half), min(1, center+half)]


def reliability(probs, outcomes):
    result = []
    for lo, hi in zip(BINS[:-1], BINS[1:]):
        values = [(float(p), int(y)) for p, y in zip(probs, outcomes) if lo <= p < hi]
        n = len(values)
        result.append({'lo': lo, 'hi': min(1, hi), 'n': n,
                       'predicted': sum(p for p, _ in values)/n if n else None,
                       'observed': sum(y for _, y in values)/n if n else None,
                       'observed_ci95': wilson(sum(y for _, y in values), n)})
    return result


def scores(probs, outcomes, baseline=.5):
    p, y = np.asarray(probs, dtype=float), np.asarray(outcomes, dtype=float)
    if not len(p):
        return {'n': 0, 'brier': None, 'log_loss': None, 'skill': None, 'ece': None}
    if p.shape != y.shape or not np.all(np.isfinite(p)) or np.any((p<0)|(p>1)) or not np.all(np.isin(y, [0,1])):
        raise ValueError('Invalid probabilities/outcomes')
    clipped = np.clip(p, 1e-12, 1-1e-12)
    bs = float(np.mean((p-y)**2))
    base_bs = float(np.mean((baseline-y)**2))
    bins = reliability(p, y)
    return {'n': len(p), 'brier': bs,
            'log_loss': float(np.mean(-y*np.log(clipped)-(1-y)*np.log1p(-clipped))),
            'baseline_probability': baseline, 'baseline_brier': base_bs,
            'skill': 1-bs/base_bs if base_bs else None,
            'ece': sum(b['n']*abs(b['predicted']-b['observed']) for b in bins if b['n'])/len(p),
            'reliability': bins}


def block_interval(values, timestamps, repetitions=1000, seed=20261113):
    """Percentile bootstrap of UTC days; explicitly approximate, not a profit proof."""
    if len(values) != len(timestamps):
        raise ValueError('Unpaired bootstrap data')
    grouped = defaultdict(list)
    for value, timestamp in zip(values, timestamps):
        grouped[int(timestamp)//86400].append(float(value))
    if len(grouped) < 5:
        return None
    sums = np.array([sum(v) for v in grouped.values()])
    counts = np.array([len(v) for v in grouped.values()])
    rng = np.random.default_rng(seed)
    draws = rng.integers(0,len(sums),size=(repetitions,len(sums)))
    estimates = sums[draws].sum(axis=1)/counts[draws].sum(axis=1)
    return np.quantile(estimates,[.025,.975]).tolist()
