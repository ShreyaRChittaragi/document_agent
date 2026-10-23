# Autonomous Document Agent

POST /agent {"request": "..."} -> agent plans its own TODO list, executes with tools, self-reviews, returns a .docx.

## Run
    pip install -r requirements.txt
    export GROQ_API_KEY=...            # free key: console.groq.com   (or run Ollama locally)
    uvicorn app.main:app --reload
    curl -X POST localhost:8000/agent -H 'content-type: application/json' \
      -d '{"request":"Create a project proposal for a customer support chatbot with timeline and budget"}'
    # then open the returned document.download_url
Offline smoke test (no LLM): `python test_run.py`  (LLM_CHAIN=mock)

## Test inputs
1. Standard: "Create a project proposal for building a customer support chatbot for a retail company, including timeline and budget."
2. Complex: "We need a launch plan ASAP but it must be cheap, also high quality; deadline unclear, team TBD. Figure it out."

## Architecture
planner (LLM -> validated Plan) -> executor (tools) -> critic (reflection) -> revise flagged tasks -> python-docx render
- llm.py: Groq/Ollama via OpenAI-compatible API, retry w/ JSON-repair, provider fallback
- tools.py: lookup_reference (mock data), write_section, build_table, estimate_budget (LLM proposes, Python calculates)
- agent.py: plan/execute/reflect loop, fallback plan, per-task recovery
- docgen.py: styled Word output (title block, shaded tables, bullets, page numbers)

## Mandatory improvement: Reflection / self-check
Critic compares the full draft to the request + assumptions, flags up to 3 weak sections, agent re-runs only those with feedback
(one bounded round), and adds any silently-relied-on assumptions. Why: small free models draft plausible but generic/contradictory
text; a second pass catches it cheaply. Visible in response: reflection.score/issues/revised_task_ids.

## Debugging insight (talking point)
Free models wrap JSON in ``` fences or add prose -> json.loads crashed the run. Fix: fence-stripping + brace extraction,
then feed the parse error back to the model for a retry, then fall back to the next provider.

## Tradeoff (talking point)
Autonomous planning vs deterministic workflow: LLM-made plans adapt to ambiguous requests, but can be invalid.
Mitigation: Pydantic-validated plan (task count, tool enum) + deterministic fallback plan.
