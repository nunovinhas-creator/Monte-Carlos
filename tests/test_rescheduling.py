"""Deterministic event timelines, frozen evidence, quarantine and production-copy checks."""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import Mock, patch

import pytest

from pipeline import database, settlement
from pipeline.coverage import coverage_report
from pipeline.integrity import audit, compare, state
from pipeline.kickoffs import current, observe
from pipeline.odds_tracking import capturar
from pipeline.pages_status import wait_for_build
from analysis.observations import prediction_rows
from analysis.roi_report import load_rows, gerar, simular
from src import api, main

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026,11,13,8,tzinfo=timezone.utc)


@pytest.fixture
def timeline(tmp_path,monkeypatch):
    path = tmp_path/'test.db'
    monkeypatch.setattr(database,'DB_NAME',str(path))
    database.init_db()
    game = dict(id='101',timestamp=int((T0+timedelta(hours=4)).timestamp()),
        data_str='13/11/2026 12:00',liga='Liga',league_id=1,home='H',away='A',
        xg_home=1.8,xg_away=1.2,o25=75,o25_base=75,btts=65,baixa_confianca=0,modelo='v2')
    def save(at=T0,kickoff=None):
        item = dict(game,timestamp=game['timestamp'] if kickoff is None else kickoff)
        with patch.object(database,'datetime') as clock:
            clock.now.return_value = at
            return database.salvar_previsoes_db([item])
    return path,game,save


def capture(path,at=T0+timedelta(minutes=1),**kwargs):
    return capturar(path,now=at,delay=0,api_client=lambda _: {
        'odds':{'over_25_goals':1.9},'last_update_at':at.isoformat()},**kwargs)


def finish(path):
    with sqlite3.connect(path) as db:
        db.execute("UPDATE predictions SET status='finished',home_score=2,away_score=1,result_o25=1,result_btts=1")


@pytest.mark.parametrize('shift',[2,-2])
def test_revision_after_quote_requires_fresh_linked_quote(timeline,shift):
    path,game,save = timeline
    save(); capture(path)
    before = state(path)
    new = game['timestamp']+shift*3600
    save(T0+timedelta(minutes=30),new)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT timestamp FROM predictions').fetchone()[0] == game['timestamp']
        assert current(db,'101') == (new,1,'applied')
        assert db.execute('SELECT change_kind FROM kickoff_revisions').fetchone()[0] == ('postponement' if shift>0 else 'advancement')
    assert not compare(before,state(path))
    capture(path,T0+timedelta(minutes=31))
    finish(path)
    rows = load_rows(path)
    assert len(rows)==1 and rows[0]['kickoff_revision_id']==1
    assert rows[0]['prediction_snapshot_id']==2 and rows[0]['timestamp']==new
    old = load_rows(path,'as_observed')
    assert old[0]['kickoff_revision_id'] is None and old[0]['timestamp']==game['timestamp']
    assert len(load_rows(path,'revision_history'))==2
    assert len(load_rows(path,'prospective'))==1
    assert len(prediction_rows(path,'first'))==1
    assert prediction_rows(path,'last')[0]['timestamp']==new
    assert audit(path,now=T0+timedelta(days=1))['errors']==[]


def test_advancement_discovered_after_start_is_not_revalidated(timeline):
    path,game,save = timeline
    save(); capture(path)
    before = state(path)
    save(T0+timedelta(hours=2),int((T0+timedelta(hours=1)).timestamp()))
    with sqlite3.connect(path) as db:
        assert current(db,'101')[2]=='quarantined'
    assert capture(path,T0+timedelta(hours=2,minutes=1))['tentativas']==0
    finish(path)
    assert load_rows(path)==[] and load_rows(path,'prospective')==[]
    assert len(load_rows(path,'as_observed'))==1
    assert not compare(before,state(path))


def test_postponed_old_start_passed_can_get_new_forecast(timeline):
    path,game,save = timeline
    save(); capture(path)
    new = game['timestamp']+86400
    assert save(T0+timedelta(hours=5),new)=={'101'}
    # A quote now requires the new window; yesterday's quote is never recycled.
    assert capture(path,T0+timedelta(hours=5,minutes=1))['tentativas']==0
    with patch.object(settlement,'DB_NAME',str(path)), patch.object(settlement,'datetime') as clock:
        clock.now.return_value = T0+timedelta(hours=6)
        assert settlement.obter_jogos_pendentes_passados()==[]
    finish(path)
    assert load_rows(path)==[]
    assert len(load_rows(path,'as_observed'))==1


