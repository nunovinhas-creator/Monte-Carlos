"""
Migracao pontual de predictions.db: preenche league_id nas previsoes
antigas (antes de a coluna existir) e reescreve o texto de league para
ficar coerente com o LEAGUE_MAP.

Por omissao corre em modo simulacao (so mostra o que faria). Só
escreve na base de dados com --aplicar, e nesse caso faz primeiro uma
copia de seguranca para predictions.db.bak.

Uso:
    python3 migrar_ligas.py            # simulacao (nao escreve nada)
    python3 migrar_ligas.py --aplicar  # aplica as alteracoes
"""
import argparse
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict

from database import DB_NAME, init_db
from main import LEAGUE_MAP

BACKUP_NAME = DB_NAME + ".bak"

PADRAO_LIGA_ID = re.compile(r"^Liga ID (\d+)$")

# Rotulos antigos que, historicamente, so foram usados para um unico
# league_id (confirmado pela correcao do LEAGUE_MAP em #15).
ROTULOS_UNICOS = {
    "Tercera RFEF": 79,
    "Liga Argentina": 85,
    "UEFA Conference League": 83,
    "Liga BetPlay": 80,
    "Championship": 9,
    "EFL Championship / Cup": 40,
    "J1 League": 49,
    "LaLiga 2": 38,
    "Chinese Super League": 52,
    "Liga Portugal": 94,
    "A-League": 70,
    "Brasileirão Série A": 35,
}

# IDs ja corretamente identificados (via 'Liga ID N' ou ROTULOS_UNICOS)
# usados para construir, a partir dos proprios dados, o conjunto de
# clubes ingleses -- em vez de manter um plantel atual codificado a
# mao (que fica desatualizado a cada mercado de transferencias).
IDS_INGLESES = {12, 40, 86, 87, 91}  # Championship, Carabao Cup, League One/Two, National League

# Parametros do metodo de clustering para o rotulo ambiguo "LaLiga"
# (ver resolver_laliga_por_clusters): nomes de clube nao sao fiaveis
# aqui porque os ids 38/36 usam a forma formal ("Club Atlético de
# Madrid") e os registos "LaLiga" usam a forma curta ("Atlético
# Madrid") -- por isso desambiguamos pelo padrao de recorrencia dos
# proprios 205 jogos, nao por nomes.
LALIGA_NUCLEO_MIN_APARICOES = 3
LALIGA_COMPONENTE_MIN = 18
LALIGA_COMPONENTE_MAX = 22


def calcular_league_id_liga_id(league_texto):
    m = PADRAO_LIGA_ID.match(league_texto)
    return int(m.group(1)) if m else None


def calcular_league_id_nao_ambiguo(league_texto):
    league_id_novo = calcular_league_id_liga_id(league_texto)
    if league_id_novo is None:
        league_id_novo = ROTULOS_UNICOS.get(league_texto)
    return league_id_novo


def construir_conjunto_ingleses(linhas):
    """
    Constroi o conjunto de clubes ingleses a partir dos proprios
    registos ja identificados em predictions.db (IDS_INGLESES),
    incluindo tanto os que ja tinham league_id preenchido como os que
    a regra (a)/(b) desta migracao resolve.
    """
    ingleses = set()
    for _, league_texto, home_team, away_team, league_id_atual in linhas:
        league_id = league_id_atual if league_id_atual is not None else calcular_league_id_nao_ambiguo(league_texto)
        if league_id in IDS_INGLESES:
            ingleses.add(home_team.strip())
            ingleses.add(away_team.strip())
    return ingleses


def calcular_league_id_premier_league(home_team, away_team, ingleses):
    if home_team.strip() in ingleses and away_team.strip() in ingleses:
        return 1
    return 8


