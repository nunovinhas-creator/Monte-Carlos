# Monte-Carlos

Previsões de futebol **Over 2,5 e BTTS**, com Monte Carlo, SQLite e fontes gratuitas BSD. O projeto mede probabilidades e hipóteses; não executa apostas, não integra Betfair nem demonstra vantagem económica.

[Previsões](https://nunovinhas-creator.github.io/Monte-Carlos/) · [Validação](https://nunovinhas-creator.github.io/Monte-Carlos/backtest.html) · [Laboratório ROI](https://nunovinhas-creator.github.io/Monte-Carlos/roi.html) · [CSV](https://nunovinhas-creator.github.io/Monte-Carlos/roi_jogos.csv)

## Auditoria de novembro de 2026

[AUDITORIA_FINAL_2026-11.md](docs/AUDITORIA_FINAL_2026-11.md) contém SHA inicial, inventário/hashes, resultados antes/depois, migrações, testes, riscos e rollback. O trabalho foi preparado numa branch e PR Draft; a produção não foi alterada durante a implementação.

A baseline reproduz **7 532 jogos liquidados**, incluindo 3 164 v2. Brier v2: **0,2444 Over / 0,2474 BTTS**. O ROI das cinco estratégias continua negativo; nenhuma recebe validação automática. A base histórica não foi recalculada.

[Estudo de calibração offline](research/results_2026-11/CALIBRACAO_2026-11.md): probabilidades congeladas em 933 jogos com odds; treino 607, validação 76, holdout 28, embargo de 48h. Platt e isotónica não melhoraram o Brier no pequeno holdout final. **Calibração experimental em produção bloqueada**, incluindo a ativação legada por `APPLY_RECAL`. O shrinkage e o teto BTTS que já fazem parte do modelo v2 mantêm-se.

## Estrutura e compatibilidade

```text
src/         modelo, xG, estatísticas, API e geração das previsões
pipeline/    SQLite, migração, snapshots, odds, liquidação e controlos
analysis/    validação, ROI e investigação de calibração offline
research/    hipóteses versionadas, protocolo, resultados e curvas
docs/       auditoria, baseline e metodologia
tests/      testes offline com mocks BSD
```

Os módulos na raiz continuam como comandos/imports de compatibilidade. A base, os JSON ativos e as três páginas mantêm os caminhos atuais. `PREDICTIONS_DB` permite selecionar uma cópia da base; por omissão usa-se a base na raiz do projeto. Nenhum ficheiro histórico foi apagado ou arquivado.

## Histórico e definições de previsão

`predictions` é a vista atual compatível. Novas previsões pré-jogo também são guardadas em `prediction_snapshots` append-only, com instante UTC, kickoff conhecido, modelo, probabilidades/base, confiança, proveniência, parâmetros e hashes do código. UPDATE/DELETE de snapshots e alterações a previsões liquidadas são bloqueados. Novas liquidações guardam `settled_at`.

O backtest distingue **primeira observação**, **última observação pré-jogo**, **probabilidade congelada na primeira odd válida** e **vista histórica legada**. Estas séries não são misturadas. Não se fabricam observações antigas; comparações impossíveis aparecem como **N/D**. A data `created_at` antiga não prova quando a última previsão foi calculada.

A migração é aditiva, transacional e idempotente, com backup local antes da primeira alteração. Foi testada duas vezes numa cópia da base existente, incluindo rollback. Consulte o procedimento de recuperação na auditoria; nunca restaure um backup antigo sobre observações posteriores.

## Modelo e cinco hipóteses ROI

O destaque original mantém-se: **Over ≥60% verde**, **BTTS ≥60% verde**; baixa confiança fica cinzenta com aviso de dados insuficientes. É informação sobre o modelo, não uma recomendação de aposta.

| Hipótese | Regra preservada |
|---|---|
| ROI-ALL-v1 | Todos os jogos elegíveis com odd |
| ROI-GREEN60-v1 | Over publicado no instante ≥60% e confiança=0 |
| ROI-DOUBLE60-v1 | Over e BTTS publicados no instante ≥60% e confiança=0 |
| ROI-O70-v1 | Over base no instante ≥70%, sem exigir BTTS/confiança |
| ROI-O70-EV5-v1 | MC-O70 e Over publicado/100 × odd − 1 ≥0,05 |

As cinco séries são exploratórias e sobrepostas. O laboratório mostra n, acertos, odd/probabilidade médias, lucro teórico por unidade, ROI, intervalos, períodos, cobertura, versão e evolução semanal. Os campos BTTS/confiança/versão ausentes em snapshots antigos não são preenchidos com dados atuais. A versão histórica inferida é identificada no CSV.

**BSD é consenso não executável.** O ROI não inclui execução, custos ou garantia de disponibilidade do preço. Uma taxa alta de acerto não demonstra lucro.

[Registo de hipóteses](research/hypotheses.json): EXPERIMENTAL = hipótese sem amostra; NO-GO = integridade falhada ou IC95% ROI inteiramente negativo; INCONCLUSIVO = evidência insuficiente. CANDIDATO e VALIDADO exigem protocolos prospetivos separados e revisão registada, sem atribuição automática.

## Executar e testar

Python **3.12**, a partir da raiz:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python -m pipeline.offline_check
python -m pipeline.integrity --published .
```

Os testes não precisam de token nem de chamadas reais BSD. Para investigação reproduzível:

```bash
pip install -r requirements-research.txt
python -m analysis.calibration_study --db predictions.db --output research/results_2026-11 --plot
```

O estudo não modifica a base nem configurações de produção. Os diagnósticos legados `recalibracao.py` e `calibration_o25.py` também escrevem apenas em investigação. `migrar_ligas.py` é diagnóstico read-only; uma migração histórica nova exige revisão separada.

Execução operacional, apenas num contexto autorizado e com `BSD_API_TOKEN` configurado:

```bash
python -m src.main
python -m pipeline.odds_tracking
python -m pipeline.settlement
python -m analysis.backtest
python -m analysis.roi_report
```

Os comandos antigos `python main.py`, `python odds_tracking.py`, etc. continuam funcionais.

## GitHub Actions e cobertura

Mantém-se o cron **`17 */2 * * *` UTC**: previsões → odds → liquidação → controlos → backtest → ROI → validação de ficheiros → commit → build Pages → confirmação do SHA publicado. Testes correm antes da API. Corridas operacionais e auditoria manual de ligas só escrevem na main, com a mesma exclusão mútua. O PR só executa CI offline; não publica.

Uma etapa crítica falhada impede publicação. Se a main mudar entretanto, não se faz rebase automático da SQLite. Um artefacto de recuperação por um dia preserva a corrida falhada; a conta/quota e o crescimento da base devem ser acompanhados. Não foram ativados serviços pagos.

**Workflow verde não significa cobertura satisfatória.** A baseline recente tem apenas **43,6%** de cobertura de odds. O piso operacional de bloqueio é 25% com n≥20 em sete dias; os protocolos científicos futuros exigem ≥80%. O resumo mostra a cobertura independentemente do estado verde/vermelho.

As permissões e a primeira publicação real do novo workflow só podem ser confirmadas depois de revisão e eventual merge. O PR não faz esse merge.

[Metodologia ROI original](docs/METODOLOGIA_ROI.md) · [Registo histórico de 23/10/2026](docs/ALTERACOES_2026-10-23.md)
