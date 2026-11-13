"""Historical integrity, chronology and offline orchestration regression tests."""
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from pipeline import database
from pipeline.integrity import state, compare, audit, published
from pipeline.odds_tracking import capturar
from pipeline import settlement
from analysis.metrics import scores, block_interval
from analysis.observations import prediction_rows
from analysis.strategy_status import scientific_status
from analysis.calibration_study import temporal_split, fit_isotonic, predict
from analysis.backtest import calcular_trivial_walk_forward
from src import main, predictor
from analysis.calibration_o25 import aplicar
from pipeline.pages_status import wait_for_build

ROOT = Path(__file__).resolve().parents[1]


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name)/'existing.db')
        self.db_patch = patch.object(database,'DB_NAME',self.path)
        self.db_patch.start(); self.addCleanup(self.db_patch.stop)
        database.init_db()
        self.now = datetime.now(timezone.utc)
        self.game = dict(id='123',match_id='123',timestamp=int((self.now+timedelta(hours=2)).timestamp()),
                         data_str='future',liga='League',home='H',away='A',xg_home=1.5,xg_away=1.2,
                         o25=70,btts=65,o25_base=70,btts_base=65,baixa_confianca=0,modelo='v2',
                         origem_xg='h2h',parameters={'rho':-.12},provenance={'source':'bsd'})

    def save(self, game=None, observed=None):
        with patch.object(database,'datetime') as clock:
            clock.now.return_value = observed or self.now
            database.salvar_previsoes_db([game or self.game])

    def test_append_updates_and_immutable_history(self):
        self.save()
        first_state = state(self.path)
        updated = dict(self.game,o25=75)
        self.save(updated,self.now+timedelta(minutes=5))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT prob_o25 FROM prediction_snapshots ORDER BY id').fetchall(),[(70.,),(75.,)])
            self.assertEqual(db.execute('SELECT prob_o25 FROM predictions').fetchone()[0],75)
            for command in ['UPDATE prediction_snapshots SET prob_o25=90','DELETE FROM prediction_snapshots','DELETE FROM predictions']:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(command)
            db.execute("UPDATE predictions SET status='finished',home_score=2,away_score=1,result_o25=1,result_btts=1")
        self.assertFalse(compare(first_state,state(self.path)))
        for definition, expected in [('first',70),('last',75)]:
            self.assertEqual(prediction_rows(self.path,definition)[0]['prob_o25'],expected)
        before = state(self.path)
        self.save(dict(self.game,o25=99),self.now+timedelta(minutes=10))
        self.assertEqual(before,state(self.path))
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE predictions SET home_score=4')

    def test_no_late_snapshot_or_current_backfill(self):
        self.save()
        before = state(self.path)
        self.save(dict(self.game,o25=99),self.now+timedelta(hours=3))
        self.assertEqual(before,state(self.path))
        self.save(dict(self.game,id='late',timestamp=int(self.now.timestamp())-10))
        self.assertEqual(before,state(self.path))

    def test_transaction_rolls_back_both_views_on_failure(self):
        before = state(self.path)
        with self.assertRaises((ValueError,sqlite3.IntegrityError)):
            self.save(dict(self.game,provenance={'bad':float('nan')}))
        self.assertEqual(before,state(self.path))

    def test_odds_frozen_immutable_and_too_late_response(self):
        self.save()
        payload = {'odds':{'over_25_goals':1.8},'last_update_at':self.now.isoformat()}
        capturar(self.path,api_client=lambda _:payload,now=self.now+timedelta(minutes=1),delay=0)
        with sqlite3.connect(self.path) as db:
            model, snapshot = db.execute('SELECT modelo_no_instante,prediction_snapshot_id FROM odds_snapshots').fetchone()
            self.assertEqual((model,snapshot),('v2',1))
            for command in ['UPDATE odds_snapshots SET odd=3','DELETE FROM odds_snapshots']:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(command)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO odds_snapshots (match_id,capturado_em,quote_at,estado,odd) VALUES ('123',?,?, 'ok',2)",
                           ((self.now+timedelta(hours=3)).isoformat(),self.now.isoformat()))

    def test_api_response_after_kickoff_is_not_registered(self):
        self.save()
        ticks = iter([self.now,self.now,self.now+timedelta(hours=3)])
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return next(ticks)
        with patch('pipeline.odds_tracking.datetime',Clock):
            captured = capturar(self.path,api_client=lambda _: {
                'odds':{'over_25_goals':2},'last_update_at':self.now.isoformat()},delay=0)
        self.assertEqual(captured['capturadas'],0)

    def test_integrity_detects_loss_and_corruption(self):
        self.save()
        before = state(self.path)
        changed = json.loads(json.dumps(before)); changed['tables']['predictions']['rows'].clear()
        self.assertTrue(compare(before,changed))
        changed = json.loads(json.dumps(before)); changed['tables']['prediction_snapshots']['rows']['1'][5]=99
        self.assertTrue(compare(before,changed))
        self.assertEqual(audit(self.path)['errors'],[])
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE predictions SET status='finished',home_score=0,away_score=0,result_o25=1,result_btts=0")
        self.assertTrue(audit(self.path)['errors'])

    def test_settlement_mock_and_repeat_preserve_results(self):
        self.game['timestamp'] = int((self.now-timedelta(hours=4)).timestamp())
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO predictions (match_id,timestamp,status,modelo,home_team,away_team) VALUES ('123',?,'pending','v2','H','A')",(self.game['timestamp'],))
        with patch.object(settlement,'DB_NAME',self.path), patch.object(settlement,'API_TOKEN','mock'), patch.object(settlement,'consultar_resultado_api',return_value=(2,1,'finished')) as api:
            settlement.resolver_jogos(); settlement.resolver_jogos()
            self.assertEqual(api.call_count,1)
        with sqlite3.connect(self.path) as db:
            row=db.execute('SELECT home_score,away_score,result_o25,result_btts,settled_at FROM predictions').fetchone()
            self.assertEqual(row[:4],(2,1,1,1)); self.assertIsNotNone(row[4])

    def test_migration_existing_database_idempotent_and_rollback(self):
        original = ROOT/'predictions.db'
        shutil.copyfile(original,self.path)
        before = state(self.path)
        digest = hashlib.sha256(Path(self.path).read_bytes()).hexdigest()
        database.init_db(); after=state(self.path)
        self.assertFalse(compare(before,after))
        self.assertEqual(after['counts']['prediction_snapshots'],before['counts'].get('prediction_snapshots',0))
        db_hash=hashlib.sha256(Path(self.path).read_bytes()).hexdigest()
        database.init_db()
        self.assertEqual(after,state(self.path))
        self.assertEqual(db_hash,hashlib.sha256(Path(self.path).read_bytes()).hexdigest())
        backup=Path(self.tmp.name)/'data/backups'/f'pre-kickoffs-{digest}.db'
        if 'odds_capture_audit' not in before['counts']:
            self.assertTrue(backup.exists())
            shutil.copyfile(backup,self.path)
        self.assertEqual(before,state(self.path))

    def test_failed_migration_rolls_back_ddl(self):
        shutil.copyfile(ROOT/'predictions.db',self.path)
        before=state(self.path)
        def fail(conn):
            conn.execute('ALTER TABLE predictions ADD COLUMN should_rollback TEXT')
            raise RuntimeError('simulated migration error')
        with patch.object(database,'migrate',side_effect=fail):
            with self.assertRaises(RuntimeError): database.init_db()
        self.assertEqual(before,state(self.path))


