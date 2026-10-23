# Metodologia de comparação de estratégias — Monte Carlos v2

Atualização: **23 de outubro de 2026**. Documento de referência para interpretar o painel `roi.html`, as decisões técnicas e futuras análises empíricas.

## 1. Original versus experimental

A página principal `index.html` **nunca foi uma página de picks**: mostra probabilidades Monte Carlo de Over 2,5 e BTTS, destacadas a verde quando atingem **60%**. Jogos com `baixa_confianca` ficam cinzentos e sinalizados «sem dados». O modelo, a apresentação e as fórmulas originais de previsão não foram substituídos.

Depois da revisão dos resultados históricos da v2 (a primeira amostra tinha 1.319 jogos liquidados e o subconjunto com probabilidade Over ≥70% registou 137 Overs em 196 jogos), foi introduzido o filtro **MC-O70** para investigação futura. **69,9% de acerto passado não constitui demonstração de ROI positivo**, nem validação fora da amostra. MC-O70 utiliza `prob_o25_base >= 70`, sem condição adicional BTTS. Não confundir com o verde original de 60%.

O painel compara **cinco** linhas de Over 2,5: todos os jogos com odds; o verde original Over ≥60%; a conjunção dos dois verdes Over + BTTS ≥60%; MC-O70; e MC-O70 com EV modelado ≥5%. Os dois filtros de 60% reproduzem critérios visuais do site **como simulações novas**, não como supostas regras históricas de aposta.

## 2. Dados e momento da observação

Fonte: BSD (API gratuita), `GET /api/v2/events/{id}/odds/`, propriedade `odds.over_25_goals`. É uma **odd de consenso**, e não uma oferta contratável de uma casa. `odds_tracking.py` recolhe, para jogos de modelo v2 ainda pendentes e com ID numérico, até 6 horas antes do pontapé inicial.

Campos congelados por captura: `match_id`, `capturado_em` (UTC com fuso), `quote_at` (momento de atualização BSD), `odd`, `estado`/`detalhe`, `fonte`, `prob_o25_no_instante` (publicada), `prob_o25_base_no_instante`, `calibracao_o25_no_instante`, `prob_btts_no_instante` e `baixa_confianca_no_instante`. As duas últimas colunas **foram acrescentadas posteriormente**. Capturas anteriores continuam com `NULL`: não se tenta reconstruir a previsão antiga por uma linha `predictions` que pode ter sido atualizada.

Uma captura é elegível se tiver odd válida (>1), estado `ok`, fonte `bsd_consensus`, dados Over congelados, e tiver ocorrido antes do início, até 6 horas de antecedência. `quote_at` não pode ser posterior à observação por mais de 300 segundos. O scanner rejeita preços anteriores a 6 horas, timestamps ausentes, valores inválidos, jogos iniciados ou IDs artificiais. Após um `ok`, o scanner não repete esse jogo; o relatório escolhe um snapshot elegível por jogo.

## 3. Filtros e regras de inclusão

| Indicador no relatório | Predicado (campo do snapshot) | Se faltar informação |
|---|---|---|
| Todos | Qualquer jogo v2 terminado com odd elegível | Sem odd → excluído |
| Original Over verde ≥60 | `prob_o25_no_instante >= 60` e `baixa_confianca_no_instante == 0` | Se `baixa_confianca_no_instante` for NULL → **N/D**, excluído dessa linha |
| Original Over e BTTS verdes ≥60 | Ambos `prob_o25_no_instante` e `prob_btts_no_instante >= 60`, `baixa_confianca_no_instante == 0` | BTTS ou confiança NULL → **N/D**, excluído |
| MC-O70 | `prob_o25_base_no_instante >= 70` | Não exige BTTS ou confiança |
| MC-O70 + EV ≥5% | MC-O70 e `(prob_o25_no_instante / 100) * odd - 1 >= 0.05` | Sem probabilidade/odd → excluído |

A **probabilidade base** corresponde à saída v2 antes do Platt adicional opcional; a publicada é a final. Em outubro/2026, o Platt adicional permanece **desligado** (`calibracao_o25_v2.json ativo=false`), porque na validação cronológica Brier original 0,23538 versus recalibrado 0,23572. Não interpretar isto como prova de que a probabilidade v2 é uma odd de aposta.

## 4. Liquidação e estatísticas

- `result_o25=1`: três ou mais golos no final; `result_o25=0`: menos de três golos. Apenas `status='finished'`.
- Aposta hipotética: **1 unidade por jogo**. Ganha `odd − 1` unidades se Over; perde `−1` unidade se Under.
- Taxa de acerto = vitórias / jogos; odd média = média simples das odds observadas; lucro = soma dos ganhos; **ROI = lucro / jogos × 100%**.
- EV **modelado** = `P(Over publicada) × odd − 1` (em unidades de stake), sem garantia de calibração ou execução.
- «Sem amostra» significa **nenhum jogo elegível liquidado**, não 0% de ROI. Estratégias sobrepõem-se: não somar ganhos de linhas diferentes.
- A comparação só é justa se forem também observados tamanho de amostra, cobertura, período e viés de seleção. As regras de 60% com campos N/D podem ter um período efetivo mais curto do que MC-O70.

## 5. Rastreabilidade, CI e monitorização

O workflow `Executar Previsões e Backtest` corre a cada 2h em minutos `17` UTC (`17 */2 * * *`), manualmente, e por `push` de alterações operacionais na branch `main`. Verifica `BSD_API_TOKEN`, prepara dados, recolhe odds, liquida resultados, publica `backtest.html`, `roi.html`, `roi_jogos.csv`, apresenta um diagnóstico com cobertura no GitHub Actions e confirma testes antes de gravar as alterações. A versão anterior, PR #21, implementou a recolha e o painel; PR #22 garantiu disparo em push e diagnóstico da execução. O mesmo arquivo README aponta para esta metodologia.

Primeira execução validada, 23/10/2026: [run #39954307779](https://github.com/nunovinhas-creator/Monte-Carlos/actions/runs/39954307779), 42 tentativas, 34 odds válidas, 8 sem odd elegível, 11 testes passaram, commit automático `07068a0`. Isto confirma gravação/publicação, **não a taxa de acerto futura nem a rentabilidade**.

O ficheiro `roi_jogos.csv` inclui flags `original_over_verde_60`, `original_over_e_btts_verdes_60`, `mc_o70`, `mc_o70_ev5`. Para linhas antigas, `N/D` indica classificação indefinida; `0` indica classificado e não selecionado; `1` indica selecionado.

## 6. Limitações a manter explícitas

1. O consenso BSD não é uma odd individual executável; a comissão, deslizamento, limites, mudanças entre observação e colocação e impostos **não** fazem parte do ROI.
2. Não existe histórico de cotações antes de 23/10/2026; os primeiros 34 preços válidos não continham BTTS e fiabilidade congelados.
3. A seleção de filtros a posteriori eleva o risco de **sobreajuste**. É necessário medir em jogos posteriores à definição da regra e evitar retreinar com resultados do próprio holdout.
4. Os valores de xG utilizados pelo modelo são estimativas algorítmicas, não necessariamente xG por eventos de remate.
5. Um run verde com zero odds válidas não confirma cobertura. Exigir logs de recolha real, persistência e publicação.