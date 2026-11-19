"""Contract tests against the real official calculator.

Run with the calculator up (docker compose up -d):  RTC_URL=... uv run pytest -m contract
Expected values come from the calculator's own documented example (docs/exemplos.md upstream).
"""

import json
from datetime import date
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rtc_mcp import server

pytestmark = pytest.mark.contract
OPERACAO = json.loads((Path(__file__).parent / "fixtures" / "operacao_exemplo.json").read_text(encoding="utf-8"))
OP_2026 = {
    "dhFatoGerador": "2026-06-10T10:00:00-03:00",
    "municipio": 3550308,
    "uf": "SP",
    "itens": [{"numero": 1, "ncm": "84713012", "cst": "000", "cClassTrib": "000001", "baseCalculo": 1000.00,
               "quantidade": 1, "unidade": "UN"}],
}


@pytest.fixture(autouse=True)
def fresh_client():
    server.set_calculadora(None)  # a new client per test, bound to this test's event loop
    yield
    server.set_calculadora(None)


async def test_documented_example_totals_and_item_error():
    out = json.loads(await server.calcular_tributos(OPERACAO))
    tot = out["resultado"]["total"]["tribCalc"]
    assert tot["IBSCBSTot"]["gCBS"]["vCBS"] == "56.76"
    assert tot["IBSCBSTot"]["gIBS"]["vIBS"] == "0.64"
    assert tot["ISTot"]["vIS"] == "15.00"
    item3 = out["resultado"]["objetos"][2]["tribCalc"]["IBSCBS"]["gIBSCBS"]
    assert item3["gCBS"]["vCBS"] == "42.24" and item3["gCBS"]["gRed"]["pAliqEfet"] == "3.52"
    assert out["itensNaoCalculados"][0]["erros"][0]["code"] == "REG-011"


async def test_strict_mode_rejects_whole_operation():
    with pytest.raises(ToolError, match="HTTP 422 - NCM não vinculada"):
        await server.calcular_tributos(OPERACAO, estrito=True)


async def test_2026_uses_official_reference_rates():
    g = json.loads(await server.calcular_tributos(OP_2026))["resultado"]["objetos"][0]["tribCalc"]["IBSCBS"]["gIBSCBS"]
    assert (g["gCBS"]["pCBS"], g["gCBS"]["vCBS"], g["gIBSUF"]["vIBSUF"]) == ("0.90", "9.00", "1.00")
    assert json.loads(await server.consultar_aliquota_referencia("uniao", date(2026, 6, 1))) == {"aliquotaReferencia": 0.9}


async def test_lookups():
    ct = json.loads(await server.consultar_classificacao_tributaria("200028", date(2027, 1, 1)))
    assert ct["percentualReducaoCbs"] == 60.0
    assert json.loads(await server.verificar_enquadramento("200035", date(2027, 1, 1), ncm="02013000"))["valido"] is False
    assert json.loads(await server.consultar_nomenclatura("22030000", date(2027, 1, 1)))["tributadoPeloImpostoSeletivo"]
    assert "REG-011" in await server.descrever_erro("REG-011")
    with pytest.raises(ToolError, match="HTTP 404"):
        await server.consultar_aliquota_referencia("uf", date(2027, 6, 1), 43)


async def test_generated_group_xml_is_valid_and_broken_xml_is_not():
    xml = await server.gerar_xml_grupo(OP_2026)
    assert "<IBSCBS>" in xml
    assert await server.validar_xml(xml) == "valido"
    with pytest.raises(ToolError, match="linha"):
        await server.validar_xml(xml.replace("<vBC>", "<vBCx>").replace("</vBC>", "</vBCx>"))
