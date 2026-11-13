# Auditoria final — Monte-Carlos — 13/11/2026

Trabalho na branch `codex/monte-carlos-final-cleanup`. Sem merge, execução BSD real ou alteração da produção. Implementação P0–P7 preparada para revisão; a confirmação operacional em produção só pode ocorrer depois dessa revisão.

## 1. Versão inicial e baseline P0

- `main`: **`d723974173a8a87026212f1936cd21b1841fe360`**.
- SHA-256 original de `predictions.db`: **`06aba338b63844a59b4b38c7eb4b2b628be814454278458641b8b86aecebb818`**.
- A base versionada neste PR conserva exatamente esse hash. Migrações executadas exclusivamente em cópias descartáveis.
- Inventário completo, bytes, hashes de todos os ficheiros originais, schema SQL, contagens, grupos e métricas: [P0_2026-11-13.json](baseline/P0_2026-11-13.json).
- Evidência posterior e comparação automática: [AFTER_2026-11-13.json](baseline/AFTER_2026-11-13.json).
- Testes originais: **14 passaram**, antes de alterações ao código. Registo em [tests_2026-11-13.txt](baseline/tests_2026-11-13.txt).

| Tabela original | Registos |
|---|---:|
| predictions | 8 299 |
| predictions finished | 7 532 |
| v1 finished | 4 368 |
| v2 finished | 3 164 |
| v2 pending | 364 |
| team_stats (cache) | 2 084 |
| odds_snapshots | 1 531 |
| odds_snapshots estado ok | 968 |

`PRAGMA integrity_check = ok`. Sem duplicação de odds válidas por jogo/fonte, sem resultados contraditórios. `sqlite_sequence` é uma tabela interna, também inventariada.

## 2. Inventário funcional e dependências

Os módulos de simulação, xG e cache dependem apenas de NumPy, requests e biblioteca padrão. O runtime passa a Python 3.12, igual em CI e execução. Versões diretas e transitivas estão fixadas em `requirements*.txt` e `constraints.txt`; pytest/PyYAML são ferramentas de teste e Matplotlib é apenas de investigação.

Ficheiros examinados especificamente:

| Ficheiro | Decisão e dependência |
|---|---|
| modelo_v2_backfill.json | Preservado, byte a byte. Continua referenciado por `pipeline.database._backfill_modelo`, regra legada de proveniência; não pode ser arquivado. Não há versões NULL na baseline que exijam novo preenchimento. |
| recalibracao.json | Preservado, byte a byte, `aplicado=false`. Artefacto legado; a aplicação em produção está bloqueada no código, incluindo `APPLY_RECAL=1`. |
| recalibracao.py | Mantido como diagnóstico exploratório legado; escreve em `research/legacy_recalibracao_offline.json`, com embargo e sem ativação. Não é executado em Actions. |
| migrar_ligas.py | Mantido para diagnóstico. A CLI lê em modo SQLite read-only; `--aplicar` é recusado explicitamente. Uma correção histórica de liga requer migração separada, revista e testada numa cópia. As funções antigas continuam disponíveis para inspeção. |
| ligas_bsd.py / ligas_bsd.json | Mantidos. O JSON é recurso ativo do modelo/backtest; o workflow manual de auditoria continua disponível, limitado à main e com exclusão mútua dos escritores. Nenhuma chamada real foi feita neste trabalho. |
| calibracao_o25_v2.json | Preservado, byte a byte, `ativo=false`. O gerador legado deixa de refazer ajuste com o holdout ou de ativar configurações; resultados vão para investigação. |

## 3. Problemas encontrados

1. Previsões pendentes eram reescritas sem histórico de observações; `created_at` só representa a inserção inicial.
2. O gerador Platt podia escolher ativação pelo holdout e ajustar os coeficientes finais incluindo esse holdout.
3. O baseline por liga podia conhecer resultados de jogos com o mesmo kickoff, ou ainda não terminados.
4. O carregador ROI dizia usar a primeira odd, mas ordenava pela última. A baseline não tem duplicados válidos: a correção não muda nenhum jogo ou ROI.
5. A cronologia de odds era verificada antes da resposta da API; uma resposta lenta podia chegar após o kickoff. Havia também tolerância de cinco minutos para cotações futuras no relatório, removida.
6. Existem **12 previsões históricas criadas no/após o kickoff**. São preservadas e assinaladas. O histórico legado não é apresentado como prova de previsão congelada pré-jogo.
7. Cobertura recente insuficiente: **245/562 = 43,6%** dos jogos v2 liquidados nos últimos sete dias. O sucesso do workflow não resolve este enviesamento.
8. Os snapshots antigos não congelavam a versão do modelo; parte deles também não congelava BTTS/confiança. Os campos ausentes continuam ausentes.

