# Monte-Carlos — Candidato de recalibração Over 2,5 (shadow, 14/11/2026)

**Estado: PESQUISA OFFLINE / NÃO ATIVAR EM PRODUÇÃO.**
Esta experiência não altera `src/predictor.py`, `src/main.py`, `calibracao_o25_v2.json`, `recalibracao.json`, a SQLite versionada, os workflows, os painéis nem as cinco regras originais de ROI.

## Hipótese técnica

A v2 está sobreconfiante em vários intervalos altos de Over 2,5. Testar um Platt regularizado e monótono, mantendo a ordem das probabilidades:

`p_shadow = sigmoid(a + b * logit(p_original))`.

A regularização L2 fixa de força 2 aproxima `(a,b)` de `(0,1)`. Não se utilizam odds ou ROI no fitting. Não há procura de parâmetros para maximizar lucro.

## Dados e separação temporal

- **Treino:** jogos da v2 anteriores a **21/10/2026 00:00 UTC**, criados depois da entrada da v2 em 04/10 e antes do respetivo kickoff; versão histórica da tabela `predictions`.
- **Avaliação:** CSV de odds pré-jogo a partir de **23/10/2026 13:30 UTC**, sem reconstituir preços antigos. Há embargo nominal de 48 h entre cortes.
- **Fonte inicial do treino:** SQLite do ZIP de 23/10/2026. A reprodução utiliza a `predictions.db` mais recente, filtrada pelo mesmo cutoff. Preservação de linhas históricas já liquidadas é requisito de integridade.
- **Avaliação exploratória:** `roi_jogos.csv` de **14/11/2026 06:42 UTC**, 956 jogos, dos quais 931 usam inferência de versão legada e 25 possuem proveniência de versão mais recente.

O histórico antigo não contém sempre o instante de liquidação, pelo que a maturação de 48 h **não prova** disponibilidade real do resultado à data. As probabilidades de treino legadas também não estavam congeladas em snapshots imutáveis. Os resultados agregados do ROI já tinham sido vistos antes de desenhar esta comparação: **não é um holdout independente pré-registado**.

## Resultado exploratório da comparação

| Medida | v2 original | Platt shadow |
|---|---:|---:|
| Jogos de treino | 1 096 | 1 096 |
| Jogos avaliados | 956 | 956 |
| Brier Score | 0,248475 | **0,244715** |
| Log Loss | 0,690664 | **0,682167** |
| ROI de apostar em todos os 956 jogos | **−6,00%** | **−6,00%** |

Coeficientes aproximados: `a = -0,024599`, `b = 0,707446`, treinados exclusivamente na amostra pré-21/10. Com estes parâmetros, uma previsão de **75%** passa a cerca de **68,0%** e uma de **80%** a cerca de **72,5%**.

Diferença de Brier (shadow − original): **−0,003759**; intervalo aproximado por bootstrap de 23 dias UTC: **[−0,00659; −0,00049]**. A dependência entre jogos/dias, a cobertura seletiva de odds e a investigação prévia dos resultados impedem interpretar o intervalo como certificação de generalização.

**A alteração melhora a estimativa de probabilidades nesta comparação, mas NÃO transforma o retorno histórico de todos os jogos em positivo.** O valor teórico de 1 unidade por aposta não muda quando apenas corrigimos probabilidades; para avaliar uma estratégia nova seriam necessários critérios definidos antes dos jogos futuros e cotações executáveis.

## Reprodução (custo zero)

```bash
python -m analysis.shadow_platt_o25
python -m analysis.shadow_platt_o25 --output research/results_2026-11/shadow_platt_20261114.json
python -m pytest -q tests/test_shadow_platt_o25.py
```

O script usa apenas bibliotecas padrão e ficheiros locais, lê SQLite em modo read-only, rejeita odds após kickoff e não contacta a API. É proibido guardar a saída em qualquer ficheiro de configuração de produção.

## Próxima decisão

Não ativar o calibrador com base nestes 956 jogos históricos. Primeiro recolher uma janela **nova**, com versões e probabilidades congeladas, cobertura de odds adequada e protocolo de avaliação publicado antes dos resultados. Comparar Brier, Log Loss e calibração de probabilidades na mesma população; avaliar ROI separadamente, com odds de mercado que possam realmente ser executadas.

A promoção exigirá um PR próprio e auditoria independente, com rollback e nenhuma alteração retrospectiva de previsões. Sem essa prova, manter a v2 original.
