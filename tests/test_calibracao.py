"""
Smoke test da calibração v2 (shrink O25 0.18, shrink BTTS 0.12, teto suave BTTS >58%).

Corre sem rede, sem BSD_API_TOKEN e sem predictions.db:
    python tests/test_calibracao.py
Também é compatível com pytest.
"""
import os
import sys

# Garantir que o Platt de recalibracao.json não é aplicado (testamos só shrink + teto).
os.environ.pop("APPLY_RECAL", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from predictor import calibrar_probabilidades, monte_carlo  # noqa: E402

TOL = 0.01
KW = dict(base_o25=0.52, base_btts=0.52, shrink_o25=0.18, shrink_btts=0.12)


def _entre(valor, lo, hi, nome):
    assert lo - TOL <= valor <= hi + TOL, f"{nome}: {valor} fora de [{lo}, {hi}]"


def test_o25_55_shrink():
    o25, _ = calibrar_probabilidades(55.0, 50.0, **KW)
    _entre(o25, 53.0, 56.0, "O25 55%")
    assert abs(o25 - 54.46) < 0.1, f"O25 55% esperado ~54.5, obtido {o25}"


def test_o25_74_sem_teto():
    o25, _ = calibrar_probabilidades(74.0, 50.0, **KW)
    assert 68.0 < o25 < 74.0, f"O25 74%: {o25} devia estar em ]68, 74["


def test_btts_55_teto_nao_actua():
    _, btts = calibrar_probabilidades(50.0, 55.0, **KW)
    _entre(btts, 54.0, 56.0, "BTTS 55%")


def test_btts_64_teto():
    _, btts = calibrar_probabilidades(50.0, 64.0, **KW)
    _entre(btts, 58.0, 62.0, "BTTS 64%")


def test_btts_73_teto():
    _, btts = calibrar_probabilidades(50.0, 73.0, **KW)
    _entre(btts, 62.0, 68.0, "BTTS 73%")


def test_monte_carlo():
    o25, btts = monte_carlo(1.5, 1.2)
    for nome, v in (("O25", o25), ("BTTS", btts)):
        assert isinstance(v, float), f"monte_carlo {nome} não é float: {type(v)}"
        assert 1.0 < v < 99.0, f"monte_carlo {nome}: {v} fora de ]1, 99["


if __name__ == "__main__":
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    falhas = 0
    for t in testes:
        try:
            t()
            print(f"OK   {t.__name__}")
        except AssertionError as e:
            falhas += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"{len(testes) - falhas}/{len(testes)} passaram")
    sys.exit(1 if falhas else 0)
