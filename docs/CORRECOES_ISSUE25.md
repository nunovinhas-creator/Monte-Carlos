# Issue #25 — horários operacionais e cobertura de odds

Base: `main` **6eaa16cc0b53542c8ab6c59d54e775a943fa42ec**, confirmada ao iniciar. Branch `codex/issue-25-kickoff-odds-audit`. Implementação pontual, sem nova estratégia, recalibração, chamadas BSD reais, alteração manual da produção ou merge. Evidência completa: [ISSUE25_2026-11-13.json](baseline/ISSUE25_2026-11-13.json).

## Política temporal

`predictions.timestamp`, `event_date` e `created_at` continuam a representar a observação original e passam a ter proteção SQL contra alteração. Probabilidades da vista pendente podem continuar a atualizar-se; cada previsão observada fica congelada, com a revisão de horário que a originou. Probabilidades, odds e ligações de snapshots anteriores nunca são reescritas.

`kickoff_revisions` é append-only: ID, evento, deteção UTC, horário operacional anterior, horário recebido (NULL quando desconhecido), fonte, tipo, estado, motivo e revisão anterior. O tipo é `postponement` quando a hora aumenta, `advancement` quando diminui, `data_correction` apenas quando indicado explicitamente pela origem da correção; a direção temporal não prova a causa desportiva. `unknown` assinala evidência incompleta. O instante de deteção não é apresentado como a data histórica em que a BSD mudou a agenda.

A vista `operational_schedule` separa o horário atual do original. Uma nova data só é aplicada automaticamente se for observada antes do novo início e o evento continuar pendente, sem resultados. O horário antigo pode já ter passado: isso não impede a criação de uma previsão nova, realmente pré-jogo da data futura. `finished`, `void` e `stale` nunca são reabertos automaticamente. Mudanças descobertas no/após o novo início, substituições de ID e contradições exigem quarentena/revisão manual. Em quarentena o horário operacional é NULL (N/D), e odds/liquidação ficam bloqueadas. Uma observação posterior de uma nova data futura para um evento pendente permite regressar ao processamento, mantendo toda a cadeia.

Cada snapshot novo guarda `kickoff_revision_id`; cada odd nova guarda também `kickoff_at_capture` e mantém `prediction_snapshot_id`. Depois de uma revisão é obrigatória uma previsão da mesma revisão, seguida de uma cotação cuja data não preceda a deteção. A janela continua a ser 6h e a idade máxima da cotação continua a ser 6h. A primeira odd válida é única por evento/fonte/revisão; regressar ao horário original cria outra revisão e não recicla a odd antiga. O estado é verificado novamente sob `BEGIN IMMEDIATE` depois da resposta da API, para rejeitar corridas com outro escritor.

`src.main` observa mudanças antes de descartar jogos já iniciados. `pipeline.database` aplica a mesma política ao gravar. `odds_tracking` e `settlement` usam a vista operacional; a liquidação volta a reconciliar o payload BSD antes de decidir. Um evento adiado com data futura não é anulado nem marcado stale pelo horário antigo. Adiamento sem data futura verificável fica pendente em quarentena, em vez de ser anulado.

## Definições de avaliação

- `load_rows(..., definition='current')`, por defeito: um evento liquidado, primeira odd válida da revisão operacional atual. Quarentena fica excluída do numerador, com N/D, sem ocultar o evento do denominador de cobertura.
- `definition='prospective'`: exige ainda ligação a uma previsão congelada e exclui a previsão original de um evento marcado como legado tardio. Uma previsão nova, validamente observada para uma nova revisão futura, é uma observação distinta.
- `definition='as_observed'`: primeira odd válida pelo horário congelado na própria observação. Preserva a capacidade de reproduzir o histórico anterior à mudança, mas não o reclassifica como evidência pré-jogo do novo horário.
- `definition='revision_history'`: primeira odd válida de cada revisão, para inspeção de todas as épocas. Pode ter várias linhas por evento; não deve ser misturada com o ROI por jogo.
- `prediction_rows(..., 'first'/'last')`: primeira/última previsão realmente observada, avaliadas com o próprio `snapshot.kickoff`, sem comparação retroativa com outra agenda. As definições são explicitadas no backtest.

Os cinco filtros ROI e os seus limiares mantêm-se. O CSV conserva as colunas anteriores e acrescenta IDs de odd/previsão/revisão, definição temporal e estado da agenda. As 12 previsões tardias foram identificadas numa tabela aditiva `legacy_prediction_flags`: não são apagadas nem promovidas a snapshots antigos. Todos os seus valores originais foram comparados por chave/coluna. A validação prospetiva não recebe estas previsões legadas.

## Os seis IDs auditados

