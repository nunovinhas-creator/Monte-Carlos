# Registo de alterações — 23/10/2026

## Implementações realizadas

- **PR #21** — Implementou `odds_tracking.py`, `roi_report.py`, `calibration_o25.py`, tabela `odds_snapshots`, testes e `roi.html`/`roi_jogos.csv`. Passou CI e foi integrado em `main` (commit `9fbe965`). Foi criado o filtro experimental **MC-O70** (Over base ≥70%) e MC-O70 + EV ≥5%, sem substituir o destaque verde Over/BTTS ≥60% da página inicial.
- **PR #22** — Adicionou execução por `push` em `main` para alterações operacionais, diagnóstico de captura e verificação do segredo BSD. O agendamento original de duas em duas horas e o disparo manual mantêm-se (commit `53f4ce0`).
- **Execução em produção #39954307779** — 42 jogos consultados, 34 odds Over 2,5 válidas, 8 indisponíveis; geração do ROI HTML e CSV; 11 testes aprovados; commit `07068a0` com dados; GitHub Pages publicado. Não havia ainda resultados liquidados para calcular ROI da nova série de odds.
- **Revisão de apresentação e integridade histórica (esta atualização)** — A página `roi.html` passa a explicar permanentemente as diferenças entre os critérios original e experimental e mostra cinco conjuntos comparáveis. Passam a ser guardados `prob_btts_no_instante` e `baixa_confianca_no_instante`, necessários para simular fielmente o verde original **nos próximos jogos**. Os registos antigos mantêm `NULL`, sem retropreenchimento. Adicionada metodologia formal e testes de anti-fuga de informação.

## Decisões de análise

- A recalibração v2 permanece desligada após ligeira degradação no conjunto temporal de validação (Brier original 0,23538; recalibrado 0,23572).
- Não usar taxa de acerto histórica como substituto de ROI; só calcular ROI de jogos com odds congeladas antes do início e resultados finais confirmados.
- Não identificar preços de consenso como odds Betfair/Pinnacle nem transformar o ROI simulado em rentabilidade real.
- Preservar histórico e acompanhar futuras janelas cronológicas com amostras suficientes, sem retropreencher probabilidades a partir de jogos já terminados.