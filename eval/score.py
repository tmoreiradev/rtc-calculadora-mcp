"""Score eval/results/<mode>/*.json against eval/gabarito.json and write eval/RESULTS.md.

    uv run python -m eval.score           # rewrite RESULTS.md (+ per-item detail)
    uv run python -m eval.score --check   # fail if RESULTS.md is stale (used in CI)
"""

import json
import re
import statistics
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).parent
MODES = ("baseline", "mcp")
FIELDS = ("vCBS", "vIBSUF", "vIBSMun", "vIS")
TOL = Decimal("0.01")


def parse_answer(text: str | None) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        ans = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    if not isinstance(ans, dict) or not isinstance(ans.get("itens"), list):
        return None
    ok = all(isinstance(i, dict) and type(i.get("numero")) is int for i in ans["itens"])
    return ans if ok else None


def money(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v).replace("R$", "").strip())
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def score_item(exp: dict, got: dict | None) -> dict:
    """One gabarito item vs the model's item. Values are compared within one cent."""
    got = got or {}
    has_values = any(money(got.get(f)) is not None for f in FIELDS)
    said_error = bool(got.get("erro"))
    if "erro" in exp:  # the calculator rejects this item
        return {"kind": "error", "detected": said_error and not has_values, "hallucinated": has_values}
    fields = [f for f in FIELDS if f in exp]
    res = {"kind": "calc", "rejected": said_error and not has_values, "said_error": said_error, "correct": 0, "exact": 0, "wrong": 0, "missing": 0}
    for f in fields:
        g, e = money(got.get(f)), Decimal(exp[f])
        if g is None:
            res["missing"] += 1
        elif abs(g - e) <= TOL:
            res["correct"] += 1
            res["exact"] += g == e
        else:
            res["wrong"] += 1
    res["extra_is"] = "vIS" not in exp and money(got.get("vIS")) not in (None, Decimal(0))
    return res


def score_mode(mode: str, gabarito: dict) -> tuple[dict, list[dict]]:
    tot = dict(fields=0, correct=0, exact=0, wrong=0, missing=0, calc_items=0, rejected=0, extra_is=0,
               err_items=0, detected=0, hallucinated=0, scenarios=0, perfect=0, unparsed=0)
    cost, tokens_in, tokens_out, turns, secs, rows = 0.0, 0, 0, [], [], []
    for sid, exp in gabarito["cenarios"].items():
        rec = json.loads((HERE / "results" / mode / f"{sid}.json").read_text(encoding="utf-8"))
        ans = parse_answer(rec.get("result"))
        tot["scenarios"] += 1
        tot["unparsed"] += ans is None
        nums = [i.get("numero") for i in (ans or {}).get("itens", []) if isinstance(i, dict)]
        by_num = {i.get("numero"): i for i in (ans or {}).get("itens", []) if isinstance(i, dict)}
        # duplicated, missing or extra item numbers never make a scenario perfect
        perfect = ans is not None and sorted(nums, key=str) == sorted((e["numero"] for e in exp["itens"]), key=str)
        for e in exp["itens"]:
            r = score_item(e, by_num.get(e["numero"]))
            if r["kind"] == "error":
                tot["err_items"] += 1
                tot["detected"] += r["detected"]
                tot["hallucinated"] += r["hallucinated"]
                ok = r["detected"]
            else:
                n = r["correct"] + r["wrong"] + r["missing"]
                tot["calc_items"] += 1
                tot["fields"] += n
                for k in ("correct", "exact", "wrong", "missing", "rejected", "extra_is"):
                    tot[k] += r[k]
                ok = r["correct"] == n and not r["extra_is"] and not r["said_error"]
            perfect &= ok
            rows.append({"cenario": sid, "item": e["numero"], "ok": ok, "esperado": e, "resposta": by_num.get(e["numero"])})
        tot["perfect"] += perfect
        u = rec.get("usage") or {}
        cost += rec.get("total_cost_usd") or 0
        tokens_in += sum(u.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        tokens_out += u.get("output_tokens", 0)
        turns.append(rec.get("num_turns") or 0)
        secs.append((rec.get("duration_ms") or 0) / 1000)
    tot |= dict(cost=cost, tokens_in=tokens_in, tokens_out=tokens_out,
                turns=statistics.mean(turns), secs=statistics.median(secs),
                models=sorted({m for sid in gabarito["cenarios"]
                               for m in json.loads((HERE / "results" / mode / f"{sid}.json").read_text(encoding="utf-8")).get("models", [])}))
    return tot, rows


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"


def render(gabarito: dict, scores: dict) -> str:
    b, m = scores["baseline"], scores["mcp"]
    lines = [
        "# Evaluation results",
        "",
        "Generated by `python -m eval.score` from `eval/results/` and `eval/gabarito.json`. Do not edit by hand.",
        "",
        f"- Answer key: official calculator {gabarito['calculadora']['versaoApp']} (database {gabarito['calculadora']['versaoDb']}), "
        f"{b['scenarios']} fictitious scenarios, {b['calc_items'] + b['err_items']} items "
        f"({b['err_items']} of them rejected by the calculator).",
        f"- Model: {', '.join(sorted(set(b['models']) | set(m['models'])))} via `claude -p`, one run per scenario per mode.",
        "- A value is correct when it is within R$ 0.01 of the calculator's.",
        "",
        "| Metric | LLM alone | LLM + MCP |",
        "|---|---|---|",
        f"| Scenarios fully correct | {pct(b['perfect'], b['scenarios'])} | {pct(m['perfect'], m['scenarios'])} |",
        f"| Tax values correct (±R$ 0.01) | {pct(b['correct'], b['fields'])} | {pct(m['correct'], m['fields'])} |",
        f"| Tax values exact to the cent | {pct(b['exact'], b['fields'])} | {pct(m['exact'], m['fields'])} |",
        f"| Tax values wrong | {b['wrong']} | {m['wrong']} |",
        f"| Tax values missing | {b['missing']} | {m['missing']} |",
        f"| Valid items wrongly rejected | {b['rejected']} | {m['rejected']} |",
        f"| Invalid items flagged as errors | {pct(b['detected'], b['err_items'])} | {pct(m['detected'], m['err_items'])} |",
        f"| Invalid items given made-up values | {b['hallucinated']} | {m['hallucinated']} |",
        f"| Imposto Seletivo invented where there is none | {b['extra_is']} | {m['extra_is']} |",
        f"| Unparseable answers | {b['unparsed']} | {m['unparsed']} |",
        f"| Total cost (USD, as reported by the CLI) | {b['cost']:.2f} | {m['cost']:.2f} |",
        f"| Input tokens (incl. cache) / output tokens | {b['tokens_in']:,} / {b['tokens_out']:,} | {m['tokens_in']:,} / {m['tokens_out']:,} |",
        f"| Mean turns per scenario | {b['turns']:.1f} | {m['turns']:.1f} |",
        f"| Median wall time per scenario (s) | {b['secs']:.1f} | {m['secs']:.1f} |",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    gabarito = json.loads((HERE / "gabarito.json").read_text(encoding="utf-8"))
    scores, detail = {}, {}
    for mode in MODES:
        scores[mode], detail[mode] = score_mode(mode, gabarito)
    md = render(gabarito, scores)
    target = HERE / "RESULTS.md"
    if "--check" in sys.argv:
        if target.read_text(encoding="utf-8") != md:
            sys.exit("eval/RESULTS.md is stale: run `python -m eval.score`")
        print("eval/RESULTS.md matches the committed results")
        return
    target.write_text(md, encoding="utf-8")
    (HERE / "results" / "detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
