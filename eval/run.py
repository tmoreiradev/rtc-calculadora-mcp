"""Ask Claude Code (`claude -p`) every scenario twice: alone, and with this MCP server.

    uv run python -m eval.run --mode baseline
    uv run python -m eval.run --mode mcp        # needs the calculator up (RTC_URL)

Both modes get the same system prompt and the same user prompt. The run is isolated from the
operator's own setup: empty temp cwd, no setting sources (no CLAUDE.md, hooks or user MCP servers),
no built-in tools. Only a whitelist of fields of each JSON result is saved to eval/results/<mode>/.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .scenarios import SCENARIOS

RESULTS = Path(__file__).parent / "results"

SYSTEM = (
    "Voce e um assistente de apuracao tributaria da Reforma Tributaria do Consumo "
    "(CBS, IBS e Imposto Seletivo, LC 214/2025). Responda apenas com o JSON pedido."
)

PROMPT = """Calcule os tributos desta operacao do regime regular. Para cada item, informe vCBS, vIBSUF e vIBSMun \
em reais com 2 casas decimais e, se o item tiver Imposto Seletivo, tambem vIS. Se um item nao puder ser calculado \
porque os dados sao inconsistentes ou invalidos segundo as regras oficiais, devolva "erro" com o motivo em vez dos valores.

Operacao (no formato de entrada da calculadora oficial da Receita Federal):
```json
{operacao}
```

Responda SOMENTE com um JSON neste formato, sem texto antes ou depois:
{{"itens": [{{"numero": 1, "vCBS": "0.00", "vIBSUF": "0.00", "vIBSMun": "0.00", "vIS": "0.00", "erro": null}}]}}"""

KEEP_USAGE = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def mcp_config(tmp: Path, mode: str) -> Path:
    servers = {}
    if mode == "mcp":
        env = {"RTC_URL": os.environ.get("RTC_URL", "http://localhost:8080/api/calculadora")}
        servers["rtc"] = {"command": sys.executable, "args": ["-m", "rtc_mcp.server"], "env": env}
    path = tmp / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def run_one(scenario: dict, mode: str, model: str, cfg: Path, cwd: Path) -> dict:
    prompt = PROMPT.format(operacao=json.dumps(scenario["operacao"], ensure_ascii=False, indent=1))
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--system-prompt", SYSTEM,
        "--setting-sources", "",
        "--strict-mcp-config", "--mcp-config", str(cfg),
        "--tools", "",
        "--no-session-persistence",
    ]
    if mode == "mcp":
        cmd += ["--allowedTools", "mcp__rtc"]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=600)
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"id": scenario["id"], "mode": mode, "is_error": True, "result": None,
                "cli_error": (proc.stderr or proc.stdout)[-500:]}
    usage = raw.get("usage") or {}
    return {
        "id": scenario["id"],
        "mode": mode,
        "models": sorted((raw.get("modelUsage") or {}).keys()),
        "result": raw.get("result"),
        "is_error": raw.get("is_error"),
        "num_turns": raw.get("num_turns"),
        "duration_ms": raw.get("duration_ms"),
        "total_cost_usd": raw.get("total_cost_usd"),
        "usage": {k: usage.get(k, 0) for k in KEEP_USAGE},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["baseline", "mcp"], required=True)
    ap.add_argument("--model", default="claude-sonnet-5-5")  # the model of the published run
    ap.add_argument("--only", nargs="*", help="scenario ids")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    out_dir = RESULTS / args.mode
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [s for s in SCENARIOS if not args.only or s["id"] in args.only]
    with tempfile.TemporaryDirectory(prefix="rtc-eval-") as tmp:
        tmp = Path(tmp)
        cfg = mcp_config(tmp, args.mode)
        cwd = tmp / "cwd"
        cwd.mkdir()
        with ThreadPoolExecutor(args.jobs) as pool:
            for rec in pool.map(lambda s: run_one(s, args.mode, args.model, cfg, cwd), todo):
                (out_dir / f"{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                print(rec["id"], rec.get("num_turns"), rec.get("total_cost_usd"), (rec.get("result") or rec.get("cli_error") or "")[:80].replace("\n", " "))


if __name__ == "__main__":
    main()
