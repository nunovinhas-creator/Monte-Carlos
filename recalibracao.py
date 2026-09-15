"""
recalibracao.py

Ajusta uma recalibracao logistica simples (Platt scaling) sobre o
log-odds das previsoes do modelo, uma por mercado (Over 2.5 e BTTS):

    recalibrado = sigmoid(a + b * logit(prob_modelo))

Regra critica: o ajuste (fit) usa SO previsoes com created_at <
DATA_CORTE; a avaliacao (skill) usa SO previsoes com created_at >=
DATA_CORTE. Nunca se ajusta e avalia na mesma amostra -- isso daria
um skill otimista (in-sample) e inutil para decidir se a recalibracao
generaliza para jogos futuros.

Este script e so um diagnostico: guarda os coeficientes em
recalibracao.json e imprime a comparacao de skill, mas NAO altera
main.py, backtest.py nem as previsoes gravadas. Aplicar a
recalibracao a previsoes reais fica para depois de haver amostra de
avaliacao (pos-corte) suficiente para confiar no resultado.
"""

import json
import math
from datetime import datetime, timezone

from backtest import DATA_CORTE, brier_stats, carregar_dados

EPS = 1e-6
MAX_ITER = 50
TOL = 1e-8

OUTPUT_JSON = "recalibracao.json"


def logit(p):
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(z):
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def ajustar_platt(pares, max_iter=MAX_ITER, tol=TOL):
    """
    Regressao logistica de 1 variavel (Platt scaling) via IRLS
    (Newton-Raphson), sem dependencias externas: ajusta a, b tal que
    sigmoid(a + b * logit(p)) aproxime o resultado real.

    pares: lista de (prob_0_a_1, resultado_0_ou_1).
    Devolve (a, b, n_iteracoes).
    """
    xs = [logit(p) for p, _ in pares]
    ys = [float(y) for _, y in pares]

    a, b = 0.0, 1.0  # ponto de partida = recalibracao identidade

    for it in range(1, max_iter + 1):
        soma_w = soma_wx = soma_wxx = soma_wz = soma_wxz = 0.0

        for x, y in zip(xs, ys):
            eta = a + b * x
            mu = sigmoid(eta)
            w = max(mu * (1.0 - mu), 1e-10)
            z = eta + (y - mu) / w

            soma_w += w
            soma_wx += w * x
            soma_wxx += w * x * x
            soma_wz += w * z
            soma_wxz += w * x * z

        det = soma_w * soma_wxx - soma_wx * soma_wx
        if abs(det) < 1e-12:
            break

        novo_a = (soma_wxx * soma_wz - soma_wx * soma_wxz) / det
        novo_b = (soma_w * soma_wxz - soma_wx * soma_wz) / det

        convergiu = abs(novo_a - a) < tol and abs(novo_b - b) < tol
        a, b = novo_a, novo_b

        if convergiu:
            return a, b, it

    return a, b, max_iter


def recalibrar_pares(pares, a, b):
    return [(sigmoid(a + b * logit(p)), y) for p, y in pares]


def skill(brier, base):
    if brier is None or not base or base <= 0:
        return None
    return (1 - brier / base) * 100


def pares_mercado(rows, idx_prob, idx_res):
    return [
        (float(r[idx_prob]) / 100.0, int(r[idx_res]))
        for r in rows
        if r[idx_prob] is not None and r[idx_res] is not None
    ]


def avaliar_mercado(nome, treino_rows, teste_rows, idx_prob, idx_res):
    pares_treino = pares_mercado(treino_rows, idx_prob, idx_res)
    pares_teste = pares_mercado(teste_rows, idx_prob, idx_res)

    resultado = {
        "mercado": nome,
        "n_treino": len(pares_treino),
        "n_teste": len(pares_teste),
        "a": None,
        "b": None,
        "iteracoes": None,
        "avaliacao": None,
    }

    print(f"\n=== {nome} ===")
    print(f"Treino (created_at < {DATA_CORTE}): n={len(pares_treino)}")
    print(f"Teste  (created_at >= {DATA_CORTE}): n={len(pares_teste)}")

    if not pares_treino:
        print("Sem dados de treino -- nao e possivel ajustar coeficientes.")
        return resultado

    a, b, it = ajustar_platt(pares_treino)
    resultado["a"] = a
    resultado["b"] = b
    resultado["iteracoes"] = it
    print(f"Coeficientes (ajustados so no treino): a={a:.4f}  b={b:.4f}  ({it} iteracoes)")

    if not pares_teste:
        print(
            "AVISO: sem previsoes liquidadas desde a data de corte -- "
            "nao ha amostra de avaliacao fora da amostra de treino. "
            "Skill NAO calculado (evita medir em cima do proprio treino)."
        )
        return resultado

    brier_atual, base, n = brier_stats(pares_teste)
    pares_teste_recal = recalibrar_pares(pares_teste, a, b)
    brier_recal, base_recal, _ = brier_stats(pares_teste_recal)

    skill_atual = skill(brier_atual, base)
    skill_recal = skill(brier_recal, base)

    resultado["avaliacao"] = {
        "n": n,
        "baseline": base,
        "brier_atual": brier_atual,
        "skill_atual": skill_atual,
        "brier_recalibrado": brier_recal,
        "skill_recalibrado": skill_recal,
    }

    print(f"Avaliacao fora da amostra (n={n}, baseline={base:.4f}):")
    print(f"  Modelo atual        -> Brier {brier_atual:.4f}  skill {skill_atual:+.1f}%")
    print(f"  Recalibrado (Platt) -> Brier {brier_recal:.4f}  skill {skill_recal:+.1f}%")

    delta = skill_recal - skill_atual
    if delta > 0:
        print(f"  Recalibracao melhoraria o skill em {delta:+.1f} pontos percentuais.")
    else:
        print(f"  Recalibracao NAO melhoraria o skill ({delta:+.1f} pontos percentuais).")

    return resultado


def main():
    rows, total_registos, total_finished = carregar_dados()

    sem_data = [r for r in rows if r[4] is None]
    if sem_data:
        print(f"AVISO: {len(sem_data)} registo(s) liquidado(s) sem created_at -- excluidos do treino/teste.")

    treino_rows = [r for r in rows if r[4] is not None and r[4] < DATA_CORTE]
    teste_rows = [r for r in rows if r[4] is not None and r[4] >= DATA_CORTE]

    print(f"Amostra liquidada total: {len(rows)} (de {total_registos} previsoes, {total_finished} 'finished')")
    print(f"DATA_CORTE = {DATA_CORTE}")

    resultado_o25 = avaliar_mercado("Over 2.5", treino_rows, teste_rows, 0, 2)
    resultado_btts = avaliar_mercado("BTTS", treino_rows, teste_rows, 1, 3)

    saida = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "data_corte": DATA_CORTE,
        "aplicado": False,
        "over_25": resultado_o25,
        "btts": resultado_btts,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(saida, f, indent=2, ensure_ascii=False)

    print(f"\nOK: coeficientes guardados em '{OUTPUT_JSON}'. Nada foi aplicado a previsoes existentes.")


if __name__ == "__main__":
    main()