def test_revision_before_first_snapshot_preserves_legacy_created_at(timeline):
    path,game,save = timeline
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO predictions(match_id,timestamp,status,created_at) VALUES ('101',?,'pending',?)",
                   (game['timestamp'],(T0-timedelta(days=1)).isoformat()))
    save(T0,game['timestamp']+3600)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT created_at FROM predictions').fetchone()[0]==(T0-timedelta(days=1)).isoformat()
        assert db.execute('SELECT kickoff_revision_id FROM prediction_snapshots').fetchone()==(1,)


def test_repeated_changes_returning_to_original_are_separate_epochs(timeline):
    path,game,save = timeline
    save(); capture(path)
    save(T0+timedelta(minutes=10),game['timestamp']+3600)
    save(T0+timedelta(minutes=20),game['timestamp'])
    before = state(path)
    save(T0+timedelta(minutes=20),game['timestamp'])
    assert state(path)==before
    assert capture(path,T0+timedelta(minutes=21))['capturadas']==1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT previous_revision_id FROM kickoff_revisions ORDER BY id').fetchall()==[(None,),(1,)]
        for table in ('kickoff_revisions','odds_capture_audit','legacy_prediction_flags'):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f'DELETE FROM {table}') if table!='legacy_prediction_flags' else db.execute("DELETE FROM predictions")
    assert not audit(path)['errors']


@pytest.mark.parametrize('status',['finished','void','stale'])
def test_terminal_event_is_not_reopened(timeline,status):
    path,game,save = timeline
    save()
    if status=='finished':
        finish(path)
    else:
        with sqlite3.connect(path) as db:
            db.execute('UPDATE predictions SET status=?',(status,))
    before = state(path)['tables']['predictions']
    save(T0+timedelta(minutes=10),game['timestamp']+86400)
    assert state(path)['tables']['predictions']==before
    with sqlite3.connect(path) as db:
        assert current(db,'101')[2]=='quarantined'
    assert capture(path)['tentativas']==0


def test_manual_data_correction_and_pending_quarantine_release(timeline):
    path,game,save = timeline
    save()
    with sqlite3.connect(path) as db:
        observe(db,'101',None,detected_at=T0,reason='missing_evidence')
        observe(db,'101',game['timestamp']+3600,detected_at=T0+timedelta(minutes=1),
                change_kind='data_correction',source='bsd:explicit_correction')
        assert current(db,'101')[2]=='applied'
    save(T0+timedelta(minutes=2),game['timestamp']+3600)
    assert capture(path,T0+timedelta(minutes=3))['capturadas']==1


def test_quote_from_before_revision_is_not_reused(timeline):
    path,game,save = timeline
    save()
    save(T0+timedelta(minutes=10),game['timestamp']+3600)
    result = capturar(path,now=T0+timedelta(minutes=11),delay=0,api_client=lambda _: {
        'odds':{'over_25_goals':2},'last_update_at':T0.isoformat()})
    assert result['capturadas']==0
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT estado FROM odds_snapshots').fetchone()==('cotacao_anterior_a_revisao',)


def test_sql_rejects_wrong_frozen_snapshot_link_and_original_time_edits(timeline):
    path,game,save=timeline
    save()
    at=(T0+timedelta(minutes=1)).isoformat()
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError,match='snapshot link mismatch'):
            db.execute('''INSERT INTO odds_snapshots
                (match_id,capturado_em,quote_at,estado,odd,modelo_no_instante,prediction_snapshot_id,
                 kickoff_at_capture,prob_o25_no_instante,prob_o25_base_no_instante,prob_btts_no_instante,
                 baixa_confianca_no_instante)
                VALUES ('101',?,?,'ok',2,'v2',1,?,99,75,65,0)''',(at,at,game['timestamp']))
        with pytest.raises(sqlite3.IntegrityError,match='original prediction times'):
            db.execute('UPDATE predictions SET timestamp=timestamp+3600')
        db.execute("INSERT INTO legacy_prediction_flags VALUES ('101','legacy_unverifiable','test')")
        with pytest.raises(sqlite3.IntegrityError,match='immutable audit'):
            db.execute('DELETE FROM legacy_prediction_flags')


def test_schedule_change_while_api_in_flight_drops_response(timeline):
    path,game,save = timeline
    save()
    def payload(_):
        with sqlite3.connect(path) as db:
            observe(db,'101',game['timestamp']+3600,detected_at=T0+timedelta(seconds=10))
        return {'odds':{'over_25_goals':2},'last_update_at':T0.isoformat()}
    assert capturar(path,now=T0+timedelta(minutes=1),delay=0,api_client=payload)['capturadas']==0
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT decision FROM odds_capture_audit').fetchone()==('schedule_changed_during_call',)
    finish(path)
    report=coverage_report(path,now=T0+timedelta(days=1))
    assert report['queried']==1 and report['event_reasons']=={'schedule_changed_during_call':1}


