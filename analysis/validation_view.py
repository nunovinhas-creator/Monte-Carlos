"""Additional scientific tables without pooling distinct forecast definitions."""
from datetime import datetime, timezone
from html import escape

from analysis.metrics import scores, block_interval
from analysis.observations import prediction_rows
from analysis.roi_report import load_rows


def render(db_path, legacy):
    out = ['<section class="card p-3 mb-4"><h3>Definições e métricas científicas</h3>',
           '<p>Histórico de predictions = vista legada mutável antes do jogo; created_at não prova a hora da última previsão. Primeira, última pré-jogo e congelada na odd são séries separadas. Não se reconstroem snapshots antigos. N/D = comparação indisponível.</p>',
           '<div class="table-responsive"><table class="table"><thead><tr><th>Série</th><th>Mercado</th><th>n</th><th>Período UTC</th><th>Brier</th><th>Log Loss</th><th>Skill vs 0,5</th><th>ECE</th><th>Brier IC95%</th></tr></thead><tbody>']
    groups = []
    for model in ('v1','v2'):
        groups.append((f'Histórico legado {model}', [dict(prob_o25=r[0],prob_btts=r[1],result_o25=r[2],result_btts=r[3],timestamp=r[6]) for r in legacy if r[7]==model]))
    for definition,label in [('first','Primeira observada'),('last','Última observada pré-jogo')]:
        values = prediction_rows(db_path,definition)
        models = sorted({r['modelo'] for r in values})
        if not models:
            groups.append((label,[]))
        for model in models:
            groups.append((f'{label} {model}',[r for r in values if r['modelo']==model]))
    groups.append(('Congelada na primeira odd válida (v2 histórica inferida quando ausente)',load_rows(db_path)))
    for name, values in groups:
        for field,label in [('o25','Over 2,5'),('btts','BTTS')]:
            rr = [r for r in values if r.get('prob_'+field) is not None and r.get('result_'+field) in (0,1)]
            if not rr:
                out.append(f'<tr><td>{escape(name)}</td><td>{label}</td><td>0</td><td colspan="6">N/D</td></tr>')
                continue
            p = [r['prob_'+field]/100 for r in rr]; y=[r['result_'+field] for r in rr]
            m = scores(p,y)
            ci = block_interval([(a-b)**2 for a,b in zip(p,y)],[r['timestamp'] for r in rr])
            uncertainty = 'N/D' if ci is None else f'[{ci[0]:.4f}; {ci[1]:.4f}]'
            first,last = [datetime.fromtimestamp(f(r['timestamp'] for r in rr),timezone.utc).date().isoformat() for f in (min,max)]
            out.append(f'<tr><td>{escape(name)}</td><td>{label}</td><td>{len(rr)}</td><td>{first} a {last}</td><td>{m["brier"]:.4f}</td><td>{m["log_loss"]:.4f}</td><td>{m["skill"]:+.1%}</td><td>{m["ece"]:.4f}</td><td>{uncertainty}</td></tr>')
    out += ['</tbody></table></div>',
            '<p>IC95% Brier: bootstrap por dia UTC, 1000 réplicas, aproximado. ECE usa faixas fixas; faixas e amostras distintas não são validações independentes. A comparação v1/v2 cobre períodos diferentes.</p>',
            '<p>Os blocos legados abaixo preservam Brier/skill histórico contra a taxa da própria amostra (baseline descritivo que usa os resultados). O baseline por liga foi corrigido para só incluir jogos com início pelo menos 48h antes. Como não existe hora de liquidação antiga, este atraso é uma hipótese retrospectiva, não prova de disponibilidade em tempo real. Ligas desconhecidas não partilham histórico.</p>',
            '<p>Estudo offline reproduzível: <a href="research/results_2026-11/CALIBRACAO_2026-11.md">relatório de calibração</a>. Calibração de produção desligada; nenhum ganho económico demonstrado.</p>',
            '<h4>Evolução mensal v1/v2 (histórico legado)</h4><table class="table"><thead><tr><th>Modelo</th><th>Mês UTC</th><th>n Over</th><th>Brier</th><th>Log Loss</th></tr></thead><tbody>']
    for model in ('v1','v2'):
        months = sorted({datetime.fromtimestamp(r[6],timezone.utc).strftime('%Y-%m') for r in legacy if r[7]==model})
        for month in months:
            rows = [r for r in legacy if r[7]==model and datetime.fromtimestamp(r[6],timezone.utc).strftime('%Y-%m')==month]
            m = scores([r[0]/100 for r in rows],[r[2] for r in rows])
            out.append(f'<tr><td>{model}</td><td>{month}</td><td>{m["n"]}</td><td>{m["brier"]:.4f}</td><td>{m["log_loss"]:.4f}</td></tr>')
    out.append('</tbody></table></section>')
    return '\n'.join(out)
