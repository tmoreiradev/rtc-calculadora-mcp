"""Answer key: run every scenario through the official calculator and keep, per item, either the
values (vCBS, vIBSUF, vIBSMun, vIS) or the error code. `--check` recomputes and diffs instead of writing."""

import json
import os
import sys
from pathlib import Path

import httpx

from .scenarios import SCENARIOS

OUT = Path(__file__).parent / "gabarito.json"
URL = os.environ.get("RTC_URL", "http://localhost:8080/api/calculadora").rstrip("/")


def expected(resp: dict) -> list[dict]:
    itens = []
    for o in resp["objetos"]:
        item = {"numero": o["nObj"], "estado": o["estadoItem"]}
        if o.get("erros"):
            item["erro"] = o["erros"][0]["code"]
        else:
            g = o["tribCalc"]["IBSCBS"]["gIBSCBS"]
            item |= {"vCBS": g["gCBS"]["vCBS"], "vIBSUF": g["gIBSUF"]["vIBSUF"], "vIBSMun": g["gIBSMun"]["vIBSMun"]}
            if "IS" in o["tribCalc"]:
                item["vIS"] = o["tribCalc"]["IS"]["vIS"]
        itens.append(item)
    return itens


def build() -> dict:
    out = {}
    with httpx.Client(timeout=30) as http:
        versao = http.get(f"{URL}/dados-abertos/versao").json()
        for s in SCENARIOS:
            body = {"id": s["id"], "versao": "1.0.0"} | s["operacao"]
            resp = http.post(f"{URL}/observabilidade/regime-geral", json=body)
            if resp.status_code != 200:  # scenarios must be scorable item by item
                sys.exit(f"{s['id']}: calculator rejected the whole operation: HTTP {resp.status_code} {resp.text[:300]}")
            out[s["id"]] = {"itens": expected(resp.json())}
    return {"calculadora": {k: versao[k] for k in ("versaoApp", "versaoDb")}, "cenarios": out}


if __name__ == "__main__":
    new = build()
    if "--check" in sys.argv:
        old = json.loads(OUT.read_text(encoding="utf-8"))
        if old != new:
            sys.exit("gabarito.json differs from what the calculator returns now")
        print(f"gabarito.json matches the calculator ({len(new['cenarios'])} scenarios)")
    else:
        OUT.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(new, ensure_ascii=False))