def test_late_settlement_reconciles_future_api_date_then_settles_once(timeline):
    path,game,save = timeline
    save(); capture(path)
    new = game['timestamp']+86400
    payload = {'event_date':datetime.fromtimestamp(new,timezone.utc).isoformat(),'status':'notstarted'}
    def result(*args,**kwargs):
        kwargs['schedule_observations']['101'] = payload
        return None,None,'notstarted'
    with patch.object(settlement,'DB_NAME',str(path)),patch.object(settlement,'API_TOKEN','mock'),patch.object(settlement,'DELAY',0),patch.object(settlement,'datetime') as clock,patch.object(settlement,'consultar_resultado_api',side_effect=result):
        clock.now.return_value = T0+timedelta(hours=8)
        settlement.resolver_jogos()
    with sqlite3.connect(path) as db:
        assert current(db,'101')==(new,1,'applied')
        assert db.execute('SELECT status FROM predictions').fetchone()==('pending',)
    # A fresh snapshot/quote is captured before the new start; settle late.
    save(T0+timedelta(hours=25),new)
    capture(path,T0+timedelta(hours=25,minutes=1))
    with patch.object(settlement,'DB_NAME',str(path)),patch.object(settlement,'API_TOKEN','mock'),patch.object(settlement,'DELAY',0),patch.object(settlement,'datetime') as clock,patch.object(settlement,'consultar_resultado_api',return_value=(2,1,'finished')) as api_mock:
        clock.now.return_value = T0+timedelta(days=3)
        settlement.resolver_jogos(); settlement.resolver_jogos()
        assert api_mock.call_count==1
    assert len(load_rows(path))==1 and not audit(path)['errors']


def test_coverage_budget_and_non_numeric_ids_have_disjoint_diagnostics(timeline):
    path,game,save = timeline
    save()
    with sqlite3.connect(path) as db:
        for mid in ('fallback-id','102','103','104'):
            db.execute("INSERT INTO predictions(match_id,timestamp,status,modelo,created_at) VALUES (?,?,'pending','v2',?)",
                       (mid,game['timestamp'],T0.isoformat()))
    capturar(path,now=T0+timedelta(minutes=1),delay=0,max_calls=2,api_client=lambda _: {'odds':{}})
    report = coverage_report(path,now=T0+timedelta(days=1))['latest_run']
    assert report['queried']==2 and report['pending_considered']==5
    assert report['decisions']=={'sem_odd_over_25':2,'run_call_limit':2,'non_numeric_id':1}


@pytest.mark.parametrize('quote,expected',[
    (None,'sem_timestamp_da_cotacao'),
    ('bad-date','timestamp_malformado'),
    ('2026-11-13T08:00:00','timestamp_sem_timezone'),
    ('2026-11-13T09:00:00+00:00','cotacao_futura'),
    ('2026-11-12T23:00:00+00:00','cotacao_antiga'),
])
def test_failed_quote_replay_is_idempotent_and_diagnosed(timeline,quote,expected):
    path,game,save = timeline
    save()
    client=Mock(return_value={'odds':{'over_25_goals':2},'last_update_at':quote})
    capturar(path,now=T0+timedelta(minutes=1),delay=0,api_client=client)
    before=state(path)
    capturar(path,now=T0+timedelta(minutes=1),delay=0,api_client=client)
    assert client.call_count==1 and state(path)==before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT decision FROM odds_capture_audit').fetchone()==(expected,)


@pytest.mark.parametrize('has_date',[False,True])
def test_postponed_status_never_uses_old_start_to_void_or_stale(timeline,has_date):
    path,game,save = timeline
    save()
    new = game['timestamp']+4*86400
    payload={'status':'postponed'}
    if has_date:
        payload['event_date']=datetime.fromtimestamp(new,timezone.utc).isoformat()
    def result(*args,**kwargs):
        kwargs['schedule_observations']['101']=payload
        return None,None,'postponed'
    with patch.object(settlement,'DB_NAME',str(path)),patch.object(settlement,'API_TOKEN','mock'),patch.object(settlement,'DELAY',0),patch.object(settlement,'datetime') as clock,patch.object(settlement,'consultar_resultado_api',side_effect=result):
        clock.now.return_value=T0+timedelta(days=3)
        settlement.resolver_jogos()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT status FROM predictions').fetchone()==('pending',)
        assert current(db,'101')[2]==('applied' if has_date else 'quarantined')


