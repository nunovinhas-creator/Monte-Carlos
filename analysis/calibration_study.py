"""Offline chronological calibration study. Never imports production activation."""
import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from analysis.calibration_o25 import fit, sigmoid, logit
from analysis.metrics import scores, block_interval
from analysis.roi_report import load_rows
from project_paths import ROOT, DB_PATH

PROTOCOL = ROOT / 'research/protocol_2026-11-13.json'


def temporal_split(rows, embargo_hours=48):
    days = sorted({r['timestamp']//86400 for r in rows})
    if len(days) < 10:
        raise ValueError('At least 10 UTC days required')
    validation_start = days[int(len(days)*.6)]*86400
    holdout_start = days[int(len(days)*.8)]*86400
    embargo = embargo_hours*3600
    # Outcomes must mature before the next partition, never on its first day.
    train = [r for r in rows if r['timestamp']+embargo < validation_start]
    validation = [r for r in rows if validation_start <= r['timestamp'] and r['timestamp']+embargo < holdout_start]
    holdout = [r for r in rows if r['timestamp'] >= holdout_start]
    if min(len(train),len(validation),len(holdout)) < 1:
        raise ValueError('Insufficient chronological partition after embargo')
    return train, validation, holdout


def fit_isotonic(probs, outcomes):
    """Weighted PAVA, grouping identical predictors before pooling violations."""
    grouped = defaultdict(list)
    for p, y in zip(probs, outcomes):
        grouped[float(p)].append(float(y))
    x = sorted(grouped)
    blocks = []
    for i, value in enumerate(x):
        blocks.append([i, i, sum(grouped[value]), len(grouped[value])])
        while len(blocks)>1 and blocks[-2][2]/blocks[-2][3] > blocks[-1][2]/blocks[-1][3]:
            right, left = blocks.pop(), blocks.pop()
            blocks.append([left[0],right[1],left[2]+right[2],left[3]+right[3]])
    fitted = [0.]*len(x)
    for first, last, total, count in blocks:
        for i in range(first,last+1):
            fitted[i] = total/count
    return {'x':x,'y':fitted}


def predict(model, rows):
    p = np.array([r['prob_o25']/100 for r in rows])
    if model['type'] == 'original':
        return p
    if model['type'] == 'platt':
        return np.array([sigmoid(model['a']+model['b']*logit(v)) for v in p])
    return np.interp(p,model['x'],model['y'])


def evaluate(model, rows, base):
    p = predict(model,rows)
    y = np.array([r['result_o25'] for r in rows])
    ts = [r['timestamp'] for r in rows]
    raw = np.array([r['prob_o25']/100 for r in rows])
    result = scores(p,y,base)
    result['skill_vs_half'] = scores(p,y,.5)['skill']
    result['brier_ci95'] = block_interval((p-y)**2,ts)
    pc = np.clip(p,1e-12,1-1e-12)
    result['log_loss_ci95'] = block_interval(-y*np.log(pc)-(1-y)*np.log1p(-pc),ts)
    result['paired_brier_delta_ci95'] = block_interval((p-y)**2-(raw-y)**2,ts)
    result['paired_brier_delta'] = float(np.mean((p-y)**2-(raw-y)**2))
    weekly = defaultdict(list)
    for i, r in enumerate(rows):
        weekly[datetime.fromtimestamp(r['timestamp'],timezone.utc).strftime('%G-W%V')].append(i)
    result['weekly'] = {week:scores(p[ids],y[ids],base) for week,ids in weekly.items()}
    high = [i for i,r in enumerate(rows) if r['prob_o25']>=60]
    result['original_over60_universe'] = scores(p[high],y[high],base)
    return result


def run(db_path, output_dir):
    protocol = json.loads(PROTOCOL.read_text())
    rows = load_rows(db_path)
    train, validation, holdout = temporal_split(rows,protocol['outcome_embargo_hours'])
    baseline = sum(r['result_o25'] for r in train)/len(train)
    beta = fit([(r['prob_o25'],r['result_o25']) for r in train])
    models = {'original_v2':{'type':'original'},'platt':{'type':'platt','a':beta[0],'b':beta[1]}}
    isotonic_reason = None
    outcomes = [r['result_o25'] for r in train]
    if (len(train)>=protocol['minimum_isotonic_train'] and
        len({r['prob_o25'] for r in train})>=protocol['minimum_isotonic_unique_probabilities'] and
        min(sum(outcomes),len(outcomes)-sum(outcomes))>=protocol['minimum_isotonic_each_class']):
        models['isotonic'] = {'type':'isotonic',**fit_isotonic([r['prob_o25']/100 for r in train],outcomes)}
    else:
        isotonic_reason = 'N/D: insufficient training sample under registered minimums'
    validation_scores = {name:evaluate(model,validation,baseline) for name,model in models.items()}
    # Selection uses validation only. Do not refit after selection.
    chosen = min(models,key=lambda name:validation_scores[name]['brier'])
    holdout_scores = {name:evaluate(model,holdout,baseline) for name,model in models.items()}
    def period(values):
        return {'n':len(values),'first_utc':datetime.fromtimestamp(min(r['timestamp'] for r in values),timezone.utc).isoformat(),
                'last_utc':datetime.fromtimestamp(max(r['timestamp'] for r in values),timezone.utc).isoformat(),
                'match_ids':[r['match_id'] for r in values]}
    result = {'protocol':protocol,'protocol_sha256':hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
              'db_sha256':hashlib.sha256(Path(db_path).read_bytes()).hexdigest(),
              'source':'frozen_at_first_valid_odds; v2; retrospective exploratory',
              'n_source':len(rows),'excluded_for_embargo':len(rows)-len(train)-len(validation)-len(holdout),
              'train':period(train),'validation':period(validation),'holdout':period(holdout),
              'train_rate_baseline':baseline,'selected_on_validation':chosen,
              'full_source_original_scores':evaluate({'type':'original'},rows,baseline),
              'isotonic_unavailable_reason':isotonic_reason,
              'validation_scores':validation_scores,'holdout_scores':holdout_scores,
              'models':models,'production_activation':False,
              'limitations':['Not a pristine independent holdout: aggregate historical outcomes were already inspected.',
                             'Odds-covered v2 subset differs from complete v2 history; do not compare as identical universes.',
                             'Legacy settlement time and frozen model version absent; legacy v2 label comes from predictions, explicitly inferred.',
                             '48h outcome maturity is a retrospective assumption, not proof of actual data availability.',
                             'Day bootstrap assumes exchangeable days and does not fully model league/time dependence.',
                             'Small final partition and short period cannot justify production activation.']}
    output = Path(output_dir); output.mkdir(parents=True,exist_ok=True)
    (output/'calibration_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    text = ['# Calibração Over 2,5 — estudo offline de 13/11/2026','',
            f'Protocolo `{protocol["id"]}`. **Exploratório histórico. Calibração em produção: desligada.**','',
            f'Universo: {len(rows)} probabilidades congeladas com a primeira odd válida, sem reconstrução de snapshots. Baseline: taxa do treino {baseline:.4f}; baseline adicional 0,5.',
            f'Treino {len(train)}, validação {len(validation)}, holdout final {len(holdout)}; {result["excluded_for_embargo"]} observações removidas pelo embargo de 48h. Seleção na validação: **{chosen}**. Coeficientes exclusivamente do treino, sem refit.','',
            '| Modelo | Validação Brier | Holdout Brier (IC95% por dia) | Log Loss | Skill vs treino | ECE | ΔBrier pareado IC95% |',
            '|---|---:|---|---:|---:|---:|---|']
    def ci(v):
        return 'N/D' if v is None else '['+', '.join(f'{x:.5f}' for x in v)+']'
    for name,s in holdout_scores.items():
        text.append(f'| {name} | {validation_scores[name]["brier"]:.5f} | {s["brier"]:.5f} {ci(s["brier_ci95"])} | {s["log_loss"]:.5f} | {s["skill"]:+.2%} | {s["ece"]:.5f} | {ci(s["paired_brier_delta_ci95"])} |')
    if isotonic_reason:
        text.extend(['',isotonic_reason])
    text += ['','![Curvas de fiabilidade](reliability.png)','','## Fiabilidade no holdout (mesma amostra por modelo)','',
             '| Modelo | Faixa | n | Probabilidade média | Frequência (Wilson IC95%) |','|---|---|---:|---:|---|']
    for name,s in holdout_scores.items():
        for b in s['reliability']:
            if b['n']:
                text.append(f'| {name} | {b["lo"]:.0%}–{b["hi"]:.0%} | {b["n"]} | {b["predicted"]:.1%} | {b["observed"]:.1%} {ci(b["observed_ci95"])} |')
    text += ['','## Estabilidade temporal do holdout','','| Modelo | Semana UTC | n | Brier | Log Loss |','|---|---|---:|---:|---:|']
    for name,s in holdout_scores.items():
        for week,v in s['weekly'].items():
            text.append(f'| {name} | {week} | {v["n"]} | {v["brier"]:.5f} | {v["log_loss"]:.5f} |')
    text += ['','## Limitações e parecer',''] + ['- '+v for v in result['limitations']]
    text += ['','Não ativar a calibração. Uma melhoria retrospectiva exige confirmação futura com protocolo registado antes da recolha; não demonstra vantagem económica.',
             '', 'Reprodução: `python -m analysis.calibration_study --db predictions.db --output research/results_2026-11 --plot`. Os JSON incluem IDs exatos, períodos, parâmetros, curvas, métricas e hashes.']
    (output/'CALIBRACAO_2026-11.md').write_text('\n'.join(text)+'\n')
    print(json.dumps({'source_n':len(rows),'train':len(train),'validation':len(validation),'holdout':len(holdout),'chosen':chosen,
                      'isotonic':isotonic_reason,'holdout_brier':{k:v['brier'] for k,v in holdout_scores.items()}},indent=2))
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',default=DB_PATH)
    ap.add_argument('--plot',action='store_true',help='Generate offline Matplotlib reliability figure')
    ap.add_argument('--output',default=str(ROOT/'research/results_2026-11'))
    args = ap.parse_args()
    result = run(args.db,args.output)
    if args.plot:
        from analysis.calibration_plot import plot
        plot(result,args.output)
