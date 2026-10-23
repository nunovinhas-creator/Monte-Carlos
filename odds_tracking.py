"""Registo prospectivo de odds Over 2,5 BSD (consenso, NÃO cotação executável).

Uma cotação por evento antes do apito inicial, no máximo 6 horas antes.
Não se fabricam preços históricos. Sem API/token, guardamos 'indisponivel'.
"""
from __future__ import annotations
import math
import os
import sqlite3
import time
from datetime import datetime, timezone

from api import obter_odds
from database import DB_NAME, init_db

FONTE = 'bsd_consensus'
JANELA_HORAS = 6


def parse_quote(payload):
    """Estrutura documentada BSD: {'odds': {'over_25_goals': 1.87}, 'last_update_at': ...}."""
    if not isinstance(payload, dict) or not isinstance(payload.get('odds'), dict):
        return None, None, 'resposta_sem_objeto_odds'
    odds = payload['odds']
    value = odds.get('over_25_goals')
    if isinstance(value, bool):
        return None, None, 'valor_odd_invalido'
    try:
        decimal = float(value)
    except (TypeError, ValueError):
        return None, None, 'sem_odd_over_25'
    if not math.isfinite(decimal) or not 1.01 <= decimal <= 100:
        return None, None, 'valor_odd_invalido'
    timestamp = payload.get('last_update_at')
    if not isinstance(timestamp, str) or not timestamp:
        return None, None, 'sem_timestamp_da_cotacao'
    try:
        quoted_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if quoted_at.tzinfo is None:
            return None, None, 'timestamp_sem_timezone'
    except ValueError:
        return None, None, 'timestamp_malformado'
    return decimal, quoted_at.astimezone(timezone.utc), 'ok'


def capturar(db_path=DB_NAME, max_calls=None, delay=None, api_client=obter_odds, now=None):
    """Não repetir jogos com odds válidas. Só eventos com ID numérico para a API BSD."""
    init_db() if db_path == DB_NAME else None
    max_calls = int(max_calls if max_calls is not None else os.getenv('ODDS_MAX_CALLS', '80'))
    delay = float(delay if delay is not None else os.getenv('ODDS_DELAY', '0.1'))
    fixed_now = now
    now = now or datetime.now(timezone.utc)
    now_ts = int(now.timestamp())
    db = sqlite3.connect(db_path)
    results = {'tentativas': 0, 'capturadas': 0, 'sem_odd': 0}
    try:
        jogos = db.execute('''
            SELECT p.match_id, p.timestamp, p.prob_o25, p.prob_o25_base, p.calibracao_o25_id FROM predictions p
            WHERE p.modelo = 'v2' AND p.status = 'pending' AND p.result_o25 IS NULL
              AND p.timestamp > ? AND p.timestamp <= ?
              AND p.created_at IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM odds_snapshots s WHERE s.match_id = p.match_id
                  AND s.fonte = ? AND s.estado = 'ok'
              )
            ORDER BY p.timestamp ASC, p.match_id ASC
            LIMIT ?
        ''', (now_ts, now_ts + JANELA_HORAS * 3600, FONTE, max_calls)).fetchall()
        for match_id, kickoff, prob, prob_base, cal_id in jogos:
            # Para não associar odds de jogos diferentes via IDs fallback.
            if not str(match_id).isdigit():
                continue
            observed = fixed_now or datetime.now(timezone.utc)
            # Verificar novamente kickoff real no momento da chamada.
            if observed.timestamp() >= kickoff:
                continue
            try:
                payload = api_client(match_id)
                odd, quote_time, estado = parse_quote(payload)
                if quote_time is not None:
                    idade = (observed - quote_time).total_seconds()
                    if idade < 0 or idade > 6 * 3600 or quote_time.timestamp() >= kickoff:
                        odd, estado = None, 'cotacao_desatualizada_ou_futura'
            except Exception as exc:
                quote_time, odd, estado = None, None, f'erro_api:{type(exc).__name__}'
            db.execute('''
                INSERT INTO odds_snapshots
                (match_id, capturado_em, quote_at, fonte, mercado, odd, estado, detalhe,
                 prob_o25_no_instante, prob_o25_base_no_instante, calibracao_o25_no_instante)
                VALUES (?, ?, ?, ?, 'over_25_goals', ?, ?, ?, ?, ?, ?)
            ''', (match_id, observed.isoformat(), quote_time.isoformat() if quote_time else None,
                  FONTE, odd, estado, 'consenso não executável' if odd else estado,
                  prob, prob_base if prob_base is not None else prob, cal_id))
            db.commit()
            results['tentativas'] += 1
            results['capturadas' if estado == 'ok' else 'sem_odd'] += 1
            if delay > 0:
                time.sleep(delay)
    finally:
        db.close()
    print('ODDS BSD (consenso) — ' + ', '.join(f'{k}={v}' for k, v in results.items()))
    return results


if __name__ == '__main__':
    capturar()