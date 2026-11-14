"""Offline-only regression tests for the chronologically separated Platt candidate."""
import csv
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis.shadow_platt_o25 import (
    EVALUATION_START, TRAIN_CUTOFF, calibrate, evaluation_data, fit,
    metrics, run, training_data, utc,
)


class ShadowPlattTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name)/'fixture.db'
        self.csv = Path(self.tmp.name)/'fixture.csv'
        with sqlite3.connect(self.db) as db:
            db.execute('''CREATE TABLE predictions (match_id TEXT PRIMARY KEY,
                prob_o25 REAL,result_o25 INTEGER,timestamp INTEGER,created_at TEXT,
                modelo TEXT,status TEXT)''')
            start=datetime(2026,10,5,tzinfo=timezone.utc)
            rows=[]
            for i in range(650):
                event=start+timedelta(minutes=15*i)
                p=40+(i%40)
                y=int((i*37)%100<p)
                rows.append((str(i),p,y,int(event.timestamp()),
                             (event-timedelta(hours=2)).isoformat(),'v2','finished'))
            db.executemany('INSERT INTO predictions VALUES(?,?,?,?,?,?,?)',rows)
        columns=['match_id','kickoff_utc','capturado_em_utc','quote_at_utc',
                 'prob_o25_publicada_no_instante_pct','resultado_over25',
                 'odd_consenso_bsd','modelo_legado_inferido','estado_horario']
        self.rows=[]
        for j in range(10):
            kickoff=EVALUATION_START+timedelta(days=j)
            self.rows.append(dict(match_id=f'test{j}',kickoff_utc=kickoff.isoformat(),
                capturado_em_utc=(kickoff-timedelta(hours=1)).isoformat(),
                quote_at_utc=(kickoff-timedelta(hours=1,minutes=3)).isoformat(),
                prob_o25_publicada_no_instante_pct=62+j,resultado_over25=j%2,
                odd_consenso_bsd=1.7,modelo_legado_inferido='v2',estado_horario='original'))
        self.columns=columns
        self.write_csv()

    def write_csv(self):
        with self.csv.open('w',newline='',encoding='utf-8') as fh:
            writer=csv.DictWriter(fh,fieldnames=self.columns)
            writer.writeheader();writer.writerows(self.rows)

    def test_training_stops_before_holdout_and_is_not_mutated(self):
        train=training_data(self.db)
        self.assertEqual(len(train),650)
        self.assertTrue(all(datetime.fromtimestamp(r[2],timezone.utc)<TRAIN_CUTOFF for r in train))
        before=run(self.db,self.csv)
        self.rows[0]['resultado_over25']=1-self.rows[0]['resultado_over25']
        self.write_csv()
        after=run(self.db,self.csv)
        self.assertEqual(before['coefficients'],after['coefficients'])
        self.assertEqual(before['training_rows_sha256'],after['training_rows_sha256'])
        self.assertEqual(before['status'],'SHADOW_ONLY_NO_PRODUCTION_ACTIVATION')

    def test_monotone_platt_and_finite_metrics(self):
        t=training_data(self.db)
        a,b=fit([(r[0],r[1]) for r in t])
        self.assertGreater(b,0)
        self.assertLess(calibrate(.2,a,b),calibrate(.5,a,b))
        self.assertLess(calibrate(.5,a,b),calibrate(.8,a,b))
        result=run(self.db,self.csv)['comparison']
        self.assertEqual(result['n'],10)
        self.assertTrue(0<=result['brier_shadow']<=1)
        self.assertEqual(result['days'],10)
        self.assertEqual(len(result['paired_brier_delta_daybootstrap_ci95']),2)

    def test_invalid_timestamp_cutoff_and_post_kickoff_odds(self):
        with self.assertRaises(ValueError):
            utc('2026-10-10T10:00:00')
        with self.assertRaises(ValueError):
            training_data(self.db,cutoff=EVALUATION_START-timedelta(hours=1))
        self.rows[1]['capturado_em_utc']=(EVALUATION_START+timedelta(days=1,hours=1)).isoformat()
        self.rows[2]['estado_horario']='quarantined'
        self.write_csv()
        evals=evaluation_data(self.csv)
        self.assertEqual(len(evals),8)
        with self.assertRaises(ValueError):
            calibrate(.65,0,-1)
        with self.assertRaises(ValueError):
            metrics([],0,1)


if __name__=='__main__':
    unittest.main()