## 4. Alterações implementadas P1–P7

- P1: separação por responsabilidade; caminhos de recursos ancorados à raiz. Os comandos e imports antigos continuam através de módulos de compatibilidade que expõem o mesmo objeto Python.
- P2: `prediction_snapshots` append-only, com instante UTC, kickoff conhecido, versão, Over/BTTS/base, confiança, proveniência, parâmetros e hashes dos três módulos do modelo. A vista `predictions` mantém-se. Atualização e snapshot são uma única transação.
- P2: triggers impedem UPDATE/DELETE de snapshots, DELETE de previsões e alteração de previsões liquidadas. Um índice único impede odds válidas duplicadas por jogo/fonte. A hora de liquidação futura fica em `settled_at`, sem reconstrução retroativa.
- P2: as primeiras/últimas observações só existem a partir da adoção deste código. Não se copia o histórico atual para fabricar observações antigas. Jogos já iniciados, liquidados ou com horário divergente são excluídos da nova previsão, com diagnóstico do conflito de horário.
- P3: protocolo fixo, estudo offline, treino/validação/holdout por dias UTC, embargo de 48h, Platt com regularização fixa e isotónica PAVA quando há amostra suficiente. Seleção pela validação; sem refit no holdout e sem ativação.
- P4: cinco hipóteses originais, sem mudar limiares, com probabilidades médias, intervalos Wilson e bootstrap por dia, períodos, versão, cobertura e evolução semanal.
- P5: backtest mostra Log Loss, ECE, IC Brier, períodos v1/v2, evolução mensal, baselines e universos separados de primeira/última/odd congelada. As tabelas por liga existentes mantêm mínimo de 50 jogos. Index preserva filtros, ordenação e critérios verdes; acrescenta versão e UTC. Nomes externos são escapados na geração HTML.
- P6: testes antes da API; baseline por corrida; controlos antes de commit/publicação; sem rebase automático de SQLite se a main mudar; diagnóstico independente de cobertura; confirmação do commit exato no build Pages.
- P7: testes offline de migração, rollback, imutabilidade, preservação, cronologia, resultados, métricas, estados científicos, ausência de dependência do holdout e pipeline completo com mocks.

## 5. Métricas antes/depois no mesmo universo

Os valores iniciais do pedido foram reproduzidos, sem diferenças de arredondamento relevantes. Os valores depois da refatoração coincidem com os iniciais: **ΔBrier=0 e ΔROI=0**. As probabilidades históricas não foram recalculadas.

| Modelo/mercado | n | Brier antes | Brier depois | Skill descritivo | Log Loss agora apresentado |
|---|---:|---:|---:|---:|---:|
| v1 Over | 4 368 | 0,263731 | 0,263731 | −6,85% | 0,729395 |
| v1 BTTS | 4 368 | 0,260609 | 0,260609 | −5,19% | 0,718848 |
| v2 Over | 3 164 | 0,244397 | 0,244397 | +1,45% | 0,682753 |
| v2 BTTS | 3 164 | 0,247419 | 0,247419 | +0,11% | 0,688106 |

O skill acima mantém o baseline descritivo da taxa da própria amostra, que usa os resultados observados. É diferente do skill contra 0,5, da taxa congelada do treino e do baseline por liga com atraso; estão identificados separadamente no painel. v1 cobre 06/08–04/10/2026 e v2 04/10–13/11/2026: não é uma experiência controlada sobre o mesmo período.

| Hipótese | n | Acerto | Prob. média | ROI antes/depois | IC95% ROI por dias | Estado |
|---|---:|---:|---:|---:|---|---|
| Todos | 933 | 55,5% | 58,6% | −6,82% | [−13,24%; −0,80%] | NO-GO |
| Over verde ≥60% | 401 | 59,1% | 69,0% | −10,87% | [−20,08%; −2,48%] | NO-GO |
| Over + BTTS verdes ≥60% | 344 | 61,9% | 69,9% | −6,98% | [−15,08%; +0,64%] | INCONCLUSIVO |
| MC-O70 | 158 | 65,8% | 76,3% | −6,92% | [−15,63%; +3,44%] | INCONCLUSIVO |
| MC-O70 + EV≥5% | 107 | 65,4% | 77,3% | −3,29% | [−15,00%; +9,56%] | INCONCLUSIVO |

