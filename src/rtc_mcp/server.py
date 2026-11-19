"""MCP server: the Receita Federal's official CBS/IBS/IS calculator as small, deterministic tools.

All arithmetic happens inside the official calculator. This server only shapes
requests, trims the answer (drops the long `memoriaCalculo` texts unless asked)
and turns calculator errors into messages an agent can act on.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal
from uuid import uuid4

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .client import Calculadora, CalculadoraError

INSTRUCTIONS = """\
Ferramentas sobre a calculadora oficial da Reforma Tributaria do Consumo (LC 214/2025),
publicada pela Receita Federal. Use-as em vez de calcular CBS, IBS ou Imposto Seletivo de cabeca.
- calcular_tributos: calcula uma operacao (itens com NCM ou NBS, CST, cClassTrib e base de calculo).
  Em 2026 a calculadora usa as aliquotas de referencia oficiais; a partir de 2027 as aliquotas
  sao obrigatorias em `aliquotasNominais` de cada item.
- Antes de calcular um item com cClassTrib desconhecido, consulte consultar_classificacao_tributaria
  e verificar_enquadramento.
- Os valores devolvidos sao os da calculadora; repasse-os sem recalcular nem arredondar de novo."""

mcp = MCPServer("rtc-calculadora", instructions=INSTRUCTIONS)

_calc: Calculadora | None = None


def calc() -> Calculadora:
    global _calc
    if _calc is None:
        _calc = Calculadora()
    return _calc


def set_calculadora(c: Calculadora | None) -> None:
    """Swap the HTTP client (tests inject one with a mock transport)."""
    global _calc
    _calc = c


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _strip_memoria(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_memoria(v) for k, v in obj.items() if k != "memoriaCalculo"}
    if isinstance(obj, list):
        return [_strip_memoria(v) for v in obj]
    return obj


async def _call(coro):
    try:
        return await coro
    except CalculadoraError as exc:
        raise ToolError(exc.message()) from exc


def _prepare(operacao: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(operacao, dict) or not isinstance(operacao.get("itens"), list) or not operacao["itens"]:
        raise ToolError("`operacao` precisa ser um objeto com a lista `itens` (pelo menos 1 item).")
    op = dict(operacao)
    op.setdefault("id", uuid4().hex)
    op.setdefault("versao", "1.0.0")
    return op


@mcp.tool()
async def calcular_tributos(operacao: dict[str, Any], estrito: bool = False, incluir_memoria: bool = False) -> str:
    """Calcula CBS, IBS (UF e municipio) e Imposto Seletivo de uma operacao com a calculadora oficial.

    `operacao` segue o contrato de POST /regime-geral da calculadora:
    {"dhFatoGerador": "2027-03-10T10:00:00-03:00", "municipio": 4314902 (codigo IBGE), "uf": "RS",
     "itens": [{"numero": 1, "ncm": "10063021" (ou "nbs" para servicos), "cst": "200", "cClassTrib": "200003",
                "baseCalculo": 100.00, "quantidade": 20, "unidade": "KG",
                "aliquotasNominais": {"cbs": 8.8, "ibsEstadual": 0.05, "ibsMunicipal": 0.05},
                "impostoSeletivo": {"cst": "000", "cClassTrib": "000001", "baseCalculo": 150.00,
                                    "quantidade": 12, "unidade": "LT", "impostoInformado": 15.00}}]}
    `aliquotasNominais` e proibido antes de 2027 e obrigatorio a partir de 2027 (valores em %).
    `id` e `versao` sao preenchidos se faltarem.

    Por padrao (estrito=false) cada item e calculado de forma independente: itens com erro vem com
    `estadoItem` = INCONSISTENCIA_ENTRADA e a lista `erros`, e os totais somam so os itens calculados.
    Com estrito=true a operacao inteira falha no primeiro erro.
    Campos do resultado: vBC (base), pCBS/pIBSUF/pIBSMun (aliquotas %), gRed.pAliqEfet (aliquota efetiva
    apos reducao), vCBS, vIBSUF, vIBSMun, vIBS, e no grupo IS: vBCIS, pIS, vIS.
    """
    op = _prepare(operacao)
    path = "/regime-geral" if estrito else "/observabilidade/regime-geral"
    resp = await _call(calc().request("POST", path, json=op))
    body = resp.json()
    out: dict[str, Any] = {"resultado": body if incluir_memoria else _strip_memoria(body)}
    falhas = [
        {"item": o.get("nObj"), "estado": o.get("estadoItem"), "erros": o.get("erros")}
        for o in body.get("objetos", [])
        if o.get("estadoItem") not in (None, "CALCULADO", "CALCULO_SIMULADO")
    ]
    if falhas:
        out["itensNaoCalculados"] = falhas
    if aviso := resp.headers.get("x-warning-dados-simulados"):
        out["avisoDadosSimulados"] = aviso
    return _dump(out)


@mcp.tool()
async def descrever_erro(codigo: str) -> str:
    """Explica um codigo de erro da calculadora (ex.: REG-011), como os que aparecem em `erros[].code`."""
    return _dump(await _call(calc().get_json("/observabilidade/erros", codigo=codigo)))


@mcp.tool()
async def consultar_classificacao_tributaria(
    c_class_trib: str, data: date, tributo: Literal["cbs-ibs", "is"] = "cbs-ibs"
) -> str:
    """Le na tabela oficial uma classificacao tributaria (cClassTrib, 6 digitos) vigente na data:
    descricao, tratamento (ex.: "Aliquota reduzida em 60%"), percentuais de reducao de CBS/IBS,
    nomenclatura exigida (NCM ou NBS) e documentos fiscais em que pode ser usada."""
    path = f"/dados-abertos/classificacoes-tributarias/{tributo}/class-trib/{c_class_trib}"
    return _dump(await _call(calc().get_json(path, data=data.isoformat())))


@mcp.tool()
async def listar_situacoes_tributarias(
    data: date, tributo: Literal["cbs-ibs", "is"] = "cbs-ibs", cst: str | None = None
) -> str:
    """Lista os CST oficiais vigentes na data com suas classificacoes tributarias (cClassTrib).
    Informe `cst` (ex.: "200") para receber so as classificacoes daquele CST."""
    if cst:
        path = f"/dados-abertos/classificacoes-tributarias/{'imposto-seletivo' if tributo == 'is' else 'cbs-ibs'}/{cst}"
    else:
        path = f"/dados-abertos/situacoes-tributarias/{'is' if tributo == 'is' else 'cbs-ibs'}"
    return _dump(await _call(calc().get_json(path, data=data.isoformat())))


@mcp.tool()
async def consultar_nomenclatura(codigo: str, data: date, tipo: Literal["ncm", "nbs"] = "ncm") -> str:
    """Consulta um codigo NCM (mercadoria) ou NBS (servico) na tabela oficial vigente na data.
    Para NCM informa tambem se o produto e tributado pelo Imposto Seletivo."""
    return _dump(await _call(calc().get_json(f"/dados-abertos/{tipo}", **{tipo: codigo, "data": data.isoformat()})))


@mcp.tool()
async def verificar_enquadramento(
    c_class_trib: str, data: date, ncm: str | None = None, nbs: str | None = None
) -> str:
    """Diz se um NCM ou NBS pode ser usado com a classificacao tributaria (cClassTrib) na data.
    Um par invalido faz o calculo do item falhar com REG-011."""
    if bool(ncm) == bool(nbs):
        raise ToolError("Informe exatamente um entre `ncm` e `nbs`.")
    tipo, codigo = ("ncm", ncm) if ncm else ("nbs", nbs)
    return _dump(
        await _call(
            calc().get_json(
                f"/dados-abertos/classificacoes-tributarias/{tipo}-aplicavel",
                cClassTrib=c_class_trib,
                **{tipo: codigo},
                dataOcorrenciaFatoGerador=data.isoformat(),
            )
        )
    )


@mcp.tool()
async def consultar_aliquota_referencia(
    esfera: Literal["uniao", "uf", "municipio"], data: date, codigo_ibge: int | None = None
) -> str:
    """Aliquota de referencia oficial (%): uniao = CBS, uf = IBS estadual, municipio = IBS municipal.
    `codigo_ibge` e o codigo da UF (ex.: 43) ou do municipio (ex.: 4314902). A tabela oficial so tem
    aliquotas para 2026; a partir de 2027 a consulta falha e as aliquotas vao em `aliquotasNominais`."""
    params: dict[str, Any] = {"data": data.isoformat()}
    if esfera != "uniao":
        if codigo_ibge is None:
            raise ToolError(f"`codigo_ibge` e obrigatorio para esfera={esfera}.")
        params["codigoUf" if esfera == "uf" else "codigoMunicipio"] = codigo_ibge
    return _dump(await _call(calc().get_json(f"/dados-abertos/aliquota-{esfera}", **params)))


TipoDoc = Literal["nfe", "nfce", "cte", "cte-simplificado", "bpe", "bpe-tm", "nf3e"]


@mcp.tool()
async def gerar_xml_grupo(operacao: dict[str, Any], tipo: TipoDoc = "nfe") -> str:
    """Calcula a operacao em modo estrito e gera o XML oficial dos grupos de tributacao
    (IBSCBS/IS por item e totais) para o documento `tipo`. Mesmo formato de `operacao` de calcular_tributos."""
    resultado = await _call(calc().post_json("/regime-geral", _prepare(operacao)))
    resp = await _call(calc().request("POST", "/xml/generate", params={"tipo": tipo}, json=resultado))
    return resp.text


@mcp.tool()
async def validar_xml(xml: str, tipo: TipoDoc | Literal["nfse"] = "nfe", subtipo: Literal["grupo", "nota"] = "grupo") -> str:
    """Valida um XML contra o XSD oficial. subtipo="grupo" valida so os grupos de tributacao
    (como os de gerar_xml_grupo); "nota" valida o documento inteiro. Devolve "valido" ou o erro com linha e coluna."""
    await _call(
        calc().request(
            "POST",
            "/xml/validate",
            params={"tipo": tipo, "subtipo": subtipo},
            content=xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
        )
    )
    return "valido"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
