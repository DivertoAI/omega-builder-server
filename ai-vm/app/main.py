# ai-vm/app/main.py
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

from routes_build import router as build_router
from routes_scaffold import router as scaffold_router 
from routes_services import router as services_router 
from routes_melos import router as melos_router 

app = FastAPI(title="AI-VM")

app.include_router(build_router)
app.include_router(scaffold_router) 
app.include_router(services_router) 
app.include_router(melos_router) 

@app.get("/api/health")
def health():
    return {"ok": True, "service": "ai-vm", "status": "ready"}

class CompileRequest(BaseModel):
    project_dir: str
    target: str = "analyze"
    platform: Optional[str] = None

@app.post("/api/compile")
def compile_stub(req: CompileRequest):
    return {"accepted": True, "project_dir": req.project_dir, "target": req.target}