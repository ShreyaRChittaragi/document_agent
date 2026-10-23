import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import OUTPUT_DIR, run_agent
from .llm import LLMError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
app = FastAPI(title="Autonomous Document Agent")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
INDEX = Path(__file__).parent / "static" / "index.html"


class AgentRequest(BaseModel):
    request: str = Field(..., min_length=10, max_length=4000, description="Natural-language request")


@app.get("/", include_in_schema=False)
def index():
    if not INDEX.exists():
        raise HTTPException(status_code=500, detail=f"frontend missing: {INDEX}")
    return FileResponse(INDEX)


@app.post("/agent")
def agent(body: AgentRequest):
    try:
        return run_agent(body.request.strip())
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"LLM unavailable: {e}")


@app.get("/download/{rid}")
def download(rid: str):
    if not re.fullmatch(r"[0-9a-f]{12}", rid):
        raise HTTPException(status_code=400, detail="invalid id")
    path = OUTPUT_DIR / f"{rid}.docx"
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type=DOCX, filename=path.name)


@app.get("/health")
def health():
    return {"status": "ok"}