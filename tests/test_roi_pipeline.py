"""Testes offline da cadeia odds -> snapshots -> settlement -> ROI e calibração."""
import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
from calibration_o25 import aplicar
from odds_tracking import capturar, parse_quote
from roi_report import gerar, simular, load_rows, classificar


class OddsRoiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'sample.db')
        with patch.object(database, 'DB_NAME', self.path):
            database.init_db()
        self.now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
        self.kickoff = int((self.now + timedelta(hours=2)).timestamp())
        con = sqlite3.connect(self.path)
        con.execute('''INSERT INTO predictions
          (match_id, timestamp, modelo, status, prob_o25, prob_o25_base, result_o25, created_at,
          home_team, away_team, league, prob_btts, baixa_confianca) VALUES
          ('1001', ?, 'v2', 'pending', 67, 73, null, ?, 'Home', 'Away', 'Liga', 65, 0)''',
                    (self.kickoff, (self.now - timedelta(hours=1)).isoformat()))
        con.commit(); con.close()

    def payload(self, odd=1.9, quote=None):
        return {'odds': {'over_25_goals': odd},
                'last_update_at': (quote or self.now - timedelta(minutes=7)).isoformat()}

    def test_strict_parser(self):
        self.assertEqual(parse_quote({'odds': {'over_25_goals': 2}})[2], 'sem_timestamp_da_cotacao')
        self.assertEqual(parse_quote({'odds': {'over_25_goals': 'NaN'}, 'last_update_at': self.now.isoformat()})[2], 'valor_odd_invalido')
        self.assertEqual(parse_quote({'odds': {'under_25_goals': 1.8}})[2], 'sem_odd_over_25')
        self.assertEqual(parse_quote(self.payload())[2], 'ok')

    def test_no_future_quote(self):
        c = capturar(self.path, api_client=lambda _: self.payload(2.5, self.now + timedelta(minutes=10)), now=self.now, delay=0)
        self.assertEqual(c['capturadas'], 0)
        con = sqlite3.connect(self.path)
        self.assertEqual(con.execute('select odd from odds_snapshots').fetchone()[0], None)
        con.close()

    def test_capture_once_and_roi(self):
        c = capturar(self.path, api_client=lambda _: self.payload(), now=self.now, delay=0)
        self.assertEqual(c['capturadas'], 1)
        c = capturar(self.path, api_client=lambda _: self.payload(), now=self.now, delay=0)
        self.assertEqual(c['tentativas'], 0)
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE predictions SET status = 'finished', result_o25 = 1 WHERE match_id = '1001'")
        conn.commit(); conn.close()
        rows = load_rows(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['prob_btts'], 65)
        self.assertEqual(rows[0]['baixa_confianca'], 0)
        flags = classificar(rows[0])
        self.assertTrue(flags['original_over'])
        self.assertTrue(flags['original_over_btts'])
        self.assertTrue(flags['mc_o70'])
        roi = simular(rows)
        self.assertEqual(roi['n'], 1)
        self.assertAlmostEqual(roi['roi'], 90.0)
        html_path = os.path.join(self.tmp.name, 'roi.html')
        csv_path = os.path.join(self.tmp.name, 'roi.csv')
        resumo = gerar(self.path, html_path, csv_path)
        self.assertEqual(len(resumo), 5)
        self.assertEqual(resumo[1][1]['n'], 1)
        self.assertEqual(resumo[2][1]['n'], 1)
        self.assertEqual(resumo[3][1]['n'], 1)
        self.assertTrue(os.path.getsize(csv_path) > 50)
        text = Path(html_path).read_text()
        self.assertIn('consenso', text.lower())
        self.assertIn('Original — destaques verdes de 60%', text)
        self.assertIn('MC-O70 (experimental)', text)
        self.assertIn('fuga de informação', text)
        header = Path(csv_path).read_text(encoding='utf-8-sig').splitlines()[0]
        self.assertIn('prob_btts_no_instante_pct', header)
        self.assertIn('original_over_e_btts_verdes_60', header)

    def test_no_backfill_and_no_fake_roi(self):
        self.assertEqual(load_rows(self.path), [])
        self.assertIsNone(simular([])['roi'])
        self.assertEqual(capturar(self.path, max_calls=0, now=self.now, delay=0)['tentativas'], 0)


    def test_historical_snapshot_missing_btts_confidence_is_not_backfilled(self):
        cap = capturar(self.path, api_client=lambda _: self.payload(), now=self.now, delay=0)
        self.assertEqual(cap['capturadas'], 1)
        con = sqlite3.connect(self.path)
        con.execute("""UPDATE odds_snapshots SET prob_btts_no_instante=NULL,
            baixa_confianca_no_instante=NULL WHERE match_id='1001'""")
        con.execute("""UPDATE predictions SET status='finished', result_o25=1,
            prob_btts=99, baixa_confianca=0 WHERE match_id='1001'""")
        con.commit()
        con.close()
        rows = load_rows(self.path)
        self.assertEqual(len(rows), 1)
        flags = classificar(rows[0])
        self.assertIsNone(flags['original_over'])
        self.assertIsNone(flags['original_over_btts'])
        self.assertTrue(flags['mc_o70'])
        sums = gerar(self.path, os.path.join(self.tmp.name, 'r.html'),
                     os.path.join(self.tmp.name, 'r.csv'))
        self.assertEqual(sums[0][1]['n'], 1)
        self.assertEqual(sums[1][1]['n'], 0)
        self.assertEqual(sums[2][1]['n'], 0)
        self.assertEqual(sums[3][1]['n'], 1)

    def test_classification_uses_frozen_values_not_later_predictions(self):
        cap = capturar(self.path, api_client=lambda _: self.payload(), now=self.now, delay=0)
        self.assertEqual(cap['capturadas'], 1)
        con = sqlite3.connect(self.path)
        con.execute("""UPDATE predictions SET status='finished', result_o25=0,
            prob_o25=3, prob_o25_base=3, prob_btts=3, baixa_confianca=1
            WHERE match_id='1001'""")
        con.commit()
        con.close()
        rows = load_rows(self.path)
        self.assertEqual(rows[0]['prob_o25'], 67)
        self.assertEqual(rows[0]['prob_btts'], 65)
        self.assertTrue(classificar(rows[0])['original_over_btts'])
        self.assertTrue(classificar(rows[0])['mc_o70'])
        self.assertAlmostEqual(simular(rows)['roi'], -100)

    def test_low_confidence_cannot_be_counted_as_original_green(self):
        con = sqlite3.connect(self.path)
        con.execute("UPDATE predictions SET baixa_confianca=1 WHERE match_id='1001'")
        con.commit()
        con.close()
        capturar(self.path, api_client=lambda _: self.payload(), now=self.now, delay=0)
        con = sqlite3.connect(self.path)
        con.execute("UPDATE predictions SET status='finished', result_o25=1 WHERE match_id='1001'")
        con.commit()
        con.close()
        rules = classificar(load_rows(self.path)[0])
        self.assertFalse(rules['original_over'])
        self.assertFalse(rules['original_over_btts'])
        self.assertTrue(rules['mc_o70'])

    def test_calibration_guard(self):
        p, ver = aplicar(80, {'ativo': False, 'modelo': 'v2'})
        self.assertEqual((p, ver), (80, None))
        p, ver = aplicar(80, {'ativo': True, 'modelo': 'v2', 'a': 0, 'b': .7, 'versao': 'test'})
        self.assertTrue(50 < p < 80)
        self.assertEqual(ver, 'test')


if __name__ == '__main__':
    unittest.main()