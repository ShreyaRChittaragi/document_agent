"""Tools the planner can assign to tasks. Each tool takes (task, context) and returns a block or data.

Design: the LLM supplies *content/inputs*; deterministic Python does the *work* that must be exact
(arithmetic, lookups). That is the tool-orchestration boundary.
"""
from .llm import complete_json

REFERENCE_DATA = {
    "company_profile": {
        "name": "Northwind Consulting",
        "industry": "Business & technology consulting",
        "headcount": 85,
        "locations": ["Bengaluru", "Pune"],
    },
    "rate_card": {
        "currency": "INR",
        "roles_per_day": {"Project Manager": 18000, "Senior Engineer": 15000, "Engineer": 10000,
                          "Designer": 9000, "QA Engineer": 7500, "Data Analyst": 9500},
    },
    "team": {"sponsor": "Head of Operations", "pm": "Project Manager", "tech_lead": "Senior Engineer"},
}

SECTION_SYS = (
    "You write one section of a professional business document. Reply ONLY with JSON: "
    '{"heading": str, "paragraphs": [str], "bullets": [str]}. Be specific, concrete and concise. '
    "Use the provided reference data and stated assumptions; never contradict them."
)
TABLE_SYS = (
    "You build one table for a professional business document. Reply ONLY with JSON: "
    '{"heading": str, "intro": str, "columns": [str], "rows": [[str]]}. Every row must have '
    "exactly len(columns) cells. 3-8 rows."
)
BUDGET_SYS = (
    "You propose budget line items for a business document. Reply ONLY with JSON: "
    '{"heading": str, "intro": str, "currency": str, "budget_cap": number|null, "items": [{"item": str, '
    '"qty": number, "unit_cost": number}], "contingency_pct": number}. Do NOT compute totals; code does that. '
    "Use the day rates from REFERENCE DATA rate_card when present. If ASSUMPTIONS or the request state a "
    "budget cap, set budget_cap to it and keep the total including contingency within it. Do not budget for "
    "resources the client is stated to provide. The currency MUST match the one stated in ASSUMPTIONS or the request; "
    "otherwise use the rate_card currency. Never mix currencies."
)


def _ctx_text(ctx: dict) -> str:
    return (
        f"REQUEST: {ctx['request']}\nDOCUMENT: {ctx['title']} ({ctx['doc_type']})\n"
        f"ASSUMPTIONS: {ctx['assumptions']}\nREFERENCE DATA: {ctx['reference']}\n"
        f"SECTIONS WRITTEN SO FAR: {ctx['outline']}\n"
    )


def _prompt(task: dict, ctx: dict) -> str:
    fb = f"\nREVIEWER FEEDBACK TO FIX: {task['feedback']}" if task.get("feedback") else ""
    return f"{_ctx_text(ctx)}\nTASK: {task['title']} - {task['description']}\nARGS: {task['args']}{fb}"


def lookup_reference(task, ctx):
    topic = task["args"].get("topic", "company_profile")
    data = REFERENCE_DATA.get(topic)
    if data is None:
        raise ValueError(f"unknown reference topic '{topic}'")
    ctx["reference"][topic] = data
    return None  # data tool: feeds context, produces no document block


def write_section(task, ctx):
    d = complete_json("section", SECTION_SYS, _prompt(task, ctx))
    return {"kind": "section", "heading": d.get("heading") or task["title"],
            "paragraphs": d.get("paragraphs", []), "bullets": d.get("bullets", [])}


def build_table(task, ctx):
    d = complete_json("table", TABLE_SYS, _prompt(task, ctx))
    cols = d["columns"]
    rows = [[str(c) for c in r][: len(cols)] + [""] * max(0, len(cols) - len(r)) for r in d["rows"]]
    return {"kind": "table", "heading": d.get("heading") or task["title"],
            "intro": d.get("intro", ""), "columns": cols, "rows": rows}


def calculate_budget(items: list[dict], contingency_pct: float, currency: str) -> tuple[list, list]:
    """Deterministic arithmetic. Returns (rows, footer rows)."""
    rows, subtotal = [], 0.0
    for it in items:
        qty, unit = float(it["qty"]), float(it["unit_cost"])
        line = qty * unit
        subtotal += line
        rows.append([it["item"], f"{qty:g}", f"{currency} {unit:,.0f}", f"{currency} {line:,.0f}"])
    cont = subtotal * contingency_pct / 100
    rows += [
        ["Subtotal", "", "", f"{currency} {subtotal:,.0f}"],
        [f"Contingency ({contingency_pct:g}%)", "", "", f"{currency} {cont:,.0f}"],
        ["TOTAL", "", "", f"{currency} {subtotal + cont:,.0f}"],
    ]
    return rows, [subtotal, cont, subtotal + cont]


def estimate_budget(task, ctx):
    prompt = _prompt(task, ctx)
    for attempt in range(3):
        d = complete_json("budget", BUDGET_SYS, prompt)
        cur = d.get("currency", "INR")
        rows, (_, _, total) = calculate_budget(d["items"], float(d.get("contingency_pct", 10)), cur)
        cap = d.get("budget_cap")
        if not cap or total <= float(cap) or attempt == 2:
            break
        prompt += (f"\nYour previous total {cur} {total:,.0f} exceeds the cap {cap}. "
                   "Reduce quantities or scope so the total including contingency is within the cap.")
    intro = d.get("intro", "")
    if cap and total > float(cap):
        intro += f" NOTE: estimate exceeds the stated cap of {cur} {float(cap):,.0f}."
    return {"kind": "table", "heading": d.get("heading") or task["title"], "intro": intro,
            "columns": ["Item", "Qty", "Unit cost", "Amount"], "rows": rows, "total_row": True}


TOOLS = {
    "lookup_reference": lookup_reference,
    "write_section": write_section,
    "build_table": build_table,
    "estimate_budget": estimate_budget,
}