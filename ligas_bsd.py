"""
Consulta o endpoint /leagues/ da API da BSD, percorre toda a
paginacao (limit/offset), grava o resultado em ligas_bsd.json e
imprime uma tabela ordenada (id, nome, pais).

Depois compara os IDs/nomes devolvidos pela API com o
LEAGUE_MAP_FALLBACK (o dicionario codificado, usado como fallback)
definido em main.py e mostra tres listas:
  - IDs onde o nome diverge entre a API e o LEAGUE_MAP_FALLBACK
  - IDs que existem na API mas faltam no LEAGUE_MAP_FALLBACK
  - IDs que existem no LEAGUE_MAP_FALLBACK mas ja nao existem na API

Nao altera main.py. Requer o token em BSD_API_TOKEN (mesma variavel
de ambiente e mesmos headers que main.py usa).

Uso:
    python3 ligas_bsd.py
"""
import json
import os
import sys

import requests

from main import LEAGUE_MAP_FALLBACK

API_TOKEN = os.getenv("BSD_API_TOKEN")
BASE_URL = "https://sports.bzzoiro.com/api/v2"
LEAGUES_URL = f"{BASE_URL}/leagues/"

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Accept": "application/json",
}

LIMIT = 100
TIMEOUT = 45
OUTPUT_JSON = "ligas_bsd.json"


def obter_ligas():
    """Percorre a paginacao limit/offset de /leagues/ e devolve a lista
    completa de ligas (um dict por liga, tal como veio da API)."""
    if not API_TOKEN:
        sys.exit("Erro: variavel de ambiente BSD_API_TOKEN nao definida.")

    todas_ligas = []
    url = LEAGUES_URL
    params = {"limit": LIMIT, "offset": 0}

    while url:
        res = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        if res.status_code != 200:
            sys.exit(f"Erro HTTP {res.status_code} em {url}: {res.text[:300]}")

        try:
            data = res.json()
        except Exception as e:
            sys.exit(f"JSON invalido na resposta de {url}: {e}")

        if isinstance(data, dict):
            ligas = data.get("results", data.get("data", []))
            url = data.get("next")
        elif isinstance(data, list):
            ligas = data
            url = None
        else:
            break

        todas_ligas.extend(ligas)
        # 'next' ja vem com os proprios query params (limit/offset) prontos
        params = None
        if not ligas or not url:
            break

    return todas_ligas


def extrair_pais(liga):
    pais = liga.get("country")
    if isinstance(pais, dict):
        return pais.get("name") or pais.get("code") or ""
    if isinstance(pais, str) and pais.strip():
        return pais.strip()
    return liga.get("country_name") or ""


def imprimir_tabela(ligas):
    linhas = []
    for liga in ligas:
        lid = liga.get("id")
        nome = liga.get("name") or ""
        pais = extrair_pais(liga)
        linhas.append((lid, nome, pais))

    linhas.sort(key=lambda x: (x[0] is None, x[0]))

    largura_id = max([len(str(l[0])) for l in linhas] + [2])
    largura_nome = max([len(l[1]) for l in linhas] + [4])
    largura_pais = max([len(l[2]) for l in linhas] + [4])

    cabecalho = f"{'ID':<{largura_id}}  {'Nome':<{largura_nome}}  {'Pais':<{largura_pais}}"
    print(cabecalho)
    print("-" * len(cabecalho))
    for lid, nome, pais in linhas:
        print(f"{str(lid):<{largura_id}}  {nome:<{largura_nome}}  {pais:<{largura_pais}}")

    return linhas


def comparar_com_league_map(ligas):
    api_por_id = {}
    for liga in ligas:
        lid = liga.get("id")
        if lid is None:
            continue
        api_por_id[int(lid)] = liga.get("name") or ""

    ids_api = set(api_por_id)
    ids_mapa = set(LEAGUE_MAP_FALLBACK)

    nomes_divergentes = sorted(
        lid for lid in (ids_api & ids_mapa)
        if api_por_id[lid].strip() != LEAGUE_MAP_FALLBACK[lid].strip()
    )
    faltam_no_mapa = sorted(ids_api - ids_mapa)
    ja_nao_existem_na_api = sorted(ids_mapa - ids_api)

    print("\n=== IDs onde o nome diverge (API vs LEAGUE_MAP_FALLBACK) ===")
    if nomes_divergentes:
        for lid in nomes_divergentes:
            print(f"  {lid}: API={api_por_id[lid]!r}  LEAGUE_MAP_FALLBACK={LEAGUE_MAP_FALLBACK[lid]!r}")
    else:
        print("  (nenhum)")

    print("\n=== IDs na API mas em falta no LEAGUE_MAP_FALLBACK ===")
    if faltam_no_mapa:
        for lid in faltam_no_mapa:
            print(f"  {lid}: {api_por_id[lid]!r}")
    else:
        print("  (nenhum)")

    print("\n=== IDs no LEAGUE_MAP_FALLBACK que ja nao existem na API ===")
    if ja_nao_existem_na_api:
        for lid in ja_nao_existem_na_api:
            print(f"  {lid}: {LEAGUE_MAP_FALLBACK[lid]!r}")
    else:
        print("  (nenhum)")

    return nomes_divergentes, faltam_no_mapa, ja_nao_existem_na_api


def main():
    ligas = obter_ligas()
    print(f"Total de ligas obtidas da API: {len(ligas)}")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(ligas, f, ensure_ascii=False, indent=2)
    print(f"Gravado em {OUTPUT_JSON}")

    print()
    imprimir_tabela(ligas)
    comparar_com_league_map(ligas)


if __name__ == "__main__":
    main()