Fonte do horário guardado: SQLite de `6eaa16c`. Fonte da divergência: [run #820, job build](https://github.com/nunovinhas-creator/Monte-Carlos/actions/runs/43428215133/job/131894823856), avisos de 13/11/2026 às **12:50:25 UTC**. O log apenas publicou o ID e o aviso; não publicou o novo horário da API. O ambiente não tem `BSD_API_TOKEN` configurado. Não foi feita uma chamada real nem reconstruído um horário ausente.

| ID | Evento | Horário original UTC | Novo horário da API no run #820 | Estado inicial da migração |
|---|---|---|---|---|
| 649924 | United Arab Emirates — Honduras | 13/11/2026 13:00 | N/D | Quarentena |
| 604616 | Shooting Stars — Bendel Insurance FC | 15/11/2026 15:00 | N/D | Quarentena |
| 606209 | Grenada — Saint Kitts and Nevis | 13/11/2026 20:00 | N/D | Quarentena |
| 654308 | PAOK — FK Partizan | 14/11/2026 13:00 | N/D | Quarentena |
| 604619 | Kano Pillars — Ranchers Bees | 15/11/2026 15:00 | N/D | Quarentena |
| 604620 | Plateau United — Kun Khalifat FC | 15/11/2026 15:00 | N/D | Quarentena |

A migração acrescenta uma revisão desconhecida por conflito, ancorada ao aviso e ao horário original exato; não altera nenhuma previsão. É idempotente e não substitui revisões posteriores. Numa futura execução autorizada, uma data futura realmente recebida permite avançar o evento pendente e voltar a criar snapshots/odds. Sem prova suficiente permanece necessária revisão manual. Não foi feita intervenção na base de produção para “corrigir” estes seis valores.

## Diagnóstico da cobertura

Reproduzido sobre `6eaa16c` no intervalo **06/11/2026 12:50:40 a 13/11/2026 12:50:40 UTC**, usando o mesmo denominador de jogos v2 liquidados:

| Estado por jogo, sem sobreposição | Jogos |
|---|---:|
| Odd elegível | 245 |
| Consultado, última tentativa sem timestamp da cotação | 75 |
| Consultado, última tentativa sem preço Over 2,5 | 71 |
| Consultado, cotação antiga/futura (código legado combinado) | 3 |
| Sem tentativa observável; causa N/D | 169 |
| Total liquidado | **563** |

**Cobertura: 245/563 = 43,5169%.** Há evidência de consulta em **394/563 = 69,9822%** dos jogos. Entre os consultados, **245/394 = 62,1827%** têm preço elegível. Uma tentativa registada não prova resposta HTTP bem-sucedida. Os 169 sem tentativa não são automaticamente classificados como falha de cron: a instrumentação anterior não permite separar janela, orçamento, exclusão de ID ou falha anterior à captura.

| Dia UTC (extremos parciais) | Liquidados | Consultados | Elegíveis |
|---|---:|---:|---:|
| 06/11 | 52 | 49 | 38 |
| 07/11 | 213 | 138 | 83 |
| 08/11 | 229 | 164 | 96 |
| 09/11 | 14 | 3 | 3 |
| 10/11 | 9 | 4 | 3 |
| 11/11 | 6 | 6 | 3 |
| 12/11 | 37 | 27 | 17 |
| 13/11 | 3 | 3 | 2 |

Os denominadores, percentagens, motivos e contagens das **64 ligas** estão no JSON de evidência. O diagnóstico é reproduzível com `python -m pipeline.coverage --now 2026-11-13T12:50:40+00:00` sobre a base daquele SHA; não é uma reconstrução de dados que venham a ser liquidados depois. O valor atual numa janela móvel pode mudar sem alteração de política.

Nas dez novas tentativas do run #820: uma válida, oito sem preço e uma sem timestamp. Não há nos logs evidência suficiente para atribuir essas nove falhas a quotas/API. Antes desta correção, `src.api` podia colapsar HTTP/rede numa resposta vazia; o novo caminho de odds preserva erros HTTP, 429 persistente e exceções, sem alterar o comportamento dos outros consumidores BSD.

A nova tabela append-only `odds_capture_audit` regista uma decisão por evento pendente/corrida, incluindo IDs não numéricos, fora da janela 6h, início passado, limite de 80 consultas, ausência de snapshot da revisão, legado não verificável, quarentena, idade, timestamps inválidos, HTTP/API/quota e resposta após início. IDs fallback são excluídos antes de consumir o orçamento. `health_summary` publica contagens por liga/dia e decisões da corrida mesmo quando o workflow está verde. As percentagens da corrida usam todos os pendentes considerados, explicitamente distintos dos liquidados nos últimos 7 dias.

O cron `17 */2 * * *`, a janela 6h, os limites BSD e o piso operacional **25% com n≥20** não mudam. Não existe backfill nem relaxamento de critérios. O ROI continua condicionado aos jogos com preço: o viés de seleção é explícito no relatório. As causas históricas não identificáveis permanecem N/D.

## Pages e confirmação operacional

O [PR #24](https://github.com/nunovinhas-creator/Monte-Carlos/pull/24) foi integrado em `main` em **13/11/2026 12:47:52 UTC**, merge `2fe1132`. O run #820 concluiu com sucesso e persistiu `6eaa16c`. Dois builds Pages visaram esse SHA: [#852](https://github.com/nunovinhas-creator/Monte-Carlos/actions/runs/43428611615) foi cancelado; [#853](https://github.com/nunovinhas-creator/Monte-Carlos/actions/runs/43428615452) concluiu com sucesso. O POST incondicional do workflow era redundante neste caso.

A etapa agora observa primeiro o build automático e confirma SHA exato + estado `built`. Se já existir um build desse SHA, incluindo `building`/`queued`, não pede outro. Se estiver ausente após cerca de 60 segundos, pede no máximo um build de recurso; isto conserva suporte a pushes cujo token não acione Pages. Erro ou falta de confirmação continua a falhar o workflow. Uma corrida entre a última leitura e um build automático muito atrasado ainda pode causar duplicação; a API não oferece uma criação condicional atómica. Não se pediu nenhum build real neste PR.

## Verificações e preservação

- **61 testes** offline, com respostas BSD simuladas; a suite completa também passa numa cópia que já recebeu a migração; `compileall`, `pipeline.offline_check`, `pipeline.integrity --published .` e `actionlint 1.7.7 -shellcheck=` passam. Comandos e resultados: [ISSUE25_checks.txt](baseline/ISSUE25_checks.txt).
- Baseline encontrada ao iniciar: **32 testes passaram e um falhou** porque assumia zero snapshots. A produção pós-PR24 já tem **340**; o teste foi corrigido para preservar a contagem existente, incluindo bases já migradas nas corridas futuras.
- A cópia migrada mantém todos os valores de todas as colunas originais: **8 301 previsões, 1 541 odds e 340 snapshots**. O segundo `init_db` conserva inclusive os bytes da cópia migrada. Uma falha após criar as estruturas novas reverte o schema e os dados na transação.
- Hash original da base: `0f31d652d9a9fe36176b376aca61aaf1c3c5b6002051cef8645a62aac0a6e1cb`. A base versionada, os quatro artefactos publicados e os JSON de referência permanecem byte a byte iguais. Não há base migrada no PR.
- As **933 linhas** do CSV foram regeneradas numa cópia e comparadas em todas as colunas anteriores; nenhuma diferença. Métricas dos cinco filtros antes/depois são idênticas; os novos campos são aditivos. `index.html`, `backtest.html` e `roi.html` mantêm compatibilidade e os relatórios são regenerados apenas em cópias de teste.
- Simulações cobrem antecipação após cotação, antecipação descoberta tarde, adiamento antes/depois do início antigo, primeira previsão, revisões repetidas, regresso à hora original, `finished/void/stale`, idempotência, resposta API em corrida com mudança de horário, liquidação tardia, adiamento sem data, quotas, timestamps e rollback.
- Não foram ativadas Platt/isotónica ou novas hipóteses. Sucesso técnico não é validação científica.

## Migração, rollback e riscos restantes

Antes da migração é feito backup SQLite em `data/backups/pre-kickoffs-<hash>.db`; Actions também conserva `work/pre-run.db` e o artefacto de recuperação de um dia. DDL, inserções de evidência e alterações de índices/triggers são transacionais. O índice passa de um preço por evento para um por revisão; todas as observações antigas ficam intactas. Novas colunas históricas ficam NULL, sem associação retroativa inventada.

Falha durante a migração: rollback automático da transação, validado em teste. Antes de qualquer nova observação, o backup pode ser validado numa cópia e comparado por chave/coluna; os escritores devem estar parados antes de eventual recuperação revista. Depois de novas observações, **não restaurar um backup antigo sobre a base atual**, nem remover tabelas, triggers, odds ou revisões para voltar ao schema anterior.

Rollback operacional depois da adoção exige parar os escritores e preparar uma reversão revista que conserve a base aditiva e compreenda a agenda operacional. Reverter só o código para PR24 preserva os dados, mas pode bloquear novas capturas/snapshots perante os triggers e várias épocas; não é autorização para retomar o cron antigo. Preferir correção incremental que preserve a cadeia. Os painéis podem ser regenerados offline antes da publicação de uma reversão. Nenhum rollback foi executado em produção neste trabalho.

Persistem a baixa cobertura e os horários recebidos desconhecidos dos seis conflitos; a correção torna esses limites explícitos e seguros. O crescimento das tabelas append-only deve continuar a ser acompanhado; não há nova infraestrutura paga, mais chamadas de rotina ou retenção adicional de artefactos. A confirmação da CI remota consta do PR. Uma primeira corrida de produção após revisão deve confirmar a reconciliação real com BSD, sem substituir os testes por uma alegação antecipada.

**Parecer: APROVAR MERGE TÉCNICO**, sujeito à CI do PR, mantendo o PR Draft para revisão humana. A quarentena é o resultado aceite quando não há prova para resolver automaticamente. Este parecer não valida cientificamente as estratégias, a calibração ou a rentabilidade. Não fazer merge automático.