def _find(parent, x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union(parent, x, y):
    rx, ry = _find(parent, x), _find(parent, y)
    if rx != ry:
        parent[rx] = ry


def resolver_laliga_por_clusters(linhas_laliga):
    """
    linhas_laliga: lista de (match_id, home_team, away_team) para os
    registos com o rotulo ambiguo "LaLiga" ainda sem league_id.

    Em vez de nomes/plantel, agrupa pelo padrao de recorrencia dos
    proprios jogos:
    - conta quantas vezes cada equipa aparece (casa+fora) nestes jogos;
    - nucleo = jogos em que as duas equipas aparecem >= LALIGA_NUCLEO_MIN_APARICOES vezes;
    - constroi o grafo do nucleo (equipas=nos, jogos=arestas) e extrai
      as componentes conexas;
    - a maior componente e a La Liga -> id 3; tudo o resto (outras
      componentes do nucleo + jogos fora do nucleo) -> id 39.

    Devolve (propostas, diagnostico):
    - propostas: dict match_id -> league_id_novo (3 ou 39), ou None se
      alguma das duas asserções de validacao falhar.
    - diagnostico: dict com 'total', 'nucleo', 'mistos' (lista de
      jogos com uma equipa >=N e outra <N -- tem de ser vazia),
      'componentes' (lista de conjuntos de equipas, maior primeiro) e
      'maior_componente'.
    """
    contagem = Counter()
    for _, home, away in linhas_laliga:
        contagem[home] += 1
        contagem[away] += 1

    nucleo = [
        (mid, h, a) for mid, h, a in linhas_laliga
        if contagem[h] >= LALIGA_NUCLEO_MIN_APARICOES and contagem[a] >= LALIGA_NUCLEO_MIN_APARICOES
    ]
    mistos = [
        (mid, h, a) for mid, h, a in linhas_laliga
        if (contagem[h] >= LALIGA_NUCLEO_MIN_APARICOES) != (contagem[a] >= LALIGA_NUCLEO_MIN_APARICOES)
    ]

    diagnostico = {
        "total": len(linhas_laliga),
        "nucleo": len(nucleo),
        "mistos": mistos,
        "contagem": contagem,
        "componentes": [],
        "maior_componente": None,
    }

    if mistos:
        return None, diagnostico

    parent = {}
    for _, h, a in nucleo:
        parent.setdefault(h, h)
        parent.setdefault(a, a)
        _union(parent, h, a)

    componentes = defaultdict(set)
    for equipa in parent:
        componentes[_find(parent, equipa)].add(equipa)

    componentes_ordenadas = sorted(componentes.values(), key=len, reverse=True)
    maior = componentes_ordenadas[0] if componentes_ordenadas else set()

    diagnostico["componentes"] = componentes_ordenadas
    diagnostico["maior_componente"] = maior

    if not (LALIGA_COMPONENTE_MIN <= len(maior) <= LALIGA_COMPONENTE_MAX):
        return None, diagnostico

    propostas = {
        mid: (3 if (h in maior and a in maior) else 39)
        for mid, h, a in linhas_laliga
    }
    return propostas, diagnostico


def imprimir_diagnostico_laliga_e_abortar(diagnostico):
    print("\n" + "!" * 70)
    print("❌ VALIDACAO DO CLUSTERING 'LaLiga' FALHOU -- A ABORTAR SEM ESCREVER NADA")
    print("!" * 70)
    print(f"Total de registos 'LaLiga' por resolver: {diagnostico['total']}")
    print(f"Nucleo (ambas as equipas >= {LALIGA_NUCLEO_MIN_APARICOES} aparicoes): {diagnostico['nucleo']}")

    if diagnostico["mistos"]:
        print(f"\nAssercao 1 FALHOU: {len(diagnostico['mistos'])} jogo(s) misto(s) "
              f"(uma equipa >= {LALIGA_NUCLEO_MIN_APARICOES}, outra < {LALIGA_NUCLEO_MIN_APARICOES}), "
              f"esperado 0:")
        contagem = diagnostico["contagem"]
        for _, h, a in diagnostico["mistos"]:
            print(f"  - {h} ({contagem[h]}x) vs {a} ({contagem[a]}x)")
        print("=" * 70)
        sys.exit(1)

    maior = diagnostico["maior_componente"] or set()
    print(f"\nAssercao 2 FALHOU: maior componente tem {len(maior)} equipa(s), "
          f"esperado entre {LALIGA_COMPONENTE_MIN} e {LALIGA_COMPONENTE_MAX}:")
    for i, comp in enumerate(diagnostico["componentes"], 1):
        print(f"  Componente {i} ({len(comp)} equipas): {sorted(comp)}")
    print("=" * 70)
    sys.exit(1)


def calcular_propostas(linhas):
    """
    linhas: iteravel de (match_id, league, home_team, away_team, league_id_atual).
    Devolve (propostas, sem_regra, ingleses, diagnostico_laliga) onde:
    - propostas: dict match_id -> (league_texto, league_id_novo) so para
      linhas com league_id_atual NULL e para as quais alguma regra se
      aplica.
    - sem_regra: dict league_texto -> contagem, para linhas com
      league_id_atual NULL e nenhuma regra aplicavel.
    - ingleses: conjunto de clubes usado para desambiguar "Premier League".
    - diagnostico_laliga: diagnostico de resolver_laliga_por_clusters()
      para o rotulo "LaLiga" (None se nao havia nenhum registo por resolver).

    Se a validacao do clustering "LaLiga" falhar, o chamador tem de
    abortar antes de escrever -- ver imprimir_diagnostico_laliga_e_abortar.
    """
    ingleses = construir_conjunto_ingleses(linhas)

    linhas_laliga = [
        (match_id, home_team.strip(), away_team.strip())
        for match_id, league_texto, home_team, away_team, league_id_atual in linhas
        if league_texto == "LaLiga" and league_id_atual is None
    ]
    propostas_laliga = {}
    diagnostico_laliga = None
    if linhas_laliga:
        propostas_laliga, diagnostico_laliga = resolver_laliga_por_clusters(linhas_laliga)

    propostas = {}
    sem_regra = {}

    for match_id, league_texto, home_team, away_team, league_id_atual in linhas:
        if league_id_atual is not None:
            continue

        if league_texto == "LaLiga":
            if propostas_laliga is not None:
                propostas[match_id] = (league_texto, propostas_laliga[match_id])
            continue

        league_id_novo = calcular_league_id_nao_ambiguo(league_texto)

        if league_id_novo is None and league_texto == "Premier League":
            league_id_novo = calcular_league_id_premier_league(home_team, away_team, ingleses)

        if league_id_novo is None:
            sem_regra[league_texto] = sem_regra.get(league_texto, 0) + 1
            continue

        propostas[match_id] = (league_texto, league_id_novo)

    return propostas, sem_regra, ingleses, diagnostico_laliga


def imprimir_relatorio(linhas, propostas, sem_regra, ingleses, diagnostico_laliga):
    print("=" * 70)
    print(f"Total de registos em predictions: {len(linhas)}")

    ja_tinham_id = sum(1 for l in linhas if l[4] is not None)
    print(f"Ja tinham league_id preenchido: {ja_tinham_id}")
    print(f"Vao receber league_id nesta migracao: {len(propostas)}")

    print("\n--- (a) via 'Liga ID N' + (b) rotulos unicos ---")
    contagem_por_id = {}
    for _, (league_texto, league_id_novo) in propostas.items():
        if league_texto not in ("Premier League", "LaLiga"):
            contagem_por_id.setdefault((league_texto, league_id_novo), 0)
            contagem_por_id[(league_texto, league_id_novo)] += 1
    for (league_texto, league_id_novo), n in sorted(contagem_por_id.items(), key=lambda x: -x[1]):
        print(f"  '{league_texto}' -> league_id={league_id_novo}: {n} registo(s)")

    print("\n--- (c1) 'Premier League' desambiguado por nacionalidade das equipas ---")
    print(f"  (conjunto 'ingleses' construido a partir dos ids {sorted(IDS_INGLESES)}: "
          f"{len(ingleses)} clubes distintos)")
    n1 = sum(1 for _, (lt, lid) in propostas.items() if lt == "Premier League" and lid == 1)
    n8 = sum(1 for _, (lt, lid) in propostas.items() if lt == "Premier League" and lid == 8)
    print(f"  'Premier League': id=1 (ambas inglesas) -> {n1} registo(s) | "
          f"id=8 (senao) -> {n8} registo(s)")

    print("\n--- (c2) 'LaLiga' desambiguado por clustering de recorrencia ---")
    if diagnostico_laliga is not None:
        maior = diagnostico_laliga["maior_componente"] or set()
        print(f"  nucleo (ambas >= {LALIGA_NUCLEO_MIN_APARICOES} aparicoes): "
              f"{diagnostico_laliga['nucleo']}/{diagnostico_laliga['total']} jogos")
        print(f"  maior componente ({len(maior)} equipas): {sorted(maior)}")
        if len(diagnostico_laliga["componentes"]) > 1:
            print("  outras componentes:")
            for comp in diagnostico_laliga["componentes"][1:]:
                print(f"    - {sorted(comp)}")
    n3 = sum(1 for _, (lt, lid) in propostas.items() if lt == "LaLiga" and lid == 3)
    n39 = sum(1 for _, (lt, lid) in propostas.items() if lt == "LaLiga" and lid == 39)
    print(f"  'LaLiga': id=3 (maior componente) -> {n3} registo(s) | "
          f"id=39 (senao) -> {n39} registo(s)")

    if sem_regra:
        print("\n--- Rotulos sem regra aplicavel (league_id fica NULL) ---")
        for league_texto, n in sorted(sem_regra.items(), key=lambda x: -x[1]):
            print(f"  '{league_texto}': {n} registo(s)")

    print("\n--- (d) reescrita do texto da liga a partir do LEAGUE_MAP ---")
    league_id_final = {}
    for match_id, league_texto, _, _, league_id_atual in linhas:
        if league_id_atual is not None:
            league_id_final[match_id] = league_id_atual
    for match_id, (_, league_id_novo) in propostas.items():
        league_id_final[match_id] = league_id_novo

    mudancas_texto = {}
    for match_id, league_texto, _, _, _ in linhas:
        lid = league_id_final.get(match_id)
        if lid is None:
            continue
        nome_novo = LEAGUE_MAP.get(lid)
        if nome_novo is None or nome_novo == league_texto:
            continue
        chave = (league_texto, nome_novo, lid)
        mudancas_texto[chave] = mudancas_texto.get(chave, 0) + 1

    if not mudancas_texto:
        print("  (nenhuma mudanca de texto necessaria)")
    else:
        for (antigo, novo, lid), n in sorted(mudancas_texto.items(), key=lambda x: -x[1]):
            print(f"  '{antigo}' -> '{novo}' (league_id={lid}): {n} registo(s)")

    print("=" * 70)


def imprimir_verificacoes_controlo(linhas, propostas, ingleses):
    """
    linhas: as mesmas (match_id, league, home_team, away_team, league_id_atual)
    lidas da base. propostas: resultado de calcular_propostas().
    """
    equipas_por_match = {
        match_id: (home_team.strip(), away_team.strip())
        for match_id, _, home_team, away_team, _ in linhas
    }

    def pares_distintos(league_texto_alvo, lid_alvo, filtro=None):
        pares = set()
        for match_id, (league_texto, lid) in propostas.items():
            if league_texto != league_texto_alvo or lid != lid_alvo:
                continue
            home, away = equipas_por_match[match_id]
            if filtro is not None and not filtro(home, away):
                continue
            pares.add((home, away))
        return sorted(pares)

    print("\n" + "=" * 70)
    print("VERIFICACOES DE CONTROLO")
    print("=" * 70)

    print("\n1) id=8 (Premier League ambiguo, 'senao') com as duas equipas em "
          "'ingleses' -- esperado: 0")
    encontrados = pares_distintos(
        "Premier League", 8,
        filtro=lambda home, away: home in ingleses and away in ingleses,
    )
    if not encontrados:
        print("   OK -- nenhum registo encontrado.")
    else:
        for home, away in encontrados:
            print(f"   - {home} vs {away}")

    print("\n2) id=39 (LaLiga, 'senao') com as duas equipas na maior componente "
          "(La Liga) -- esperado: 0")
    ids_por_match = {mid: lid for mid, (lt, lid) in propostas.items() if lt == "LaLiga"}
    laliga_ids_3 = {mid for mid, lid in ids_por_match.items() if lid == 3}
    maior_componente_equipas = set()
    for mid in laliga_ids_3:
        h, a = equipas_por_match[mid]
        maior_componente_equipas.add(h)
        maior_componente_equipas.add(a)
    encontrados = pares_distintos(
        "LaLiga", 39,
        filtro=lambda home, away: home in maior_componente_equipas and away in maior_componente_equipas,
    )
    if not encontrados:
        print("   OK -- nenhum registo encontrado.")
    else:
        for home, away in encontrados:
            print(f"   - {home} vs {away}")

    print("\n3) id=8 (Premier League ambiguo, 'senao') SEM nenhuma equipa em "
          "'ingleses' -- para inspecionar manualmente se algum destes pares e, "
          "na realidade, ingles (falha de cobertura do conjunto 'ingleses')")
    encontrados = pares_distintos(
        "Premier League", 8,
        filtro=lambda home, away: home not in ingleses and away not in ingleses,
    )
    if not encontrados:
        print("   (nenhum -- todos os registos em id=8 tem pelo menos uma equipa "
              "em 'ingleses')")
    else:
        for home, away in encontrados:
            print(f"   - {home} vs {away}")

    print("=" * 70)


def aplicar_migracao(conn, linhas, propostas):
    cursor = conn.cursor()

    cursor.executemany(
        "UPDATE predictions SET league_id = ? WHERE match_id = ?",
        [(league_id_novo, match_id) for match_id, (_, league_id_novo) in propostas.items()],
    )

    cursor.execute("SELECT DISTINCT league_id FROM predictions WHERE league_id IS NOT NULL")
    for (lid,) in cursor.fetchall():
        nome_novo = LEAGUE_MAP.get(lid)
        if nome_novo is None:
            continue
        cursor.execute(
            "UPDATE predictions SET league = ? WHERE league_id = ? AND league != ?",
            (nome_novo, lid, nome_novo),
        )

    conn.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--aplicar", action="store_true",
        help="Escreve as alteracoes na base de dados (por omissao corre so em simulacao).",
    )
    args = parser.parse_args()

    init_db()

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT match_id, league, home_team, away_team, league_id FROM predictions")
    linhas = cursor.fetchall()
    conn.close()

    propostas, sem_regra, ingleses, diagnostico_laliga = calcular_propostas(linhas)

    if diagnostico_laliga is not None and diagnostico_laliga["maior_componente"] is None:
        imprimir_diagnostico_laliga_e_abortar(diagnostico_laliga)
    if diagnostico_laliga is not None:
        maior = diagnostico_laliga["maior_componente"]
        if not (LALIGA_COMPONENTE_MIN <= len(maior) <= LALIGA_COMPONENTE_MAX):
            imprimir_diagnostico_laliga_e_abortar(diagnostico_laliga)

    imprimir_relatorio(linhas, propostas, sem_regra, ingleses, diagnostico_laliga)
    imprimir_verificacoes_controlo(linhas, propostas, ingleses)

    if not args.aplicar:
        print("\nModo simulacao (nao foi escrito nada). Corre com --aplicar para gravar.")
        return

    print(f"\nA copiar '{DB_NAME}' para '{BACKUP_NAME}' antes de aplicar...")
    shutil.copy2(DB_NAME, BACKUP_NAME)

    conn = sqlite3.connect(DB_NAME)
    aplicar_migracao(conn, linhas, propostas)
    conn.close()

    print(f"✅ Migracao aplicada. Copia de seguranca em '{BACKUP_NAME}'.")


if __name__ == "__main__":
    main()