@pytest.mark.parametrize('code,reason',[(429,'quota_http_429'),(503,'http_503')])
def test_bsd_errors_do_not_masquerade_as_missing_price(code,reason):
    response = Mock(status_code=code,headers={})
    with patch.object(api.requests,'get',return_value=response),patch.object(api.time,'sleep'),patch.object(api,'CACHE',{}):
        with pytest.raises(api.BSDRequestError,match=reason):
            api.obter_odds('101')


def test_production_copy_preserves_history_metrics_csv_and_flags(tmp_path):
    path = tmp_path/'copy.db'
    shutil.copyfile(ROOT/'predictions.db',path)
    original_hash = hashlib.sha256((ROOT/'predictions.db').read_bytes()).hexdigest()
    before = state(path)
    original_rows = load_rows(path)
    database.init_db(path)
    after = state(path)
    assert not compare(before,after)
    assert simular(load_rows(path))==simular(original_rows)
    # Every existing original column/value, including pending forecasts, survives.
    for table,old in before['tables'].items():
        new = after['tables'][table]
        for key,row in old['rows'].items():
            assert row==[new['rows'][key][new['columns'].index(c)] for c in old['columns']]
    with sqlite3.connect(path) as db:
        expected_late = db.execute('SELECT COUNT(*) FROM predictions WHERE unixepoch(created_at)>=timestamp').fetchone()[0]
        assert db.execute('SELECT COUNT(*) FROM legacy_prediction_flags').fetchone()==(expected_late,)
        if 'kickoff_revisions' not in before['counts']:
            assert db.execute("SELECT COUNT(*) FROM kickoff_revisions WHERE state='quarantined' AND new_kickoff IS NULL").fetchone()==(6,)
        legacy_ids = {r[0] for r in db.execute('SELECT match_id FROM legacy_prediction_flags')}
    assert not audit(path)['errors']
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    database.init_db(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    gerar(path,tmp_path/'roi.html',tmp_path/'roi.csv')
    with (ROOT/'roi_jogos.csv').open(encoding='utf-8-sig',newline='') as f:
        original = list(csv.DictReader(f))
    with (tmp_path/'roi.csv').open(encoding='utf-8-sig',newline='') as f:
        exported = list(csv.DictReader(f))
    assert [{k:r[k] for k in original[0]} for r in exported]==original
    assert hashlib.sha256((ROOT/'predictions.db').read_bytes()).hexdigest()==original_hash
    assert not {r['match_id'] for r in load_rows(path,'prospective') if r['kickoff_revision_id'] is None} & legacy_ids


def test_production_coverage_denominators_are_consistent():
    report = coverage_report(ROOT/'predictions.db',datetime(2026,11,13,12,50,40,tzinfo=timezone.utc))
    assert report['eligible']<=report['queried']<=report['settled']
    assert sum(report['event_reasons'].values())==report['settled']
    assert sum(r['settled'] for r in report['by_league'].values())==report['settled']
    assert sum(r['settled'] for r in report['by_day_utc'].values())==report['settled']


def test_failed_schedule_migration_rolls_back_all_new_tables(tmp_path):
    path = tmp_path/'copy.db'
    shutil.copyfile(ROOT/'predictions.db',path)
    before = state(path)
    from pipeline.schedule_migration import migrate_schedule
    def fail(db):
        migrate_schedule(db)
        raise RuntimeError('crash after additive migration')
    with patch('pipeline.schedule_migration.migrate_schedule',side_effect=fail):
        with pytest.raises(RuntimeError):
            database.init_db(path)
    assert state(path)==before
    with sqlite3.connect(path) as db:
        assert bool(db.execute("SELECT name FROM sqlite_master WHERE name='operational_schedule'").fetchone()) == ('kickoff_revisions' in before['counts'])


def test_pages_observes_automatic_build_and_requests_fallback_once():
    def response(sha,status):
        r=Mock(); r.json.return_value={'commit':sha,'status':status}; return r
    post=Mock()
    wait_for_build('o/r','new','token',get=Mock(side_effect=[response('new','building'),response('new','built')]),
                   request_build=post,sleep=lambda _:None,fallback_after=0)
    post.assert_not_called()
    post=Mock()
    wait_for_build('o/r','new','token',get=Mock(side_effect=[response('old','built'),response('old','built'),response('new','built')]),
                   request_build=post,sleep=lambda _:None,fallback_after=0)
    post.assert_called_once()
