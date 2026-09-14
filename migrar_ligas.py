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

from database import DB_NAME
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

# Plantel atual (temporada 2025-26) usado para desambiguar registo a
# registo os dois rotulos que, historicamente, cobriam duas ligas
# diferentes com o mesmo texto.
PREMIER_LEAGUE_ATUAL = {
    "Arsenal", "Aston Villa", "AFC Bournemouth", "Bournemouth", "Brentford",
    "Brighton & Hove Albion", "Brighton", "Burnley", "Chelsea",
    "Crystal Palace", "Everton", "Fulham", "Leeds United", "Liverpool",
    "Liverpool FC", "Manchester City", "Manchester United",
    "Newcastle United", "Nottingham Forest", "Sunderland",
    "Tottenham Hotspur", "West Ham United", "Wolverhampton Wanderers",
}

LALIGA_ATUAL = {
    "Real Madrid", "FC Barcelona", "Barcelona", "Atlético Madrid",
    "Atletico Madrid", "Athletic Club", "Athletic Bilbao", "Real Sociedad",
    "Real Betis", "Villarreal", "Valencia", "Sevilla", "Celta Vigo",
    "Celta de Vigo", "Getafe", "Osasuna", "Girona", "Rayo Vallecano",
    "Mallorca", "RCD Mallorca", "Deportivo Alavés", "Alavés", "Espanyol",
    "RCD Espanyol", "Elche", "Elche CF", "Levante", "Levante UD",
    "Real Oviedo",
}

ROTULOS_AMBIGUOS = {
    "Premier League": {
        "confirmado": 1,
        "outro": 8,
        "plantel": PREMIER_LEAGUE_ATUAL,
    },
    "LaLiga": {
        "confirmado": 3,
        "outro": 39,
        "plantel": LALIGA_ATUAL,
    },
}


def calcular_league_id_liga_id(league_texto):
    m = PADRAO_LIGA_ID.match(league_texto)
    return int(m.group(1)) if m else None


def calcular_league_id_ambiguo(league_texto, home_team, away_team):
    regra = ROTULOS_AMBIGUOS.get(league_texto)
    if regra is None:
        return None
    plantel = regra["plantel"]
    if home_team.strip() in plantel and away_team.strip() in plantel:
        return regra["confirmado"]
    return regra["outro"]


def calcular_propostas(linhas):
    """
    linhas: iteravel de (match_id, league, home_team, away_team, league_id_atual).
    Devolve (propostas, sem_regra) onde:
    - propostas: dict match_id -> (league_texto, league_id_novo) so para
      linhas com league_id_atual NULL e para as quais alguma regra se
      aplica.
    - sem_regra: dict league_texto -> contagem, para linhas com
      league_id_atual NULL e nenhuma regra aplicavel (para o utilizador
      ver o que fica de fora).
    """
    propostas = {}
    sem_regra = {}

    for match_id, league_texto, home_team, away_team, league_id_atual in linhas:
        if league_id_atual is not None:
            continue

        league_id_novo = calcular_league_id_liga_id(league_texto)

        if league_id_novo is None:
            league_id_novo = ROTULOS_UNICOS.get(league_texto)

        if league_id_novo is None and league_texto in ROTULOS_AMBIGUOS:
            league_id_novo = calcular_league_id_ambiguo(league_texto, home_team, away_team)

        if league_id_novo is None:
            sem_regra[league_texto] = sem_regra.get(league_texto, 0) + 1
            continue

        propostas[match_id] = (league_texto, league_id_novo)

    return propostas, sem_regra


def imprimir_relatorio(linhas, propostas, sem_regra):
    print("=" * 70)
    print(f"Total de registos em predictions: {len(linhas)}")

    ja_tinham_id = sum(1 for l in linhas if l[4] is not None)
    print(f"Ja tinham league_id preenchido: {ja_tinham_id}")
    print(f"Vao receber league_id nesta migracao: {len(propostas)}")

    print("\n--- (a) via 'Liga ID N' + (b) rotulos unicos ---")
    contagem_por_id = {}
    for _, (league_texto, league_id_novo) in propostas.items():
        if league_texto not in ROTULOS_AMBIGUOS:
            contagem_por_id.setdefault((league_texto, league_id_novo), 0)
            contagem_por_id[(league_texto, league_id_novo)] += 1
    for (league_texto, league_id_novo), n in sorted(contagem_por_id.items(), key=lambda x: -x[1]):
        print(f"  '{league_texto}' -> league_id={league_id_novo}: {n} registo(s)")

    print("\n--- (c) rotulos ambiguos, desambiguados por plantel ---")
    for league_texto, regra in ROTULOS_AMBIGUOS.items():
        n_confirmado = sum(
            1 for _, (lt, lid) in propostas.items()
            if lt == league_texto and lid == regra["confirmado"]
        )
        n_outro = sum(
            1 for _, (lt, lid) in propostas.items()
            if lt == league_texto and lid == regra["outro"]
        )
        print(
            f"  '{league_texto}': id={regra['confirmado']} (ambas no plantel atual) "
            f"-> {n_confirmado} registo(s) | id={regra['outro']} (pelo menos uma fora) "
            f"-> {n_outro} registo(s)"
        )

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

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(predictions)")
    colunas = {row[1] for row in cursor.fetchall()}
    if "league_id" not in colunas:
        print(
            "❌ ERRO: a coluna league_id nao existe em predictions. "
            "Corre init_db() (database.py) antes de migrar."
        )
        conn.close()
        sys.exit(1)

    cursor.execute("SELECT match_id, league, home_team, away_team, league_id FROM predictions")
    linhas = cursor.fetchall()
    conn.close()

    propostas, sem_regra = calcular_propostas(linhas)
    imprimir_relatorio(linhas, propostas, sem_regra)

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