Intervalos aproximados, condicionais à cobertura, com apenas 22 dias e dependência entre ligas/jogos; não provam lucro executável. As linhas sobrepõem-se. Todos os campos originais dos **933 registos CSV** foram comparados e preservados; só foram acrescentadas duas colunas explícitas de proveniência da versão.

### Calibração offline — universo diferente e explícito

Estudo sobre as **933 probabilidades congeladas nas odds**, não sobre as 3 164 linhas da vista legada. Treino 607, validação 76, holdout 28; 222 observações excluídas pelo embargo. Platt e isotónica usam exclusivamente o treino. A isotónica vence na validação, mas não melhora o Brier no holdout.

| Modelo | Validação Brier | Holdout Brier | Holdout Log Loss |
|---|---:|---:|---:|
| v2 original | 0,27506 | 0,22612 | 0,64304 |
| Platt | 0,25712 | 0,23400 | 0,66067 |
| Isotónica | 0,25562 | 0,22741 | 0,64721 |

Relatório, curvas, incerteza pareada, faixas ≥60%, estabilidade e IDs exatos: [CALIBRACAO_2026-11.md](../research/results_2026-11/CALIBRACAO_2026-11.md) e [calibration_results.json](../research/results_2026-11/calibration_results.json).

Este holdout histórico não é independente dos agregados já inspecionados. A divisão por dias e o embargo foram mantidos, mesmo produzindo apenas 28 jogos finais; não se procurou um corte favorável. Como a liquidação histórica não tem timestamp, 48h é uma hipótese explícita de maturação, não prova de disponibilidade em tempo real. **Nenhuma melhoria científica foi demonstrada que justifique ativação.**

## 6. Ficheiros movidos ou arquivados

| Implementação anterior | Implementação atual |
|---|---|
| main.py, predictor.py, adjusted_xg.py, team_stats.py, api.py | src/ |
| database.py, odds_tracking.py, settlement.py | pipeline/ |
| backtest.py, roi_report.py, calibration_o25.py | analysis/ |

Os ficheiros raiz continuam como compatibilidade. **Nenhum ficheiro, workflow, tabela ou branch histórico foi apagado ou arquivado.** A base e os JSON ativos ficaram na raiz para reduzir risco e manter recursos, comandos e URLs públicos. Não se criaram diretórios vazios decorativos.

## 7. Migração SQLite e rollback

`pipeline.database.init_db()` faz backup SQLite antes da primeira migração e executa DDL numa transação `BEGIN IMMEDIATE`. Adiciona tabelas/colunas/índices/triggers; não altera probabilidades, resultados, datas ou odds existentes. A migração foi executada duas vezes numa cópia da base original; a segunda execução conservou inclusive o hash da cópia migrada. Uma exceção simulada após ALTER TABLE reverteu o schema.

O backup local fica em `data/backups/pre-snapshots-<hash-original>.db`, fora de Git. Em Actions existe ainda `work/pre-run.db`. O PR não inclui uma base migrada; a primeira execução revista fará a migração sobre os dados então atuais.

Rollback do código: reverter os commits do PR numa branch revista. Os comandos antigos continuam compatíveis com as tabelas/colunas adicionais; **a reversão preferida mantém a base nova e os snapshots já recolhidos**. Não restaurar um backup antigo sobre dados posteriores.

Rollback de migração falhada: a transação é revertida automaticamente. Se for necessário recuperar o ficheiro antes de qualquer nova observação, parar os escritores, validar o backup numa cópia com `PRAGMA integrity_check` e comparar o manifesto antes de substituir o ficheiro. Uma recuperação após novas observações exige cópia integral atual e reconciliação, para preservar resultados/odds/snapshots. Nunca remover triggers para corrigir resultados liquidados sem revisão específica.

Rollback de painéis: regenerar com a versão anterior do código sobre uma cópia da base validada; manter `index.html`, `backtest.html`, `roi.html` e `roi_jogos.csv`. Git conserva os artefactos e a base de cada commit anterior.

## 8. Verificações de integridade

- Hash da base original e dos quatro JSON de referência inalterados.
- Comparação por chave e por coluna de todas as previsões/odds existentes numa cópia migrada.
- Preservação de linhas liquidadas, resultados e instantes; novos campos históricos permanecem NULL.
- Bloqueio de odds futuras/pós-kickoff/antigas, odds válidas duplicadas e divergência entre a probabilidade congelada na odd e o snapshot de previsão associado.
- Deteção de redução de contagens, remoção de IDs/colunas e modificação de dados históricos.
- Validação de documentos HTML, links relativos, exportação CSV e IDs únicos.
- Apenas primeira odd válida; critérios verdes e cinco filtros originais preservados.

## 9. Testes executados

