"""
recalibracao.py

Ajusta uma recalibracao logistica simples (Platt scaling) sobre o
log-odds das previsoes do modelo, uma por mercado (Over 2.5 e BTTS):

    recalibrado = sigmoid(a + b * logit(prob_modelo))

Regra critica: nunca se ajusta e avalia na mesma amostra -- isso daria
um skill otimista (in-sample) e inutil para decidir se a recalibracao
generaliza para jogos futuros. A separacao treino/teste e, por ordem
de preferencia:

  1. modelo = 'v2' com N liquidado >= MIN_N_V2_FIT: treino = primeiros
     70% (cronologico) das previsoes v2, teste = ultimos 30%. E o unico
     modo cujos coeficientes servem para aplicar ao v2.
  2. coluna modelo preenchida mas v2 com N baixo: treino = modelo 'v1',
     teste = modelo 'v2'. Os coeficientes descrevem o v1 -- so
     diagnostico, NAO aplicar ao v2.
  3. sem coluna modelo: treino = created_at < DATA_CORTE, teste =
     created_at >= DATA_CORTE (comportamento antigo).

Este script e so um diagnostico: guarda os coeficientes em
recalibracao.json e imprime a comparacao de skill, mas NAO altera
main.py, backtest.py nem as previsoes gravadas. Aplicar a
recalibracao a previsoes reais fica para depois de haver amostra de
avaliacao (pos-corte) suficiente para confiar no resultado.
"""

import json
import math
from datetime import datetime, timezone

from backtest import DATA_CORTE, IDX_MODELO, brier_stats, carregar_dados

EPS = 1e-6
MAX_ITER = 50
TOL = 1e-8

OUTPUT_JSON = "recalibracao.json"

# N minimo de previsoes v2 liquidadas para ajustar Platt so no v2.
MIN_N_V2_FIT = 300
FRACAO_TREINO_V2 = 0.7


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


def avaliar_mercado(nome, treino_rows, teste_rows, idx_prob, idx_res,
                    desc_treino, desc_teste):
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
    print(f"Treino ({desc_treino}): n={len(pares_treino)}")
    print(f"Teste  ({desc_teste}): n={len(pares_teste)}")

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
            "AVISO: sem previsoes liquidadas na amostra de teste -- "
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

    print(f"Amostra liquidada total: {len(rows)} (de {total_registos} previsoes, {total_finished} 'finished')")
    print(f"DATA_CORTE = {DATA_CORTE}")

    tem_modelo = any(r[IDX_MODELO] is not None for r in rows)
    rows_v2 = [r for r in rows if r[IDX_MODELO] == "v2"]  # ja por timestamp

    if tem_modelo and len(rows_v2) >= MIN_N_V2_FIT:
        modo = "v2_split"
        corte = int(len(rows_v2) * FRACAO_TREINO_V2)
        treino_rows, teste_rows = rows_v2[:corte], rows_v2[corte:]
        desc_treino = f"modelo='v2', primeiros {FRACAO_TREINO_V2:.0%}"
        desc_teste = f"modelo='v2', ultimos {1 - FRACAO_TREINO_V2:.0%}"
    elif tem_modelo:
        modo = "v1_treino_v2_teste"
        treino_rows = [r for r in rows if r[IDX_MODELO] == "v1"]
        teste_rows = rows_v2
        desc_treino = "modelo='v1'"
        desc_teste = "modelo='v2'"
        print(
            f"AVISO: so {len(rows_v2)} previsoes v2 liquidadas (< {MIN_N_V2_FIT}). "
            "Nao ha N para ajustar Platt no v2: os coeficientes abaixo sao "
            "ajustados no v1 e servem so de diagnostico -- NAO os aplique ao v2."
        )
    else:
        modo = "data_corte"
        treino_rows = [r for r in rows if r[4] is not None and r[4] < DATA_CORTE]
        teste_rows = [r for r in rows if r[4] is not None and r[4] >= DATA_CORTE]
        desc_treino = f"created_at < {DATA_CORTE}"
        desc_teste = f"created_at >= {DATA_CORTE}"
        print("AVISO: coluna modelo vazia -- a separar por DATA_CORTE.")

    print(f"Modo treino/teste: {modo} (v2 liquidadas: {len(rows_v2)}, minimo para fit v2: {MIN_N_V2_FIT})")

    resultado_o25 = avaliar_mercado("Over 2.5", treino_rows, teste_rows, 0, 2, desc_treino, desc_teste)
    resultado_btts = avaliar_mercado("BTTS", treino_rows, teste_rows, 1, 3, desc_treino, desc_teste)

    saida = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "data_corte": DATA_CORTE,
        "modo": modo,
        "n_v2_liquidado": len(rows_v2),
        "min_n_v2_fit": MIN_N_V2_FIT,
        "aplicado": False,
        "over_25": resultado_o25,
        "btts": resultado_btts,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(saida, f, indent=2, ensure_ascii=False)

    print(f"\nOK: coeficientes guardados em '{OUTPUT_JSON}'. Nada foi aplicado a previsoes existentes.")


if __name__ == "__main__":
    main()
