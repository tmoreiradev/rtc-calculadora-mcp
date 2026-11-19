"""Tool tests against a mocked calculator (no Java needed). Fixtures are real calculator answers."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rtc_mcp import server
from rtc_mcp.client import Calculadora

FIX = Path(__file__).parent / "fixtures"
OPERACAO = json.loads((FIX / "operacao_exemplo.json").read_text(encoding="utf-8"))
OBSERVABILIDADE = json.loads((FIX / "observabilidade_exemplo.json").read_text(encoding="utf-8"))


@pytest.fixture
def mock():
    """Install a mock calculator; returns the list of requests it saw and a dict of canned routes."""
    seen: list[httpx.Request] = []
    routes: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path.removeprefix("/api/calculadora")
        if path in routes:
            return routes[path]
        return httpx.Response(404, json={"title": "Nao mapeado no mock", "detail": path})

    server.set_calculadora(Calculadora("http://calc.test/api/calculadora", transport=httpx.MockTransport(handler)))
    yield seen, routes
    server.set_calculadora(None)


async def test_calcular_uses_per_item_endpoint_and_strips_memoria(mock):
    seen, routes = mock
    routes["/observabilidade/regime-geral"] = httpx.Response(
        200, json=OBSERVABILIDADE, headers={"x-warning-dados-simulados": "6"}
    )
    out = json.loads(await server.calcular_tributos(OPERACAO))

    assert seen[0].method == "POST" and json.loads(seen[0].content)["itens"][0]["cClassTrib"] == "200003"
    assert "memoriaCalculo" not in json.dumps(out)
    tot = out["resultado"]["total"]["tribCalc"]["IBSCBSTot"]
    assert tot["gCBS"]["vCBS"] == "56.76" and tot["gIBS"]["vIBS"] == "0.64"
    assert out["itensNaoCalculados"] == [
        {"item": 4, "estado": "INCONSISTENCIA_ENTRADA", "erros": OBSERVABILIDADE["objetos"][3]["erros"]}
    ]
    assert out["avisoDadosSimulados"] == "6"


async def test_calcular_keeps_memoria_when_asked(mock):
    _, routes = mock
    routes["/observabilidade/regime-geral"] = httpx.Response(200, json=OBSERVABILIDADE)
    assert "memoriaCalculo" in await server.calcular_tributos(OPERACAO, incluir_memoria=True)


async def test_calcular_fills_id_and_versao(mock):
    seen, routes = mock
    routes["/regime-geral"] = httpx.Response(200, json={"objetos": []})
    op = {k: v for k, v in OPERACAO.items() if k not in ("id", "versao")}
    await server.calcular_tributos(op, estrito=True)
    sent = json.loads(seen[0].content)
    assert seen[0].url.path.endswith("/calculadora/regime-geral")
    assert sent["versao"] == "1.0.0" and len(sent["id"]) == 32


async def test_calcular_rejects_operation_without_items(mock):
    seen, _ = mock
    with pytest.raises(ToolError, match="itens"):
        await server.calcular_tributos({"municipio": 1})
    assert seen == []


async def test_problem_detail_becomes_readable_tool_error(mock):
    _, routes = mock
    routes["/regime-geral"] = httpx.Response(
        422,
        json={
            "title": "NCM não vinculada",
            "status": 422,
            "detail": "NCM de código 02013000 não vinculada à Classificação Tributária de código 200035 (CBS e IBS)",
        },
    )
    with pytest.raises(ToolError) as exc:
        await server.calcular_tributos(OPERACAO, estrito=True)
    assert str(exc.value).startswith("Calculadora RTC recusou a requisicao: HTTP 422 - NCM não vinculada: NCM de código 02013000")


async def test_non_json_error_is_still_readable(mock):
    _, routes = mock
    routes["/observabilidade/erros"] = httpx.Response(500, text="boom")
    with pytest.raises(ToolError, match="HTTP 500.*boom"):
        await server.descrever_erro("REG-011")


async def test_unreachable_calculator_says_how_to_start_it():
    def down(request):
        raise httpx.ConnectError("refused")

    server.set_calculadora(Calculadora("http://calc.test/api/calculadora", transport=httpx.MockTransport(down)))
    try:
        with pytest.raises(ToolError, match="indisponivel.*docker compose up"):
            await server.descrever_erro("REG-011")
    finally:
        server.set_calculadora(None)


@pytest.mark.parametrize(
    ("call", "path", "params"),
    [
        (lambda: server.descrever_erro("REG-011"), "/observabilidade/erros", {"codigo": "REG-011"}),
        (
            lambda: server.consultar_classificacao_tributaria("200028", date(2027, 1, 1)),
            "/dados-abertos/classificacoes-tributarias/cbs-ibs/class-trib/200028",
            {"data": "2027-01-01"},
        ),
        (
            lambda: server.listar_situacoes_tributarias(date(2027, 1, 1), cst="200"),
            "/dados-abertos/classificacoes-tributarias/cbs-ibs/200",
            {"data": "2027-01-01"},
        ),
        (
            lambda: server.listar_situacoes_tributarias(date(2027, 1, 1), tributo="is"),
            "/dados-abertos/situacoes-tributarias/is",
            {"data": "2027-01-01"},
        ),
        (
            lambda: server.consultar_nomenclatura("122011200", date(2027, 1, 1), tipo="nbs"),
            "/dados-abertos/nbs",
            {"nbs": "122011200", "data": "2027-01-01"},
        ),
        (
            lambda: server.verificar_enquadramento("200035", date(2027, 1, 1), ncm="02013000"),
            "/dados-abertos/classificacoes-tributarias/ncm-aplicavel",
            {"cClassTrib": "200035", "ncm": "02013000", "dataOcorrenciaFatoGerador": "2027-01-01"},
        ),
        (
            lambda: server.consultar_aliquota_referencia("municipio", date(2026, 6, 1), 4314902),
            "/dados-abertos/aliquota-municipio",
            {"codigoMunicipio": "4314902", "data": "2026-06-01"},
        ),
        (
            lambda: server.consultar_aliquota_referencia("uniao", date(2026, 6, 1)),
            "/dados-abertos/aliquota-uniao",
            {"data": "2026-06-01"},
        ),
    ],
)
async def test_lookup_tools_hit_official_endpoints(mock, call, path, params):
    seen, routes = mock
    routes[path] = httpx.Response(200, json={"ok": True})
    assert json.loads(await call()) == {"ok": True}
    assert seen[0].url.path == "/api/calculadora" + path
    assert dict(seen[0].url.params) == params


async def test_lookup_argument_errors_are_caught_before_calling(mock):
    seen, _ = mock
    with pytest.raises(ToolError, match="exatamente um"):
        await server.verificar_enquadramento("200035", date(2027, 1, 1))
    with pytest.raises(ToolError, match="codigo_ibge"):
        await server.consultar_aliquota_referencia("uf", date(2026, 6, 1))
    assert seen == []


async def test_gerar_xml_chains_strict_calc_and_generate(mock):
    seen, routes = mock
    routes["/regime-geral"] = httpx.Response(200, json={"objetos": [{"nObj": 1}]})
    routes["/xml/generate"] = httpx.Response(200, text="<infNFe/>")
    assert await server.gerar_xml_grupo(OPERACAO, tipo="nfce") == "<infNFe/>"
    assert [r.url.path for r in seen] == ["/api/calculadora/regime-geral", "/api/calculadora/xml/generate"]
    assert seen[1].url.params["tipo"] == "nfce" and json.loads(seen[1].content) == {"objetos": [{"nObj": 1}]}


async def test_validar_xml_reports_xsd_error(mock):
    seen, routes = mock
    routes["/xml/validate"] = httpx.Response(200, json=True)
    assert await server.validar_xml("<infNFe/>") == "valido"
    assert seen[0].headers["content-type"] == "application/xml" and seen[0].content == b"<infNFe/>"
    routes["/xml/validate"] = httpx.Response(422, json={"title": "Erro de validação de XML", "detail": "linha 9, coluna 27"})
    with pytest.raises(ToolError, match="linha 9, coluna 27"):
        await server.validar_xml("<x/>")


async def test_server_lists_all_tools():
    names = {t.name for t in await server.mcp.list_tools()}
    assert names == {
        "calcular_tributos",
        "descrever_erro",
        "consultar_classificacao_tributaria",
        "listar_situacoes_tributarias",
        "consultar_nomenclatura",
        "verificar_enquadramento",
        "consultar_aliquota_referencia",
        "gerar_xml_grupo",
        "validar_xml",
    }


async def test_redirect_is_an_error_not_a_valid_answer(mock):
    _, routes = mock
    routes["/xml/validate"] = httpx.Response(302, headers={"Location": "/login"})
    with pytest.raises(ToolError, match="HTTP 302"):
        await server.validar_xml("<invalid/>")
