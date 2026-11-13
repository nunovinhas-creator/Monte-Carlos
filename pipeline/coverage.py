"""Read-only odds denominators and observed failure evidence. No inferred backfill."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from project_paths import DB_PATH


def coverage_report(db_path=DB_PATH, now=None, days=7):
    from analysis.roi_report import load_rows
    now = now or datetime.now(timezone.utc)
    start,end = int(now.timestamp())-days*86400,int(now.timestamp())
    with sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro',uri=True) as db:
        db.row_factory = sqlite3.Row
        revised = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='operational_schedule'").fetchone())
        # Quarantine excludes the price numerator, never hides an event from
        # the denominator: retain its immutable date as the reporting reference.
        ts = 'COALESCE(o.operational_kickoff,p.timestamp)' if revised else 'p.timestamp'
        join = 'JOIN operational_schedule o USING(match_id)' if revised else ''
        events = [dict(r) for r in db.execute(f'''SELECT p.match_id,p.league,{ts} kickoff
            FROM predictions p {join} WHERE p.modelo='v2' AND p.status='finished'
            AND {ts}>=? AND {ts}<?''',(start,end))]
        ids = {r['match_id'] for r in events}
        valid = {r['match_id'] for r in load_rows(db_path) if r['match_id'] in ids}
        attempts = defaultdict(list)
        decisions = defaultdict(list)
        for r in db.execute('SELECT match_id,estado,capturado_em FROM odds_snapshots ORDER BY julianday(capturado_em),id'):
            if r['match_id'] in ids:
                attempts[r['match_id']].append(dict(r))
        if revised:
            for r in db.execute('SELECT match_id,decision AS estado,run_at AS capturado_em,queried FROM odds_capture_audit ORDER BY julianday(run_at),id'):
                if r['match_id'] in ids:
                    (attempts if r['queried'] else decisions)[r['match_id']].append(dict(r))
        def observed_time(record):
            try:
                return datetime.fromisoformat(record['capturado_em'].replace('Z','+00:00')).timestamp()
            except (TypeError,ValueError):
                return float('-inf')
        for records in list(attempts.values())+list(decisions.values()):
            records.sort(key=observed_time)
        categories = Counter()
        leagues, intervals = {}, {}
        for event in events:
            mid = event['match_id']
            # Exactly one final category per event. Old unqueried events cannot
            # be retroactively attributed to cron/window/API/quota failures.
            evidence = attempts[mid] or decisions[mid]
            category = 'eligible' if mid in valid else evidence[-1]['estado'] if evidence else 'not_observed_cause_ND'
            categories[category] += 1
            day = datetime.fromtimestamp(event['kickoff'],timezone.utc).date().isoformat()
            for groups,key in ((leagues,event['league'] or 'N/D'),(intervals,day)):
                group = groups.setdefault(key,{'settled':0,'queried':0,'eligible':0,'reasons':Counter()})
                group['settled'] += 1; group['queried'] += bool(attempts[mid]); group['eligible'] += mid in valid
                group['reasons'][category] += 1
        queried = sum(bool(attempts[mid]) for mid in ids)
        latest_run = None
        if revised:
            last = db.execute('SELECT MAX(run_at) FROM odds_capture_audit').fetchone()[0]
            if last:
                decisions = Counter(dict(db.execute('SELECT decision,COUNT(*) FROM odds_capture_audit WHERE run_at=? GROUP BY decision',(last,)).fetchall()))
                total,called = db.execute('SELECT COUNT(*),SUM(queried) FROM odds_capture_audit WHERE run_at=?',(last,)).fetchone()
                latest_run = {'run_at':last,'pending_considered':total,'queried':called,
                              'queried_pct':called/total*100 if total else None,'decisions':decisions}
        for groups in (leagues,intervals):
            for group in groups.values():
                group['coverage_pct'] = group['eligible']/group['settled']*100
                group['queried_pct'] = group['queried']/group['settled']*100
        return {'from_utc':datetime.fromtimestamp(start,timezone.utc).isoformat(),'to_utc':now.isoformat(),
                'settled':len(ids),'eligible':len(valid),'queried':queried,
                'coverage_pct':len(valid)/len(ids)*100 if ids else None,
                'queried_pct':queried/len(ids)*100 if ids else None,
                'eligible_among_queried_pct':len(valid)/queried*100 if queried else None,
                'event_reasons':categories,'by_league':leagues,'by_day_utc':intervals,'latest_run':latest_run,
                'limitations':['not_observed is missing evidence, not proof of a missed cron',
                    'legacy API/quota errors may have been collapsed into missing odds',
                    'ROI is conditional on price coverage; selection bias remains',
                    '6h window, 2h cron and 80-call budget unchanged; no historical backfill']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default=DB_PATH)
    parser.add_argument('--now',help='timezone-aware ISO timestamp for reproducible audit')
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now.replace('Z','+00:00')) if args.now else None
    if now is not None and now.tzinfo is None:
        parser.error('--now requires timezone')
    print(json.dumps(coverage_report(args.db,now),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
