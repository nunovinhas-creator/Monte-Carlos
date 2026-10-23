# Monte-Carlos

Previsões de futebol **Over 2,5 e BTTS** usando simulação Monte Carlo e estimativas de força ofensiva e defensiva. O site apresenta probabilidades; não executa apostas.

**Site:** https://nunovinhas-creator.github.io/Monte-Carlos/  
**Relatório ROI:** https://nunovinhas-creator.github.io/Monte-Carlos/roi.html  
**Metodologia completa:** [docs/METODOLOGIA_ROI.md](docs/METODOLOGIA_ROI.md)  
**Alterações verificadas em 23/10/2026:** [docs/ALTERACOES_2026-10-23.md](docs/ALTERACOES_2026-10-23.md)

## Modelo v2 e apresentação original (sem alterações à lógica)

- Probabilidades de Over 2,5 e BTTS a partir do modelo `predictor.py`: estimativas xG/força de equipa, vantagem de casa, forma, médias por liga, ajuste H2H e simulação. O código inclui Dixon–Coles e shrinkage.
- **Regra visual do `index.html`:** Over ≥60% aparece verde; BTTS ≥60% aparece verde; um jogo marcado `baixa_confianca` aparece cinzento com aviso «sem dados», mesmo acima do limiar.
- Estes destaques são **informativos**, não uma estratégia anteriormente comprovada como rentável. A regra de 60% não foi substituída.
- O ajuste adicional de calibração v2 é opcional. `calibracao_o25_v2.json` mantém `ativo=false` porque piorou ligeiramente o Brier Score na validação temporal; o seu retreino não corre nas Actions. Não confundir com a recalibração legada `APPLY_RECAL`.

## Comparação de estratégias de Over 2,5 no painel ROI

Todas são testadas sobre **jogos v2 terminados com odds BSD de consenso registadas antes do início**. Uma unidade hipotética por jogo, sem comissão:

| Linha do relatório | Regra | Origem |
|---|---|---|
| Todos os jogos com odd | Sem limiar de probabilidade | Base de comparação |
| Original: Over verde ≥60% | Over **publicado no instante** ≥60% e `baixa_confianca=0` | Reprodução do destaque visual original, usado como filtro experimental de apostas |
| Original: Over e BTTS verdes ≥60% | Ambas as probabilidades **no instante** ≥60% e `baixa_confianca=0` | Conjunção dos dois destaques originais, testada em separado |
| MC-O70: Over base ≥70% | Over **base no instante** ≥70%, sem exigir BTTS nem filtrar confiança | Filtro experimental adicionado após análise histórica |
| MC-O70 + EV modelado ≥5% | MC-O70 e `probabilidade publicada / 100 × odd − 1 ≥ 0,05` | Hipótese de valor **modelado**, não vantagem real comprovada |

**«Base»** significa probabilidade v2 antes da *calibração adicional opcional*; **«publicada»** é a que aparece no site. A recalibração adicional está atualmente inativa, mas as duas séries são guardadas separadamente.

### Integridade histórica e limitações

- `odds_tracking.py` recolhe odds `odds.over_25_goals` a partir de `/api/v2/events/{id}/odds/`, de partidas `pending` com ID numérico e início nas próximas **6h**. Aceita apenas preços decimais e cotações datadas até **6h** antes da recolha; guarda também timestamp da cotação, instante observado, probabilidades e baixa confiança. Não volta a recolher um jogo com odd válida já guardada.
- `database.init_db()` cria/migra `odds_snapshots`, mantendo as linhas existentes. Os snapshots capturados antes da inclusão de `prob_btts_no_instante` e `baixa_confianca_no_instante` **ficam com NULL**. A simulação original de 60% **exclui** esses casos se faltar alguma variável requerida; nunca completa com os valores atuais de `predictions`.
- `roi_report.py` produz `roi.html` e `roi_jogos.csv` com os cinco conjuntos. As estratégias sobrepõem-se; não é válido somar ROI/lucros. Dados sem partidas liquidadas aparecem **«Sem amostra»**, e não um falso ROI de 0%.
- `odd` é **consenso BSD gratuito**, não a odd disponível numa casa definida. O ROI simulado **não é rentabilidade executável**, não inclui comissão, slippage, limites, impostos ou alterações de cotação. **Não existe backfill artificial** para preços anteriores ao início da recolha.
- A série futura de odds e resultados permite avaliar *hipóteses*: não há garantia de acerto ou rentabilidade; comparação e calibração devem ser avaliadas cronologicamente.

## Execução e GitHub Actions

```bash
export BSD_API_TOKEN='...'
python main.py          # previsões + index.html
python odds_tracking.py # odds pré-jogo: apenas jogos elegíveis
python settlement.py    # resultados finais
python backtest.py      # backtest de probabilidades
python roi_report.py    # roi.html e roi_jogos.csv
python -m pytest -q     # testes sem usar a API
```

O workflow `.github/workflows/monte_carlo.yml` corre **às 00:17, 02:17, 04:17… UTC** (cron `17 */2 * * *`, sujeito à fila do GitHub), também por `workflow_dispatch` e quando código operacional é publicado na `main`. Requer `secrets.BSD_API_TOKEN`; o `GITHUB_TOKEN` tem `contents: write`. Executa previsões → odds → settlement → backtest → ROI → diagnóstico → testes → commit dos artefactos para `main`. Os commits automáticos de dados **não** desencadeiam novos runs por `push` (evita loop). O diagnóstico no Actions Job Summary mostra número de snapshots e odds válidas; a publicação Pages atualiza o site.

Verificação de produção de **23/10/2026, run [39954307779](https://github.com/nunovinhas-creator/Monte-Carlos/actions/runs/39954307779)**: 42 jogos consultados, 34 odds válidas, 8 sem cotação utilizável, geração `roi.html`/CSV, 11 testes positivos, commit automático `07068a0`, GitHub Pages publicado. **Isto valida o pipeline, não o ROI**: nessa primeira execução não havia resultados liquidados para as odds novas. Depois das alterações documentais e dos campos adicionais serão necessários novos jogos para avaliar o cruzamento original Over+BTTS sem fuga de informação.

Detalhes, critérios de qualidade e fórmulas: [docs/METODOLOGIA_ROI.md](docs/METODOLOGIA_ROI.md).