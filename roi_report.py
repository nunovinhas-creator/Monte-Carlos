"""ROI *teórico* com consenso BSD observado pré-jogo; 1 unidade por jogo.

Não confundir com rentabilidade executável de uma casa. Sem odds, sem ROI.
Dados históricos sem snapshots não são retro-preenchidos.
"""
from __future__ import annotations
import csv
import html
import os
import sqlite3
from datetime import datetime, timezone

from database import DB_NAME, init_db

MAX_AGE_SECONDS = 6 * 3600


def load_rows(db_path=DB_NAME):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute('''
            WITH candidates AS (
                SELECT p.match_id, p.home_team, p.away_team, p.league,
                    p.timestamp, s.prob_o25_no_instante AS prob_o25,
                    s.prob_o25_base_no_instante AS prob_o25_base,
                    s.calibracao_o25_no_instante AS calibracao_o25_id,
                    p.result_o25, p.status, s.odd, s.capturado_em, s.quote_at,
                    s.fonte,
                    ROW_NUMBER() OVER (PARTITION BY p.match_id ORDER BY s.capturado_em DESC, s.id DESC) AS pos
                FROM predictions p JOIN odds_snapshots s ON p.match_id = s.match_id
                WHERE p.modelo = 'v2' AND p.status = 'finished' AND p.result_o25 IN (0,1)
                  AND s.estado = 'ok' AND s.fonte = 'bsd_consensus'
                  AND s.odd > 1.0
                  AND s.prob_o25_no_instante IS NOT NULL
                  AND s.prob_o25_base_no_instante IS NOT NULL
                  AND CAST(strftime('%s', s.capturado_em) AS INTEGER) < p.timestamp
                  AND p.timestamp - CAST(strftime('%s', s.capturado_em) AS INTEGER) <= ?
                  AND CAST(strftime('%s', s.quote_at) AS INTEGER) <= CAST(strftime('%s', s.capturado_em) AS INTEGER) + 300
            ) SELECT * FROM candidates WHERE pos = 1 ORDER BY timestamp, match_id
        ''', (MAX_AGE_SECONDS,))]
    finally:
        conn.close()


def simular(rows):
    if not rows:
        return {'n': 0, 'wins': 0, 'stake': 0, 'profit': 0, 'roi': None, 'avg_odds': None}
    gains = [float(r['odd']) - 1 if r['result_o25'] == 1 else -1 for r in rows]
    profit = sum(gains)
    return {
        'n': len(rows), 'wins': sum(r['result_o25'] for r in rows),
        'stake': len(rows), 'profit': profit, 'roi': profit / len(rows) * 100,
        'avg_odds': sum(r['odd'] for r in rows) / len(rows),
    }


def stats(db_path=DB_NAME):
    rows = load_rows(db_path)
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0]
        quoted = conn.execute("SELECT COUNT(*) FROM odds_snapshots WHERE estado='ok'").fetchone()[0]
        finished_post = conn.execute('''
            SELECT COUNT(*) FROM predictions
            WHERE modelo='v2' AND status='finished' AND result_o25 IN (0,1)
              AND prob_o25_base IS NOT NULL
        ''').fetchone()[0]
    finally:
        conn.close()
    return rows, total, quoted, finished_post


def gerar(db_path=DB_NAME, html_path='roi.html', csv_path='roi_jogos.csv'):
    if db_path == DB_NAME:
        init_db()
    rows, total, quoted, finished_post = stats(db_path)
    all_stats = simular(rows)
    pattern = [r for r in rows if (r.get('prob_o25_base') or 0) >= 70]
    # EV só teórico usando a probabilidade calibrada efetivamente publicada.
    value = [r for r in pattern if r['prob_o25'] / 100 * r['odd'] - 1 >= 0.05]
    summaries = [
        ('Todos com odd', all_stats),
        ('MC-O70: probabilidade BASE ≥70%', simular(pattern)),
        ('MC-O70 e EV modelado ≥5%', simular(value)),
    ]
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as file:
        writer = csv.writer(file)
        writer.writerow(['match_id','liga','casa','fora','kickoff_utc','prob_original_pct',
                         'prob_calibrada_pct','calibracao_id','odd_consenso_bsd','capturado_em_utc',
                         'quote_at_utc','resultado_over25','lucro_teorico_1u','ev_modelado_pct'])
        for r in rows:
            writer.writerow([r['match_id'], r['league'], r['home_team'], r['away_team'],
                             datetime.fromtimestamp(r['timestamp'], tz=timezone.utc).isoformat(),
                             r['prob_o25_base'], r['prob_o25'], r['calibracao_o25_id'], r['odd'],
                             r['capturado_em'], r['quote_at'], r['result_o25'],
                             round(r['odd'] - 1 if r['result_o25'] else -1, 4),
                             round((r['prob_o25'] / 100 * r['odd'] - 1) * 100, 2)])

    def cell_pct(v):
        return f'{v:+.2f}%' if v is not None else 'Sem amostra'

    trs = ''.join('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{:+.2f}u</td><td>{}</td></tr>'.format(
        html.escape(name), t['n'], t['wins'],
        f"{t['avg_odds']:.3f}" if t['avg_odds'] is not None else '—',
        t['profit'], cell_pct(t['roi'])) for name, t in summaries)
    final = f'''<!doctype html><html lang="pt"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Monte Carlos · ROI O2.5</title>
    <style>body{{font-family:system-ui,sans-serif;max-width:1080px;margin:36px auto;padding:0 18px;background:#f8fafc;color:#17243a}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:13px 9px;text-align:left;border-bottom:1px solid #dbe2ec}}.notice{{padding:15px;border:1px solid #f2cf75;background:#fffaf0;border-radius:8px}}a{{color:#2050a2}}</style></head>
    <body><nav><a href="index.html">Previsões</a> · <a href="backtest.html">Calibração</a> · <a href="roi_jogos.csv">Exportar CSV</a></nav>
    <h1>ROI Over 2,5 · registo prospectivo</h1>
    <p class="notice"><strong>Simulação, não lucro real:</strong> a API BSD gratuita publica odds de <em>consenso</em>, que não garantem execução numa casa. As odds são recolhidas antes do jogo; não se retropreenchem cotações antigas. 1 unidade por evento, sem comissão. Não colocar apostas automaticamente.</p>
    <p>Snapshots recolhidos: <b>{total}</b> · com odds válidas: <b>{quoted}</b> · jogos já terminados depois do início do registo: <b>{finished_post}</b> · jogos com odds pré-jogo elegíveis: <b>{len(rows)}</b>.</p>
    <table><thead><tr><th>Estratégia</th><th>Jogos</th><th>Acertos</th><th>Odd média</th><th>Lucro 1u/jogo</th><th>ROI teórico</th></tr></thead>
    <tbody>{trs}</tbody></table>
    <p>MC-O70 usa a probabilidade <strong>base</strong> do modelo v2, antes da calibração adicional. EV modelado = probabilidade publicada × odd − 1. Uma boa taxa de acerto não implica EV positivo.</p>
    <p>Relatório UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}.</p></body></html>'''
    with open(html_path, 'w', encoding='utf-8') as file:
        file.write(final)
    print(f'ROI: {len(rows)} jogos com odd pré-jogo; O70={len(pattern)}, ROI O70={cell_pct(simular(pattern)["roi"])}')
    return summaries


if __name__ == '__main__':
    gerar()