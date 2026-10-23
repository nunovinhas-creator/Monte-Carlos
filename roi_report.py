"""Relatório prospectivo de ROI de Over 2,5 com odds de consenso BSD.

Compara a apresentação original (verde >=60%, não é recomendação de aposta)
com filtros experimentais MC-O70. As classificações relativas ao BTTS e à
confiabilidade usam apenas snapshots capturados ANTES do jogo. Não se recuperam
valores históricos de predictions porque as previsões pendentes são mutáveis.
"""
from __future__ import annotations

import csv
import html
import sqlite3
from datetime import datetime, timezone

from database import DB_NAME, init_db

MAX_AGE_SECONDS = 6 * 3600


def load_rows(db_path=DB_NAME):
    """Uma cotação válida pré-jogo por evento terminado, sem olhar para odds futuras."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute('''
            WITH candidates AS (
                SELECT p.match_id, p.home_team, p.away_team, p.league,
                    p.timestamp, s.prob_o25_no_instante AS prob_o25,
                    s.prob_o25_base_no_instante AS prob_o25_base,
                    s.prob_btts_no_instante AS prob_btts,
                    s.baixa_confianca_no_instante AS baixa_confianca,
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
        return {'n': 0, 'wins': 0, 'stake': 0, 'profit': 0, 'roi': None,
                'avg_odds': None, 'hit_rate': None}
    gains = [float(r['odd']) - 1 if r['result_o25'] == 1 else -1 for r in rows]
    profit = sum(gains)
    wins = sum(r['result_o25'] for r in rows)
    return {
        'n': len(rows), 'wins': wins, 'stake': len(rows),
        'profit': profit, 'roi': profit / len(rows) * 100,
        'avg_odds': sum(r['odd'] for r in rows) / len(rows),
        'hit_rate': wins / len(rows) * 100,
    }


def classificar(row):
    """Não usar valores BTTS/confiabilidade atuais para reclassificar o passado.

    original_over e original_over_btts são None quando falta algum dado no
    snapshot; False significa inelegível. MC-O70 segue a regra já lançada
    (probabilidade base, não exige BTTS nem indicador de confiança).
    """
    o25 = row['prob_o25']
    base = row['prob_o25_base']
    btts = row.get('prob_btts')
    baixa = row.get('baixa_confianca')
    original_over = None if baixa is None else baixa == 0 and o25 >= 60
    original_duplo = None if baixa is None or btts is None else (
        baixa == 0 and o25 >= 60 and btts >= 60
    )
    mc_o70 = base >= 70
    ev = o25 / 100 * row['odd'] - 1
    return {
        'original_over': original_over,
        'original_over_btts': original_duplo,
        'mc_o70': mc_o70,
        'mc_o70_ev5': mc_o70 and ev >= 0.05,
        'ev_pct': ev * 100,
    }


def stats(db_path=DB_NAME):
    rows = load_rows(db_path)
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0]
        quoted = conn.execute("SELECT COUNT(*) FROM odds_snapshots WHERE estado='ok'").fetchone()[0]
    finally:
        conn.close()
    return rows, total, quoted


def gerar(db_path=DB_NAME, html_path='roi.html', csv_path='roi_jogos.csv'):
    if db_path == DB_NAME:
        init_db()
    rows, total, quoted = stats(db_path)
    classes = [(r, classificar(r)) for r in rows]
    original = [r for r, c in classes if c['original_over'] is True]
    original_duplo = [r for r, c in classes if c['original_over_btts'] is True]
    pattern = [r for r, c in classes if c['mc_o70']]
    value = [r for r, c in classes if c['mc_o70_ev5']]
    incompletos = sum(1 for _, c in classes if c['original_over'] is None or
                      c['original_over_btts'] is None)
    summaries = [
        ('Todos os jogos com odd', simular(rows)),
        ('Original: Over verde ≥60%', simular(original)),
        ('Original: Over e BTTS verdes ≥60%', simular(original_duplo)),
        ('MC-O70: Over base ≥70%', simular(pattern)),
        ('MC-O70 + EV modelado ≥5%', simular(value)),
    ]

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as file:
        writer = csv.writer(file)
        writer.writerow([
            'match_id', 'liga', 'casa', 'fora', 'kickoff_utc',
            'prob_o25_base_no_instante_pct', 'prob_o25_publicada_no_instante_pct',
            'prob_btts_no_instante_pct', 'baixa_confianca_no_instante', 'calibracao_id',
            'odd_consenso_bsd', 'capturado_em_utc', 'quote_at_utc', 'resultado_over25',
            'lucro_teorico_1u', 'ev_modelado_pct', 'original_over_verde_60',
            'original_over_e_btts_verdes_60', 'mc_o70', 'mc_o70_ev5'
        ])
        for r, c in classes:
            writer.writerow([
                r['match_id'], r['league'], r['home_team'], r['away_team'],
                datetime.fromtimestamp(r['timestamp'], tz=timezone.utc).isoformat(),
                r['prob_o25_base'], r['prob_o25'], r['prob_btts'],
                r['baixa_confianca'], r['calibracao_o25_id'], r['odd'],
                r['capturado_em'], r['quote_at'], r['result_o25'],
                round(r['odd'] - 1 if r['result_o25'] else -1, 4),
                round(c['ev_pct'], 2),
                'N/D' if c['original_over'] is None else int(c['original_over']),
                'N/D' if c['original_over_btts'] is None else int(c['original_over_btts']),
                int(c['mc_o70']), int(c['mc_o70_ev5'])
            ])

    def percent(v):
        return f'{v:+.2f}%' if v is not None else 'Sem amostra'

    trs = ''.join('''<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>
        <td>{}</td><td>{}</td></tr>'''.format(
        html.escape(name), t['n'], t['wins'],
        f"{t['hit_rate']:.1f}%" if t['hit_rate'] is not None else '—',
        f"{t['avg_odds']:.3f}" if t['avg_odds'] is not None else '—',
        f"{t['profit']:+.2f}u" if t['n'] else '—', percent(t['roi'])
    ) for name, t in summaries)

    final = f'''<!doctype html><html lang="pt"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Monte Carlos · ROI e comparação de estratégias Over 2,5</title>
    <style>
    body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:1100px;margin:30px auto;padding:0 18px;background:#f8fafc;color:#17243a;line-height:1.55}}
    h1{{font-size:1.85rem;margin-bottom:4px}} h2{{font-size:1.3rem;margin-top:30px}} h3{{font-size:1.05rem}}
    .muted{{color:#526176}} .sub{{margin-top:0;color:#526176}} nav{{display:flex;gap:16px;flex-wrap:wrap;padding-bottom:12px}}
    a{{color:#2050a2}} .notice{{padding:14px 16px;border:1px solid #dcc780;background:#fff9e9;border-radius:8px}}
    .card{{background:white;border:1px solid #dce4ee;border-radius:10px;padding:15px 18px;margin:14px 0}}
    .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}
    .stat{{background:white;border:1px solid #dce4ee;border-radius:10px;padding:12px}}
    .stat b{{display:block;font-size:1.65rem}}.stat span{{font-size:.86rem;color:#526176}}
    .scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:760px;background:white}}
    th,td{{padding:12px 9px;text-align:left;border-bottom:1px solid #e0e7ef;vertical-align:top}}
    th{{background:#e9eff8;font-size:.9rem}}tbody tr:hover{{background:#f0f5fa}}
    .hint{{font-size:.9rem;color:#495c73}}
    </style></head><body>
    <nav><a href="index.html">← Previsões</a><a href="backtest.html">Backtest e calibração</a><a href="roi_jogos.csv">Exportar CSV detalhado</a></nav>
    <h1>ROI Over 2,5 · comparação de critérios</h1>
    <p class="sub">O modelo Monte Carlos v2 original mantém-se. Esta página acrescenta medição independente dos filtros experimentais, sem substituir as previsões.</p>
    <div class="notice"><strong>Simulação, não lucro real.</strong> As odds gratuitas BSD são preços de <em>consenso</em>, não ofertas garantidas de uma casa de apostas. O ROI assume uma unidade por jogo, preço registado, sem comissão, e não prova rentabilidade executável. Não existem odds históricas anteriores à recolha.</div>
    <h2>O que existia e o que foi acrescentado</h2>
    <div class="card">
      <h3>Original — destaques verdes de 60%</h3>
      <p>Na <a href="index.html">tabela de previsões</a>, o <strong>Over 2,5 aparece a verde quando a probabilidade publicada é ≥60%</strong> e o <strong>BTTS aparece a verde quando é ≥60%</strong>. Previsões com aviso «sem dados» ficam cinzentas. Estes destaques eram apenas indicadores visuais, <strong>não uma estratégia de aposta com ROI validado</strong>.</p>
      <p>Para testar estes destaques como regras de aposta Over 2,5, comparamos «Original: Over verde ≥60%» e «Original: Over e BTTS verdes ≥60%». O segundo exige as duas probabilidades ≥60% e que o jogo não esteja marcado como baixa confiança.</p>
      <h3>Novo — MC-O70 (experimental)</h3>
      <p>Seleciona jogos cuja <strong>probabilidade base de Over 2,5 é ≥70%</strong>. Não exige BTTS ≥60% e não aplica o indicador de baixa confiança. É um <strong>filtro de investigação independente</strong>, introduzido depois da análise histórica da v2; não substitui o modelo nem a cor original.</p>
      <p>A versão «MC-O70 + EV modelado ≥5%» também exige <strong>probabilidade publicada × odd de consenso − 1 ≥0,05</strong>. O EV é uma estimativa do modelo, <strong>não valor de mercado comprovado</strong>.</p>
      <p class="hint">«Base» = probabilidade v2 antes da recalibração adicional opcional. «Publicada» = probabilidade mostrada no site. A calibração adicional foi mantida desligada por não melhorar a validação temporal; neste estado as duas probabilidades coincidem, mas são guardadas separadamente para preservar o histórico.</p>
    </div>
    <h2>Dados recolhidos e resultados</h2>
    <div class="stats"><div class="stat"><span>Capturas tentadas (acumuladas)</span><b>{total}</b></div>
      <div class="stat"><span>Odds válidas (acumuladas)</span><b>{quoted}</b></div>
      <div class="stat"><span>Jogos terminados com odd elegível</span><b>{len(rows)}</b></div>
      <div class="stat"><span>Liquidados sem BTTS / confiança históricos completos</span><b>{incompletos}</b></div></div>
    <div class="scroll"><table><thead><tr><th>Conjunto de jogos / filtro</th><th>Jogos</th><th>Acertos Over</th><th>Taxa</th><th>Odd média</th><th>Lucro (1u/jogo)</th><th>ROI teórico</th></tr></thead><tbody>{trs}</tbody></table></div>
    <p class="hint">Cada linha é uma simulação independente sobre os mesmos jogos elegíveis quando as variáveis da regra estão disponíveis. As linhas sobrepõem-se: <strong>não se somam lucros entre estratégias</strong>. «Sem amostra» significa que ainda não existem jogos liquidados aptos para aquela regra; não significa ROI de 0%.</p>
    <h2>Metodologia e limitações — para analisar mais tarde</h2>
    <div class="card">
      <p><strong>Unidade de análise:</strong> um jogo terminado da versão v2, com resultado Over 2,5, odd BSD válida e respetiva probabilidade registadas <em>antes</em> do início. Apenas a primeira odd válida guardada por jogo é utilizada (a recolha deixa de repetir jogos com odd válida). Não se inventam odds anteriores à implementação. A janela é de até 6 horas antes do jogo.</p>
      <p><strong>Resultado:</strong> Over 2,5 é verdadeiro quando há três ou mais golos. Lucro por uma unidade: <strong>odd − 1</strong> num acerto; <strong>−1</strong> numa falha. ROI = soma dos lucros ÷ número de jogos selecionados × 100. Sem comissão ou custos de execução.</p>
      <p><strong>Dados em falta:</strong> os primeiros snapshots de outubro de 2026 guardaram Over e odd, mas não congelaram BTTS e o indicador de baixa confiança. Esses jogos <strong>não entram nas linhas «Original»</strong> se estes campos estiverem em falta; continuam disponíveis para «Todos» e MC-O70. Não se usam valores atuais da base de dados para completar snapshots antigos, pois isso introduziria fuga de informação.</p>
      <p><strong>Validade estatística:</strong> as percentagens históricas que motivaram MC-O70 foram obtidas antes deste registo de odds. Servem para formular uma hipótese, não para demonstrar ROI. Comparar amostras posteriores, cobertura, tamanho da amostra, datas, odds médias e estabilidade ao longo do tempo antes de tirar conclusões.</p>
      <p><strong>Rastreabilidade:</strong> o <a href="roi_jogos.csv">CSV</a> disponibiliza jogo, liga, hora UTC, probabilidades no instante, odd, data da cotação, resultado, EV e pertença a cada filtro. «N/D» indica que faltava informação capturada para classificar a regra original.</p>
    </div>
    <p class="muted">Documentação técnica: <a href="https://github.com/nunovinhas-creator/Monte-Carlos/blob/main/docs/METODOLOGIA_ROI.md">metodologia e critérios</a> · <a href="https://github.com/nunovinhas-creator/Monte-Carlos/blob/main/docs/ALTERACOES_2026-10-23.md">alterações de 23/10/2026</a>.</p>
    <p class="muted">Última atualização do relatório (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}.</p>
    </body></html>'''
    with open(html_path, 'w', encoding='utf-8') as file:
        file.write(final)
    print(f'ROI: {len(rows)} jogos com odd pré-jogo; Original 60={len(original)}; '
          f'O70={len(pattern)}; ROI O70={percent(simular(pattern)["roi"])}')
    return summaries


if __name__ == '__main__':
    gerar()