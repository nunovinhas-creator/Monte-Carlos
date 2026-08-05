import sqlite3
import os

def calculate_brackets(data, prob_index, result_index):
    """
    Agrupa previsões por intervalos de probabilidade e calcula a taxa de acerto.
    """
    brackets = {
        "50% - 59%": {"total": 0, "hits": 0},
        "60% - 69%": {"total": 0, "hits": 0},
        "70% - 79%": {"total": 0, "hits": 0},
        "80%+":       {"total": 0, "hits": 0}
    }
    
    for row in data:
        prob = row[prob_index]
        hit = row[result_index]
        
        if prob is None or hit is None:
            continue
            
        prob_val = float(prob)
        if prob_val >= 80:
            b_key = "80%+"
        elif prob_val >= 70:
            b_key = "70% - 79%"
        elif prob_val >= 60:
            b_key = "60% - 69%"
        elif prob_val >= 50:
            b_key = "50% - 59%"
        else:
            continue
            
        brackets[b_key]["total"] += 1
        if int(hit) == 1:
            brackets[b_key]["hits"] += 1
            
    return brackets

def build_table_rows(brackets):
    html = ""
    for bracket, stats in brackets.items():
        total = stats["total"]
        hits = stats["hits"]
        rate = (hits / total * 100) if total > 0 else 0.0
        html += f"""
        <tr>
            <td><strong>{bracket}</strong></td>
            <td>{total}</td>
            <td>{hits}</td>
            <td><span class="badge {'bg-success' if rate >= 60 else 'bg-warning'}">{rate:.1f}%</span></td>
        </tr>
        """
    return html

def main():
    total_finished = 0
    over25_rows = ""
    btts_rows = ""

    if os.path.exists("predictions.db"):
        conn = sqlite3.connect("predictions.db")
        cursor = conn.cursor()

        # Verificar se as colunas necessárias existem
        cursor.execute("PRAGMA table_info(predictions);")
        columns = [col[1] for col in cursor.fetchall()]

        required_cols = ['prob_over25', 'prob_btts', 'status', 'settled_over25', 'settled_btts']
        if all(col in columns for col in required_cols):
            cursor.execute("""
                SELECT prob_over25, prob_btts, settled_over25, settled_btts 
                FROM predictions 
                WHERE status = 'finished' AND settled_over25 IS NOT NULL
            """)
            rows = cursor.fetchall()
            if rows:
                total_finished = len(rows)
                over25_brackets = calculate_brackets(rows, 0, 2)
                btts_brackets = calculate_brackets(rows, 1, 3)
                over25_rows = build_table_rows(over25_brackets)
                btts_rows = build_table_rows(btts_brackets)

        conn.close()

    html_content = f"""<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backtest & Métrica Monte Carlo</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ background-color: #f8f9fa; font-family: system-ui, -apple-system, sans-serif; }}
        .card {{ border-radius: 12px; border: none; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        .badge {{ font-size: 0.9rem; padding: 0.5em 0.75em; }}
    </style>
</head>
<body class="py-4">
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h2 class="fw-bold mb-0">📊 Relatório de Backtest</h2>
                <p class="text-muted mb-0">Validação de acerto do modelo Monte Carlo em jogos finalizados</p>
            </div>
            <a href="index.html" class="btn btn-outline-primary">Ver Previsões</a>
        </div>

        <div class="alert alert-info d-flex align-items-center mb-4">
            <div><strong>Amostra Analisada:</strong> {total_finished} jogos liquidados na base de dados.</div>
        </div>

        <div class="row g-4">
            <!-- Over 2.5 -->
            <div class="col-md-6">
                <div class="card p-3">
                    <h5 class="card-title fw-bold text-primary mb-3">⚽ Mais de 2.5 Golos (Over 2.5)</h5>
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead class="table-light">
                                <tr>
                                    <th>Intervalo Modelo</th>
                                    <th>Apostas</th>
                                    <th>Acertos</th>
                                    <th>Taxa de Acerto</th>
                                </tr>
                            </thead>
                            <tbody>
                                {over25_rows if over25_rows else '<tr><td colspan="4" class="text-center text-muted py-3">Aguardando liquidação de jogos...</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- BTTS -->
            <div class="col-md-6">
                <div class="card p-3">
                    <h5 class="card-title fw-bold text-primary mb-3">🥊 Ambas Marcam (BTTS)</h5>
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead class="table-light">
                                <tr>
                                    <th>Intervalo Modelo</th>
                                    <th>Apostas</th>
                                    <th>Acertos</th>
                                    <th>Taxa de Acerto</th>
                                </tr>
                            </thead>
                            <tbody>
                                {btts_rows if btts_rows else '<tr><td colspan="4" class="text-center text-muted py-3">Aguardando liquidação de jogos...</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

    with open("backtest.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    print("✅ Página 'backtest.html' gerada com sucesso!")

if __name__ == "__main__":
    main()
