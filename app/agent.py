"""Autonomous agent: PLAN -> EXECUTE -> REFLECT -> RENDER.

Engineering improvement implemented: REFLECTION / SELF-CHECK.
After drafting, a critic pass reviews the whole document against the original request and the
agent's own assumptions, flags weak/missing/contradictory sections, and the agent re-runs only
those tasks with the feedback. (One bounded round - no infinite loops.)
"""
import logging
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .docgen import render_docx
from .llm import complete_json
from .tools import TOOLS

log = logging.getLogger("agent")
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
MAX_TASKS, MAX_FIXES = 10, 3

PLANNER_SYS = """You are the planning module of an autonomous business-document agent.
Given a request, decide what document to produce and create your own execution plan.
Reply ONLY with JSON:
{"title": str, "doc_type": str, "summary": str,
 "assumptions": [str],
 "tasks": [{"id": int, "title": str, "tool": "lookup_reference|write_section|build_table|estimate_budget",
            "description": str, "args": {}}]}
Rules:
- 5 to 9 tasks, ordered. lookup_reference tasks (args.topic: company_profile|rate_card|team) go first.
- First content task is an Executive Summary; last is Next Steps.
- Use estimate_budget only if cost is relevant; build_table for timelines/risks/RACI/comparisons.
- If the request is ambiguous, missing details, or has conflicting requirements, do NOT ask questions:
  make reasonable, explicit assumptions and resolve each conflict in `assumptions`.
- Money: the reference rate_card is in INR. Use INR for every amount unless the request names another currency,
  and say so in `assumptions`. If no budget cap is given, assume a sensible cap in that currency.
- `summary` is a 1-2 sentence description of what you will deliver."""

CRITIC_SYS = """You are a strict reviewer of a business document drafted by an agent.
Compare the draft to the ORIGINAL REQUEST and the stated ASSUMPTIONS. Reply ONLY with JSON:
{"approved": bool, "score": int 1-10, "issues": [{"task_id": int, "problem": str}],
 "missing_assumptions": [str]}
Flag only real problems: requirements not addressed, contradictions, vague/generic content, empty sections.
Do not flag text as truncated or incomplete unless it genuinely ends mid-sentence. Max 3 issues. missing_assumptions = assumptions the document silently relies on but did not state."""


class PlanTask(BaseModel):
    id: int
    title: str
    tool: Literal["lookup_reference", "write_section", "build_table", "estimate_budget"]
    description: str = ""
    args: dict = Field(default_factory=dict)


class Plan(BaseModel):
    title: str
    doc_type: str
    summary: str = ""
    assumptions: list[str] = Field(default_factory=list)
    tasks: list[PlanTask] = Field(min_length=1, max_length=MAX_TASKS)


def _fallback_plan(request: str) -> Plan:
    """Deterministic safety net if the model cannot produce a valid plan."""
    t = [
        ("Executive Summary", "write_section"), ("Background and Objectives", "write_section"),
        ("Scope and Approach", "write_section"), ("Timeline and Milestones", "build_table"),
        ("Risks and Mitigations", "build_table"), ("Next Steps", "write_section"),
    ]
    return Plan(title="Business Document", doc_type="report", summary="Fallback structured report.",
                assumptions=["Planner output was invalid; a standard structure was used."],
                tasks=[PlanTask(id=i + 1, title=n, tool=tool, description=f"{n} for: {request[:200]}")
                       for i, (n, tool) in enumerate(t)])


def _normalize_plan(raw) -> tuple[dict, bool]:
    """Repair common model slips. Returns (plan_dict, had_garbage)."""
    if isinstance(raw, list):
        raw = next((x for x in raw if isinstance(x, dict)), {})
    garbage = False
    if isinstance(raw, dict) and isinstance(raw.get("tasks"), list):
        good = [t for t in raw["tasks"] if isinstance(t, dict) and "tool" in t]
        garbage = len(good) != len(raw["tasks"])
        raw["tasks"] = good
    return raw, garbage


def make_plan(request: str) -> Plan:
    note = ""
    for attempt in range(3):
        try:
            raw, garbage = _normalize_plan(complete_json("plan", PLANNER_SYS, f"REQUEST: {request}{note}"))
            if garbage and attempt < 2:      # a task was malformed: ask again rather than silently lose it
                note = "\nYour previous plan had a malformed task. Every task must be a complete JSON object."
                log.warning("plan had malformed tasks (attempt %d), retrying", attempt)
                continue
            return Plan.model_validate(raw)
        except ValidationError as e:
            log.warning("plan validation failed (attempt %d): %s", attempt, e.errors()[:1])
            note = "\nYour previous plan failed schema validation. Follow the schema exactly."
    return _fallback_plan(request)


def _outline(blocks: dict) -> str:
    return "; ".join(f"{b['heading']}: {(b.get('paragraphs') or [''])[0][:120]}" for b in blocks.values()) or "none"