- `python -m pytest -q`: **33 passaram** offline, com API BSD simulada.
- `python -m compileall -q src pipeline analysis tests *.py`: sem erros.
- `python -m pipeline.offline_check`: migração repetida e geração de relatórios numa cópia; base de origem preservada.
- `python -m pipeline.integrity --published .`: zero erros, aviso dos 12 registos legados tardios.
- `actionlint 1.7.7 -shellcheck=`: três workflows válidos; análise externa shellcheck não executada.
- `git diff --check`: sem erros.
- Chromium/Playwright: três páginas HTTP 200, desktop e viewport 390px; filtro de texto, filtro de baixa confiança, ordenação Over e CSV com 933 jogos funcionais; zero erros JavaScript. Evidência em `baseline/AFTER_2026-11-13.json`.
- Teste de fuga: inverter exclusivamente os resultados do holdout não modifica coeficientes, seleção ou métricas de validação.
- Testes de publicação usam mocks e exigem o SHA exato; não foi pedido nenhum build em produção.

O resultado da CI remota fica registado no Pull Request. Não se substitui essa evidência por uma afirmação de sucesso antecipada.

## 10. Limitações por resolver

- Não é possível obter a primeira/última previsão antiga: **N/D**. O mesmo vale para versão/BTTS/confiança não congelados nos snapshots antigos.
- Cobertura de odds de 43,6% em sete dias; fonte de seleção que exige acompanhamento prospetivo.
- O histórico `predictions` não prova a hora da última atualização anterior a este PR.
- Holdout de 28 jogos numa única semana, curvas extremas com amostras pequenas e intervalos amplos.
- Não há timestamps históricos de liquidação; nenhuma alegação de validação real-time perfeita.
- Simulação estocástica original sem seed por jogo; este PR congela a saída observada, sem refazer ou otimizar o modelo original.
- BSD é preço de consenso **não executável**; não valida rentabilidade numa casa, não integra Betfair nem execução de apostas.

## 11. Riscos operacionais e custos

O cron continua `17 */2 * * *` UTC. Não há fontes ou serviços pagos. Repositório público, runners padrão, mesmas três Actions, nenhuma tarefa de investigação no cron. Limites de chamadas BSD existentes foram mantidos.

Cobertura inferior a **25%**, com pelo menos 20 jogos liquidados recentes, bloqueia persistência/publicação; é um piso operacional explícito para detetar falha grave, não um limiar científico. Os **80%** do protocolo futuro são uma referência diferente. O sumário mostra cobertura mesmo com workflow verde.

Persistência é atómica por commit. Se a main avançar durante a corrida, o push é bloqueado sem rebase de SQLite. Uma etapa crítica falhada impede publicação; observações da corrida falhada ficam num artefacto de recuperação com retenção de um dia. Atualmente duas cópias da base somam aproximadamente 5,4 MB antes de compressão; mesmo 12 falhas/dia ficam perto de 65 MB antes de compressão. Crescimento da base e quota de artefactos da conta devem ser acompanhados; nenhuma subscrição ou quota paga foi ativada.

A configuração Pages foi apenas lida: `legacy`, fonte `main:/`, estado inicial `built`. Após revisão, o workflow pedirá um build e verificará SHA/estado; token, permissões e resposta desse endpoint em produção ainda não foram exercitados. Uma falha de publicação produz workflow vermelho e recuperação, sem alegar que o site foi atualizado. A revisão deve incluir uma primeira corrida supervisionada após eventual merge.

## 12. Registo de hipóteses

[research/hypotheses.json](../research/hypotheses.json) fixa IDs, regras e estados. [protocol_2026-11-13.json](../research/protocol_2026-11-13.json) fixa o estudo de calibração. Ambos são um registo histórico exploratório de 13/11/2026; não fingem pré-registo dos resultados já conhecidos.

Nenhuma estratégia atual recebe CANDIDATO ou VALIDADO automaticamente. Uma confirmação futura exige novos dados, protocolo registado antes da recolha, cobertura adequada, incerteza, período e revisão independente documentada. O consenso BSD continua sem demonstrar execução lucrativa.

## 13. Parecer científico e âmbito da aprovação

**Não ativar calibração. Não afirmar vantagem económica.** A calibração offline não melhora o Brier no pequeno holdout final; os resultados de ROI continuam negativos. O ganho deste PR é técnico e de auditabilidade.

A aprovação técnica é independente da validação científica. O código, migração em cópia e painéis passaram as verificações offline; a CI remota e a primeira execução operacional após revisão têm de ser avaliadas nos seus próprios termos. Não houve merge, publicação ou alteração de dados de produção durante este trabalho.
