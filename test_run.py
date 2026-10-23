import os, json
os.environ["LLM_CHAIN"] = "mock"
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
cases = {
 "standard": "Create a project proposal for building a customer support chatbot for a retail company, including timeline and budget.",
 "complex": "We need a launch plan ASAP but it must be cheap, also high quality; deadline unclear, team TBD. Figure it out.",
}
for name, req in cases.items():
    r = c.post("/agent", json={"request": req}); r.raise_for_status(); d = r.json()
    print(name, json.dumps({k: d[k] for k in ("summary", "plan", "assumptions", "reflection")}, indent=1)[:1500])
    f = c.get(d["document"]["download_url"]); open(f"/home/claude/{name}.docx", "wb").write(f.content); print(len(f.content), "bytes")
print(c.post("/agent", json={"request": "hi"}).status_code, c.get("/download/..%2f..%2fetc").status_code)