class ScienceTests(unittest.TestCase):
    def test_scores_known_values_and_invalid_values(self):
        m=scores([.2,.8],[0,1])
        self.assertAlmostEqual(m['brier'],.04)
        self.assertAlmostEqual(m['log_loss'],-__import__('math').log(.8))
        self.assertAlmostEqual(m['skill'],.84)
        self.assertIsNone(scores([],[])['brier'])
        with self.assertRaises(ValueError): scores([float('nan')],[1])

    def test_temporal_partitions_disjoint_with_embargo(self):
        rows=[{'timestamp':1700000000+i*86400+j,'match_id':str(i)+'-'+str(j)} for i in range(100) for j in range(2)]
        train,validation,test=temporal_split(rows)
        self.assertLess(max(r['timestamp'] for r in train)+48*3600,min(r['timestamp'] for r in validation))
        self.assertLess(max(r['timestamp'] for r in validation)+48*3600,min(r['timestamp'] for r in test))
        self.assertFalse({r['match_id'] for r in train}&{r['match_id'] for r in test})

    def test_league_baseline_cannot_see_simultaneous_or_recent_results(self):
        def row(ts,y,league=1): return (60,60,y,y,'old',league,ts,'v2')
        output=calcular_trivial_walk_forward([row(100,1),row(100,0),row(110,0),row(200000,1),row(200001,0,None)])
        self.assertEqual(output[0][-2:],(None,None)); self.assertEqual(output[1][-2:],(None,None))
        self.assertEqual(output[2][-2:],(None,None)); self.assertAlmostEqual(output[3][-2],1/3)
        self.assertEqual(output[4][-2:],(None,None))

    def test_isotonic_ties_and_monotonicity(self):
        fit=fit_isotonic([.1,.1,.2,.3],[1,0,0,1])
        self.assertEqual(fit['x'],[.1,.2,.3])
        self.assertTrue(all(a<=b for a,b in zip(fit['y'],fit['y'][1:])))
        self.assertAlmostEqual(fit['y'][0],1/3)

    def test_no_automatic_validation_or_activation(self):
        self.assertEqual(scientific_status({'n':0}),'EXPERIMENTAL')
        self.assertEqual(scientific_status({'n':100,'roi_ci95':[-10,-1]}),'NO-GO')
        self.assertEqual(scientific_status({'n':10000,'roi_ci95':[1,10]}),'INCONCLUSIVO')
        self.assertEqual(scientific_status({'n':10000},False),'NO-GO')
        with patch.dict('os.environ',{'APPLY_RECAL':'1'}):
            predictor._carregar_recalibracao.cache_clear()
            self.assertIsNone(predictor._carregar_recalibracao())
            self.assertEqual(aplicar(80),(80,None))

    def test_bootstrap_reproducible_and_reports_too_few_days(self):
        self.assertIsNone(block_interval([1,2],[0,1]))
        values=list(range(20)); ts=[i*86400 for i in values]
        self.assertEqual(block_interval(values,ts),block_interval(values,ts))


