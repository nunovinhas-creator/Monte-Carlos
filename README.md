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

## Odds pré-jogo e ROI (introduzido em outubro/2026)

- `python odds_tracking.py` consulta `/api/v2/events/{id}/odds/` da BSD para os jogos `v2` pendentes que comecem nas próximas 6h. Só utiliza o campo documentado `odds.over_25_goals`; guarda `last_update_at` e hora da recolha em `odds_snapshots`. Exclui quotes futuras/desatualizadas, cotações inválidas, jogos já iniciados e IDs sintéticos. Limite de 80 chamadas/corrida; a cada 2h no workflow. Falhas são identificadas, não preenchidas com odds inventadas.
- `python roi_report.py` gera `roi.html` e `roi_jogos.csv` com todas as partidas terminadas com odd pré-jogo observada. Inclui MC-O70 (probabilidade **base** >=70%), lucro por 1 unidade e EV modelado. **É ROI hipotético ao consenso BSD, não ROI executável numa casa nem lucro realizado.** O consenso não representa preço firme. Sem odds pre-jogo a tabela fica vazia e o ROI é "Sem amostra".
- `python calibration_o25.py --write` é uma operação **manual**, não executada automaticamente. Ajusta coeficientes nas partidas v2 terminadas, faz treino/validação cronológicos sem dividir jogos do mesmo kickoff e regista Brier no holdout. Por segurança, `calibracao_o25_v2.json` indica `ativo=false` se não houver melhoria. Não misturar com o velho `APPLY_RECAL` baseado na v1, que deve permanecer desligado.
- Futuro: para medir ROI realmente executável, integrar uma fonte com odds datadas de **bookmaker individual** e comissionamento real. O endpoint BSD de comparação por casa exige subscrição Football Unlimited; nunca renomear consenso como odds Betfair/Pinnacle.