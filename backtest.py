"""
backtest.py

Relatorio de calibracao do modelo sobre jogos ja liquidados.

Invariante: nenhum estado de erro pode parecer sucesso.
Se as colunas nao existirem ou a query falhar, o script sai com codigo != 0.
Amostra vazia NAO e erro (e um estado valido no arranque), mas e sinalizada
de forma explicita no HTML.
"""

import math
import os
import sqlite3
import sys

DB_NAME = os.getenv("PREDICTIONS_DB", "predictions.db")

REQUIRED_COLS = [
    "prob_o25",
    "prob_btts",
    "status",
    "result_o25",
    "result_btts",
]

BRACKETS = [
    ("0% - 39%", 0.0, 40.0),
    ("40% - 49%", 40.0, 50.0),
    ("50% - 59%", 50.0, 60.0),
    ("60% - 69%", 60.0, 70.0),
    ("70% - 79%", 70.0, 80.0),
    ("80%+", 80.0, 101.0),
]


def calculate_brackets(data, prob_index, result_index):
    """
    Agrupa previsoes por intervalo de probabilidade.

    Devolve, por bracket: total, hits, taxa real e probabilidade media
    prevista pelo modelo (para comparar previsto vs realizado).
    """
    out = {
        label: {"total": 0, "hits": 0, "soma_prob": 0.0}
        for label, _, _ in BRACKETS
    }

    for row in data:
        prob = row[prob_index]
        hit = row[result_index]

        if prob is None or hit is None:
            continue

        prob_val = float(prob)

        for label, lo, hi in BRACKETS:
            if lo <= prob_val < hi:
                out[label]["total"] += 1
                out[label]["soma_prob"] += prob_val
                if int(hit) == 1:
                    out[label]["hits"] += 1
                break

    return out


def brier_score(data, prob_index, result_index):
    """
    Brier score: media de (prob - resultado)^2. Menor e melhor.
    Baseline util: prever sempre a taxa base da amostra.
    """
    pares = [
        (float(r[prob_index]) / 100.0, int(r[result_index]))
        for r in data
        if r[prob_index] is not None and r[result_index] is not None
    ]

    if not pares:
        return None, None, 0

    n = len(pares)
    brier = sum((p - y) ** 2 for p, y in pares) / n

    base = sum(y for _, y in pares) / n
    brier_base = sum((base - y) ** 2 for _, y in pares) / n

    return brier, brier_base, n


def build_table_rows(brackets):
    html = ""

    for label, _, _ in BRACKETS:
        stats = brackets[label]
        total = stats["total"]
        hits = stats["hits"]

        if total == 0:
            html += (
                f'<tr class="text-muted"><td><strong>{label}</strong></td>'
                f"<td>0</td><td>0</td><td>&mdash;</td><td>&mdash;</td></tr>"
            )
            continue

        rate = hits / total * 100.0
        previsto = stats["soma_prob"] / total
        desvio = rate - previsto

        # Erro padrao binomial, para nao ler ruido como sinal
        se = math.sqrt(max(rate * (100.0 - rate), 0.0) / total)

        if total < 30:
            cor = "bg-secondary"
        elif abs(desvio) <= 2 * se:
            cor = "bg-success"
        else:
            cor = "bg-warning text-dark"

        html += f"""
        <tr>
            <td><strong>{label}</strong></td>
            <td>{total}</td>
            <td>{hits}</td>
            <td>{previsto:.1f}%</td>
            <td><span class="badge {cor}">{rate:.1f}% ({desvio:+.1f})</span></td>
        </tr>
        """

    return html


