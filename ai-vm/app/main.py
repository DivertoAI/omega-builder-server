from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

@app.get("/api/health")
def health():
    return {"ok": True, "service": "ai-vm", "status": "ready"}

# (Optional) simple compile stub so omega can ping this later
class CompileRequest(BaseModel):
    project_dir: str
    target: str = "analyze"   # analyze|test|web
    platform: Optional[str] = None

@app.post("/api/compile")
def compile_stub(req: CompileRequest):
    # In our wiring, real compile is done by the Redis worker via job_runner.sh
    return {"accepted": True, "project_dir": req.project_dir, "target": req.target}