def _run_task(task: dict, ctx: dict, blocks: dict) -> None:
    ctx["outline"] = _outline(blocks)
    try:
        block = TOOLS[task["tool"]](task, ctx)
        task["status"] = "done"
    except Exception as e:  # recovery: degrade this task to a plain section, keep the run alive
        if task["tool"] == "write_section":
            task["status"], task["error"] = "failed", str(e)
            return
        log.warning("task %s (%s) failed: %s - degrading to write_section", task["id"], task["tool"], e)
        try:
            block = TOOLS["write_section"](task, ctx)
            task["status"] = "recovered"
        except Exception as e2:
            task["status"], task["error"] = "failed", str(e2)
            return
    if block:
        blocks[task["id"]] = block


def reflect(request: str, plan: Plan, blocks: dict) -> dict:
    try:
        return _reflect(request, plan, blocks)
    except Exception as e:  # self-check is an enhancement: never fail the whole run because of it
        log.warning("reflection skipped: %s", e)
        return {"approved": None, "score": None, "issues": [], "missing_assumptions": []}


def _reflect(request: str, plan: Plan, blocks: dict) -> dict:
    draft = "\n".join(
        f"[task {tid}] {b['heading']}: " + " ".join(b.get("paragraphs", []) + b.get("bullets", []) +
                                                      [" | ".join(r) for r in b.get("rows", [])])[:2500]
        for tid, b in blocks.items()
    )
    return complete_json("reflect", CRITIC_SYS,
                         f"ORIGINAL REQUEST: {request}\nASSUMPTIONS: {plan.assumptions}\nDRAFT:\n{draft}")


def run_agent(request: str) -> dict:
    rid = uuid.uuid4().hex[:12]
    plan = make_plan(request)
    tasks = [t.model_dump() | {"status": "pending"} for t in plan.tasks]
    ctx = {"request": request, "title": plan.title, "doc_type": plan.doc_type,
           "assumptions": plan.assumptions, "reference": {}, "outline": ""}
    blocks: dict[int, dict] = {}

    # EXECUTE. Summary sections run last so they summarise real content; document order stays as planned.
    run_order = sorted(tasks, key=lambda t: "summary" in t["title"].lower())
    for task in run_order:
        _run_task(task, ctx, blocks)
    for task in run_order:                       # one retry pass for failures (e.g. rate-limit window has passed)
        if task["status"] == "failed":
            task["status"] = "pending"
            _run_task(task, ctx, blocks)

    review = reflect(request, plan, blocks)      # REFLECT
    snapshot = dict(blocks)                      # keep the pre-revision draft
    by_task: dict[int, list[str]] = {}           # one revision per task, all feedback merged
    for issue in review.get("issues", []):
        by_task.setdefault(issue.get("task_id"), []).append(issue.get("problem", ""))
    fixed = []
    for tid, problems in list(by_task.items())[:MAX_FIXES]:
        task = next((t for t in tasks if t["id"] == tid and t["tool"] != "lookup_reference"), None)
        if task:
            task["feedback"] = " | ".join(problems)
            _run_task(task, ctx, blocks)
            if task["status"] == "done":
                task["status"] = "revised"
            fixed.append(tid)
    for a in review.get("missing_assumptions", []):
        if a not in plan.assumptions:
            plan.assumptions.append(a)

    final = reflect(request, plan, blocks) if fixed else review   # RE-CHECK once
    reverted = False                             # accept-if-better: never ship a revision that scored worse
    s0, s1 = review.get("score"), final.get("score")
    if fixed and s0 is not None and s1 is not None and s1 < s0:
        blocks, reverted = snapshot, True
        for t in tasks:
            if t["id"] in fixed and t["status"] == "revised":
                t["status"] = "reverted"
    shown = review if reverted else final

    ordered = [blocks[t["id"]] for t in tasks if t["id"] in blocks]   # RENDER
    path = OUTPUT_DIR / f"{rid}.docx"
    render_docx(path, plan.title, plan.doc_type, plan.summary, plan.assumptions, ordered)

    outcome = (f"revisions scored {s1}/10 (worse), so the original draft was kept" if reverted
               else f"{s0}/10 -> {s1}/10 after revising {len(fixed)} section(s)")
    return {
        "request_id": rid,
        "summary": f"Generated '{plan.title}' ({plan.doc_type}) with {len(ordered)} sections; self-review {outcome}.",
        "plan": [{k: t.get(k) for k in ("id", "title", "tool", "status", "error")} for t in tasks],
        "assumptions": plan.assumptions,
        "reflection": {"approved": shown.get("approved"), "initial_score": s0,
                       "score": shown.get("score"), "initial_issues": review.get("issues", []),
                       "issues": shown.get("issues", []), "revised_task_ids": fixed,
                       "reverted": reverted, "revision_score": s1},
        "document": {"filename": path.name, "download_url": f"/download/{rid}"},
    }