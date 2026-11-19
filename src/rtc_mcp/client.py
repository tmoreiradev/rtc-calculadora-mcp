"""Thin HTTP client for the Receita Federal's RTC calculator (calculadora-rtc-regime-regular).

Every non-2xx answer becomes a CalculadoraError carrying the calculator's own
problem-detail fields, so the MCP layer can hand the agent a readable message.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_URL = "http://localhost:8080/api/calculadora"


class CalculadoraError(Exception):
    """The calculator answered with an error, or could not be reached."""

    def __init__(self, status: int | None, title: str, detail: str = "", pointer: str | None = None):
        self.status, self.title, self.detail, self.pointer = status, title, detail, pointer
        super().__init__(self.message())

    def message(self) -> str:
        if self.status is None:
            return f"Calculadora RTC indisponivel: {self.detail}"
        tail = f" (campo: {self.pointer})" if self.pointer else ""
        return f"Calculadora RTC recusou a requisicao: HTTP {self.status} - {self.title}: {self.detail}{tail}"


class Calculadora:
    def __init__(self, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = (base_url or os.environ.get("RTC_URL") or DEFAULT_URL).rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30, transport=transport)

    async def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            resp = await self._http.request(method, path, **kw)
        except httpx.TransportError as exc:
            raise CalculadoraError(
                None,
                "indisponivel",
                f"nao foi possivel conectar em {self.base_url} ({type(exc).__name__}). "
                "Suba a calculadora oficial com `docker compose up -d` e confira RTC_URL.",
            ) from exc
        if not resp.is_success:  # 3xx too: a redirect is never a calculator answer
            raise _to_error(resp)
        return resp

    async def get_json(self, path: str, **params: Any) -> Any:
        return (await self.request("GET", path, params=params)).json()

    async def post_json(self, path: str, body: Any, **params: Any) -> Any:
        return (await self.request("POST", path, json=body, params=params)).json()

    async def aclose(self) -> None:
        await self._http.aclose()


def _to_error(resp: httpx.Response) -> CalculadoraError:
    try:
        body = resp.json()
    except ValueError:
        return CalculadoraError(resp.status_code, resp.reason_phrase or "Erro", resp.text[:500])
    if not isinstance(body, dict):
        return CalculadoraError(resp.status_code, resp.reason_phrase or "Erro", str(body)[:500])
    return CalculadoraError(
        resp.status_code,
        body.get("title") or resp.reason_phrase or "Erro",
        body.get("detail") or "",
        body.get("pointer"),
    )