class PipelineTests(unittest.TestCase):
    def test_pages_requires_exact_commit_and_propagates_failure(self):
        from unittest.mock import Mock
        def response(sha,status):
            r=Mock(); r.json.return_value={'commit':sha,'status':status}; return r
        wait_for_build('owner/repo','new','mock',get=Mock(side_effect=[response('old','built'),response('new','built')]),sleep=lambda _:None)
        with self.assertRaises(RuntimeError):
            wait_for_build('owner/repo','new','mock',get=Mock(return_value=response('old','built')),sleep=lambda _:None,attempts=1)
        with self.assertRaises(RuntimeError):
            wait_for_build('owner/repo','new','mock',get=Mock(return_value=response('new','errored')),sleep=lambda _:None)

    def test_workflow_safety_cron_and_pipeline_entry_points(self):
        import yaml
        from importlib import import_module
        config=yaml.load((ROOT/'.github/workflows/monte_carlo.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(config['on']['schedule'][0]['cron'],'17 */2 * * *')
        self.assertEqual(config['jobs']['build']['if'],"github.ref == 'refs/heads/main'")
        scripts=['src.main','pipeline.odds_tracking','pipeline.settlement','analysis.backtest','analysis.roi_report']
        for script in scripts: import_module(script)
        steps=config['jobs']['build']['steps']
        before_push=[s.get('run','') for s in steps[:next(i for i,s in enumerate(steps) if s.get('id')=='persist')]]
        self.assertTrue(any('--compare' in s and '--published' in s for s in before_push))
        self.assertFalse(any('calibration_study' in s or 'recalibracao.py' in s for s in before_push))

    def test_offline_end_to_end_main_odds_settlement_reports(self):
        import os
        from analysis import backtest, roi_report
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'test.db'); now=datetime.now(timezone.utc)
            kickoff=now+timedelta(hours=2)
            event={'id':999,'event_date':kickoff.isoformat(),'home_team':{'name':'Home','id':1},'away_team':{'name':'Away','id':2},'league_id':1}
            cwd=os.getcwd()
            try:
                os.chdir(directory)
                shutil.copytree(ROOT/'research',Path(directory)/'research')
                with patch.object(database,'DB_NAME',path), patch.object(main,'DB_NAME',path), patch.object(main,'obter_jogos_proximos_dias',return_value=[event]), patch.object(main,'recolher_estatisticas_equipas'), patch.object(main,'monte_carlo_sim',return_value=(75,65)):
                    main.analisar()
                capturar(path,api_client=lambda _: {'odds':{'over_25_goals':1.9},'last_update_at':now.isoformat()},now=now+timedelta(minutes=1),delay=0)
                # Time advances; no rescheduling or mutation of frozen kickoff.
                with patch.object(settlement,'DB_NAME',path), patch.object(settlement,'API_TOKEN','mock'), patch.object(settlement,'obter_jogos_pendentes_passados',return_value=[('999','Home','Away',int(kickoff.timestamp()))]), patch.object(settlement,'consultar_resultado_api',return_value=(2,1,'finished')):
                    settlement.resolver_jogos()
                with patch.object(backtest,'DB_NAME',path): backtest.main()
                roi_report.gerar(path)
                self.assertEqual(published(directory),[])
                self.assertIn('v2',Path('index.html').read_text())
                self.assertEqual(len(prediction_rows(path,'first')),1)
                self.assertEqual(len(roi_report.load_rows(path)),1)
            finally:
                os.chdir(cwd)


class LeakageTests(unittest.TestCase):
    def test_holdout_labels_cannot_change_fit_or_selection(self):
        from analysis.calibration_study import run
        from analysis.roi_report import load_rows
        rows=load_rows(str(ROOT/'predictions.db'))
        _,_,holdout=temporal_split(rows)
        test_ids={r['match_id'] for r in holdout}
        modified=[dict(r,result_o25=1-r['result_o25']) if r['match_id'] in test_ids else dict(r) for r in rows]
        with tempfile.TemporaryDirectory() as directory:
            with patch('analysis.calibration_study.load_rows',return_value=rows):
                original=run(str(ROOT/'predictions.db'),Path(directory)/'original')
            with patch('analysis.calibration_study.load_rows',return_value=modified):
                changed=run(str(ROOT/'predictions.db'),Path(directory)/'changed')
        self.assertEqual(original['models'],changed['models'])
        self.assertEqual(original['selected_on_validation'],changed['selected_on_validation'])
        self.assertEqual(original['validation_scores'],changed['validation_scores'])
        self.assertNotEqual(original['holdout_scores'],changed['holdout_scores'])


if __name__ == '__main__':
    unittest.main()