def carregar_dados():
    if not os.path.exists(DB_NAME):
        print(f"ERRO: base de dados '{DB_NAME}' nao encontrada.")
        sys.exit(1)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(predictions);")
    columns = [col[1] for col in cursor.fetchall()]

    if not columns:
        print("ERRO: tabela 'predictions' nao existe.")
        conn.close()
        sys.exit(1)

    em_falta = [c for c in REQUIRED_COLS if c not in columns]
    if em_falta:
        print(f"ERRO: colunas em falta no schema: {em_falta}")
        print(f"Colunas existentes: {columns}")
        conn.close()
        sys.exit(1)

    cursor.execute("SELECT COUNT(*) FROM predictions")
    total_registos = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM predictions WHERE status = 'finished'")
    total_finished = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT prob_o25, prob_btts, result_o25, result_btts
        FROM predictions
        WHERE status = 'finished'
          AND result_o25 IS NOT NULL
          AND result_btts IS NOT NULL
        """
    )
    rows = cursor.fetchall()
    conn.close()

    return rows, total_registos, total_finished


def bloco_brier(rows, idx_prob, idx_res, nome):
    brier, base, n = brier_score(rows, idx_prob, idx_res)

    if brier is None:
        return '<span class="text-muted">sem amostra</span>'

    skill = (1 - brier / base) * 100 if base and base > 0 else 0.0
    cor = "text-success" if skill > 0 else "text-danger"

    return (
        f"Brier <strong>{brier:.4f}</strong> | "
        f"baseline {base:.4f} | "
        f'<span class="{cor}">skill {skill:+.1f}%</span> (n={n})'
    )


def main():
    rows, total_registos, total_finished = carregar_dados()

    n = len(rows)

    if n == 0:
        aviso = (
            '<div class="alert alert-warning mb-4">'
            f"<strong>Amostra vazia.</strong> {total_registos} previsoes na base, "
            f"{total_finished} com status 'finished', 0 com resultado liquidado. "
            "O settlement nao esta a resolver jogos &mdash; ver logs do workflow."
            "</div>"
        )
        over25_rows = ""
        btts_rows = ""
        brier_o25 = brier_btts = '<span class="text-muted">sem amostra</span>'
    else:
        aviso = (
            '<div class="alert alert-info mb-4">'
            f"<strong>Amostra:</strong> {n} jogos liquidados "
            f"(de {total_registos} previsoes registadas)."
            "</div>"
        )
        over25_rows = build_table_rows(calculate_brackets(rows, 0, 2))
        btts_rows = build_table_rows(calculate_brackets(rows, 1, 3))
        brier_o25 = bloco_brier(rows, 0, 2, "Over 2.5")
        brier_btts = bloco_brier(rows, 1, 3, "BTTS")

    vazio = (
        '<tr><td colspan="5" class="text-center text-muted py-3">'
        "Sem jogos liquidados.</td></tr>"
    )

    html_content = f"""<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backtest &amp; Calibracao Monte Carlo</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ background-color: #f8f9fa; font-family: system-ui, -apple-system, sans-serif; }}
        .card {{ border-radius: 12px; border: none; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        .badge {{ font-size: 0.85rem; padding: 0.45em 0.6em; }}
        td, th {{ font-size: 0.9rem; }}
    </style>
</head>
<body class="py-4">
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h2 class="fw-bold mb-0">Relatorio de Backtest</h2>
                <p class="text-muted mb-0">Calibracao do modelo em jogos liquidados</p>
            </div>
            <a href="index.html" class="btn btn-outline-primary">Ver Previsoes</a>
        </div>

        {aviso}

        <div class="row g-4">
            <div class="col-md-6">
                <div class="card p-3">
                    <h5 class="card-title fw-bold text-primary mb-1">Mais de 2.5 Golos</h5>
                    <p class="small text-muted mb-3">{brier_o25}</p>
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead class="table-light">
                                <tr>
                                    <th>Intervalo</th>
                                    <th>N</th>
                                    <th>Acertos</th>
                                    <th>Previsto</th>
                                    <th>Real (desvio)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {over25_rows if over25_rows else vazio}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div class="col-md-6">
                <div class="card p-3">
                    <h5 class="card-title fw-bold text-primary mb-1">Ambas Marcam (BTTS)</h5>
                    <p class="small text-muted mb-3">{brier_btts}</p>
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead class="table-light">
                                <tr>
                                    <th>Intervalo</th>
                                    <th>N</th>
                                    <th>Acertos</th>
                                    <th>Previsto</th>
                                    <th>Real (desvio)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {btts_rows if btts_rows else vazio}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <p class="text-muted small mt-4">
            Verde = desvio dentro de 2 erros padrao (calibrado). Amarelo = desvio
            significativo. Cinzento = amostra abaixo de 30, sem leitura possivel.
            Skill positivo significa que o modelo bate a taxa base da propria amostra.
        </p>
    </div>
</body>
</html>
"""

    with open("backtest.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"OK: backtest.html gerado. Amostra liquidada: {n} jogos.")


if __name__ == "__main__":
    main()
