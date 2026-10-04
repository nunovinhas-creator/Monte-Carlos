# Monte-Carlos

Previsões de futebol (Over 2.5 e BTTS) via Monte Carlo + ratings de equipa.

## Melhorias recentes (modelo v2)

1. **xG multiplicativo** (Maher-style): ataque × defesa × vantagem de casa × forma, ancorado na média da liga — corrige a subestimação sistemática (~2.54 xG vs ~2.91 golos reais).
2. **Dixon-Coles** na simulação: correlação em 0-0 / 1-0 / 0-1 / 1-1 (melhora BTTS).
3. **Calibração**: shrinkage das probs para a taxa base da liga + Platt scaling opcional (`APPLY_RECAL=1`).
4. **Caminho H2H** assimétrico (54/46 casa/fora) em vez de lambdas iguais.
5. **Shrinkage bayesiano** nos ratings de equipas com poucos jogos.

## Correr

```bash
export BSD_API_TOKEN=...
python main.py          # previsões + dashboard
python backtest.py      # relatório de calibração
python recalibracao.py  # ajustar Platt (treino < DATA_CORTE)
```

## Notas de calibração

O histórico mostrava skill negativo (-7% O25, -5% BTTS) porque:
- o xG médio previsto era ~0.35 golos abaixo do real;
- probs baixas subestimavam e as altas sobrestimavam (overconfidence);
- friendlies / taças destroem o skill (considerar filtro ou peso menor).

Após estas mudanças, **re-correr o pipeline em jogos novos** e comparar o bloco "Desde o corte" no `backtest.html`. Não avaliar o skill em previsões antigas gravadas com o modelo v1.
