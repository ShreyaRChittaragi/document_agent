"""Deterministic offline stand-in for the LLM (LLM_CHAIN=mock). Used for tests and offline demos only."""
import re


def mock_response(stage: str, user: str) -> dict:
    if stage == "plan":
        req = user.lower()
        complex_ = any(w in req for w in ("but", "unclear", "tbd", "asap", "cheap", "also", "however"))
        tasks = [
            {"id": 1, "title": "Load company context", "tool": "lookup_reference", "description": "Company profile", "args": {"topic": "company_profile"}},
            {"id": 2, "title": "Executive Summary", "tool": "write_section", "description": "Summarise purpose, scope and outcome", "args": {}},
            {"id": 3, "title": "Scope and Approach", "tool": "write_section", "description": "Describe what is in and out of scope", "args": {}},
            {"id": 4, "title": "Timeline and Milestones", "tool": "build_table", "description": "Phased milestones with owners", "args": {}},
        ]
        assumptions = ["Mock data is used for company details."]
        if "budget" in req or "cost" in req or complex_:
            tasks.insert(2, {"id": 9, "title": "Budget Estimate", "tool": "estimate_budget", "description": "Itemised costs", "args": {}})
            tasks.insert(1, {"id": 8, "title": "Load rate card", "tool": "lookup_reference", "description": "Day rates", "args": {"topic": "rate_card"}})
        tasks += [
            {"id": 5, "title": "Risks and Mitigations", "tool": "build_table", "description": "Top risks", "args": {}},
            {"id": 6, "title": "Next Steps", "tool": "write_section", "description": "Concrete actions", "args": {}},
        ]
        if complex_:
            assumptions += ["Conflicting speed vs cost goals resolved: fixed budget cap, phased delivery.",
                            "Unspecified deadline assumed to be 12 weeks."]
        return {"title": "Project Proposal", "doc_type": "proposal",
                "summary": "A structured proposal derived from the request.",
                "assumptions": assumptions, "tasks": tasks}
    if stage == "section":
        h = re.search(r"TASK: (.*?) -", user)
        h = h.group(1) if h else "Section"
        return {"heading": h, "paragraphs": [f"This section covers {h.lower()} in line with the request and stated assumptions."],
                "bullets": ["Key point one", "Key point two"]}
    if stage == "table":
        h = re.search(r"TASK: (.*?) -", user)
        return {"heading": h.group(1) if h else "Table", "intro": "Summary below.",
                "columns": ["Item", "Owner", "Timing"],
                "rows": [["Kickoff", "PM", "Week 1"], ["Build", "Engineering", "Weeks 2-8"], ["Launch", "PM", "Week 12"]]}
    if stage == "budget":
        return {"heading": "Budget Estimate", "intro": "Estimated using the standard rate card.", "currency": "INR",
                "items": [{"item": "Project Manager (days)", "qty": 20, "unit_cost": 18000},
                          {"item": "Senior Engineer (days)", "qty": 40, "unit_cost": 15000}],
                "contingency_pct": 10}
    if stage == "reflect":
        if "REVIEWER" in user:
            return {"approved": True, "score": 9, "issues": [], "missing_assumptions": []}
        return {"approved": False, "score": 7,
                "issues": [{"task_id": 3, "problem": "Scope is generic; name concrete deliverables."}],
                "missing_assumptions": ["Team availability assumed full-time."]}
    return {}
