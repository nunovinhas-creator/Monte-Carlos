"""Offline-only, chronologically separated Over 2.5 Platt candidate.

No imports from production entrypoints, no writes to SQLite/production config.
Training uses historical v2 legacy outcomes strictly before 2026-10-21 UTC;
evaluation uses frozen-odds CSV from 2026-10-23 onward. Legacy outcome
availability is assumed after 48h, not proven (settled_at was absent).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from project_paths import DB_PATH, ROOT

TRAIN_CUTOFF = datetime(2026, 10, 21, tzinfo=timezone.utc)
EVALUATION_START = datetime(2026, 10, 23, 13, 30, tzinfo=timezone.utc)
MODEL_V2_START = datetime(2026, 10, 4, 13, 47, tzinfo=timezone.utc)
RIDGE = 2.0  # fixed in advance: regularize towards identity (a=0,b=1)
MIN_TRAIN = 500


def utc(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Naive timestamp not allowed')
    return result.astimezone(timezone.utc)


def sigmoid(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    expx = math.exp(x)
    return expx / (1 + expx)


def logit(p):
    p = min(1-1e-6, max(1e-6, float(p)))
    return math.log(p/(1-p))


def calibrate(p, a, b):
    if not math.isfinite(float(p)) or not 0 <= p <= 1 or b <= 0:
        raise ValueError('Invalid probability or nonmonotone calibrator')
    return sigmoid(a+b*logit(p))


def fit(train, ridge=RIDGE):
    """Two-parameter, deterministic ridge Platt via Newton iterations."""
    if len(train) < MIN_TRAIN or not 0 < sum(y for _, y in train) < len(train):
        raise ValueError('Insufficient training data')
    a, b = 0.0, 1.0
    for _ in range(80):
        g0, g1 = -ridge*a, -ridge*(b-1)
        h00, h01, h11 = ridge, 0.0, ridge
        for p, y in train:
            x=logit(p); q=sigmoid(a+b*x); w=max(1e-9,q*(1-q))
            g0 += y-q; g1 += (y-q)*x
            h00 += w; h01 += w*x; h11 += w*x*x
        det = h00*h11-h01*h01
        if det <= 0:
            raise RuntimeError('Singular Platt Hessian')
        da=(g0*h11-g1*h01)/det
        db=(g1*h00-g0*h01)/det
        a+=da; b+=db
        if max(abs(da),abs(db)) < 1e-10:
            break
    if b <= 0 or not all(math.isfinite(v) for v in (a,b)):
        raise ValueError('Nonmonotone or invalid Platt fit')
    return (a,b)


def training_data(db_path, cutoff=TRAIN_CUTOFF):
    if cutoff + timedelta(hours=48) > EVALUATION_START:
        raise ValueError('Training cutoff violates 48-hour embargo')
    uri=f'file:{Path(db_path).resolve()}?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as conn:
        rows=conn.execute('''SELECT prob_o25,result_o25,timestamp,created_at,match_id
            FROM predictions WHERE modelo='v2' AND status='finished'
            AND result_o25 IN (0,1) AND prob_o25 BETWEEN 0.01 AND 99.99
            AND timestamp < ? ORDER BY timestamp, match_id''', (int(cutoff.timestamp()),)).fetchall()
    valid=[]
    for p,y,kickoff,created,match_id in rows:
        try:
            created_at=utc(created)
        except (ValueError,TypeError):
            continue
        if MODEL_V2_START <= created_at and created_at.timestamp() < kickoff:
            valid.append((float(p)/100,int(y),int(kickoff),str(match_id)))
    if len(valid) < MIN_TRAIN:
        raise ValueError('Insufficient historic model-v2 training observations')
    return valid


def evaluation_data(csv_path, start=EVALUATION_START):
    values=[]
    with Path(csv_path).open(encoding='utf-8-sig',newline='') as fh:
        for row in csv.DictReader(fh):
            try:
                kickoff=utc(row['kickoff_utc']); captured=utc(row['capturado_em_utc']); quoted=utc(row['quote_at_utc'])
                p=float(row['prob_o25_publicada_no_instante_pct'])/100
                outcome=int(row['resultado_over25'])
                odd=float(row['odd_consenso_bsd'])
            except (KeyError,ValueError,TypeError):
                continue
            if not (kickoff>=start and captured<kickoff and quoted<=captured and 0<p<1 and
                    outcome in (0,1) and math.isfinite(odd) and 1.01<=odd<=100):
                continue
            # The existing publication pipeline applies current-schedule validation.
            # Do not reinterpret quarantined/obsolete odds here.
            if row.get('estado_horario')=='quarantined':
                continue
            values.append((p,outcome,kickoff,str(row['match_id']),odd,row.get('modelo_legado_inferido','')))
    if len({r[3] for r in values})!=len(values):
        raise ValueError('Duplicate match ids in evaluation CSV')
    return sorted(values,key=lambda r:(r[2],r[3]))


def metrics(values, a, b):
    if not values:
        raise ValueError('Empty evaluation')
    raw_brier=shadow_brier=raw_ll=shadow_ll=profit=0.0
    daily=defaultdict(list)
    for p,y,dt,_,odd,_ in values:
        q=calibrate(p,a,b)
        raw=(p-y)**2; new=(q-y)**2
        raw_brier+=raw;shadow_brier+=new
        raw_log=-y*math.log(p)-(1-y)*math.log1p(-p)
        shadow_log=-y*math.log(q)-(1-y)*math.log1p(-q)
        raw_ll+=raw_log;shadow_ll+=shadow_log
        profit+=odd-1 if y else -1
        daily[dt.date().isoformat()].append((new-raw,shadow_log-raw_log))
    n=len(values)
    day_values=list(daily.values())
    bootstrap_brier=[];bootstrap_logloss=[]
    if len(day_values)>=5:
        rng=random.Random(20261114)
        for _ in range(5000):
            selected=[day_values[rng.randrange(len(day_values))] for _ in day_values]
            flat=[item for day in selected for item in day]
            bootstrap_brier.append(sum(z[0] for z in flat)/len(flat))
            bootstrap_logloss.append(sum(z[1] for z in flat)/len(flat))
    def ci(numbers):
        if not numbers:return None
        numbers.sort()
        return [numbers[int(.025*len(numbers))],numbers[int(.975*len(numbers))]]
    return {'n':n,'days':len(daily),'legacy_inferred_n':sum(bool(r[5]) for r in values),
            'paired_brier_delta_daybootstrap_ci95':ci(bootstrap_brier),
            'paired_logloss_delta_daybootstrap_ci95':ci(bootstrap_logloss),
            'brier_raw':raw_brier/n,'brier_shadow':shadow_brier/n,
            'brier_delta_shadow_minus_raw':(shadow_brier-raw_brier)/n,
            'logloss_raw':raw_ll/n,'logloss_shadow':shadow_ll/n,
            'logloss_delta_shadow_minus_raw':(shadow_ll-raw_ll)/n,
            'roi_all_unchanged_pct':profit/n*100,
            'day_blocks':{k:{'n':len(v),'sum_brier_delta':sum(x[0] for x in v),
                            'sum_logloss_delta':sum(x[1] for x in v)} for k,v in daily.items()}}


def run(db_path=DB_PATH, csv_path=ROOT/'roi_jogos.csv'):
    train=training_data(db_path); test=evaluation_data(csv_path)
    if not test:
        raise ValueError('No evaluation games')
    a,b=fit([(p,y) for p,y,_,_ in train])
    data=metrics(test,a,b)
    # The comparison is post-hoc exploratory; never auto-enable in production.
    return {'status':'SHADOW_ONLY_NO_PRODUCTION_ACTIVATION',
            'classification':'RETROSPECTIVE_EXPLORATORY_NOT_PRE_REGISTERED',
            'training_cutoff_utc':TRAIN_CUTOFF.isoformat(),
            'evaluation_start_utc':EVALUATION_START.isoformat(),
            'training_n':len(train),'training_first_kickoff_utc':datetime.fromtimestamp(train[0][2],timezone.utc).isoformat(),
            'training_last_kickoff_utc':datetime.fromtimestamp(train[-1][2],timezone.utc).isoformat(),
            'training_rows_sha256':hashlib.sha256(json.dumps(train).encode()).hexdigest(),
            'evaluation_csv_sha256':hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
            'regularization_identity_l2':RIDGE,
            'coefficients':{'a':a,'b':b},
            'comparison':{key:value for key,value in data.items() if key!='day_blocks'},
            'limitations':['Legacy training predictions not independently frozen at generation time.',
                          'Legacy settled_at missing: 48h outcome maturity assumed not verified.',
                          'Evaluation population conditioned on BSD odds coverage and may be biased.',
                          'Evaluation outcomes had been inspected in prior ROI research; no independent holdout.',
                          'Odds are BSD consensus, not executable. Brier improvement does not prove positive ROI.',
                          'No automatic activation, retraining, or parameter selection from evaluation.']}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',default=DB_PATH)
    ap.add_argument('--csv',default=ROOT/'roi_jogos.csv')
    ap.add_argument('--output',default=None,help='Optional research JSON path; never write a production config')
    args=ap.parse_args()
    result=run(args.db,args.csv)
    content=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if args.output:
        target=Path(args.output).resolve()
        protected={ROOT/'calibracao_o25_v2.json',ROOT/'recalibracao.json',ROOT/'predictions.db'}
        if target in protected or not target.is_relative_to((ROOT/'research').resolve()):
            ap.error('Output restricted to the research directory')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content,encoding='utf-8')
    print(content)


if __name__=='__main__':
    main()
