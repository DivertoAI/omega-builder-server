from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["rx"])

class RxIn(BaseModel):
    order_id: str
    image_b64: str  # keep it simple

@router.post("/rx")
def upload_rx(body: RxIn):
    return {"ok": True, "order_id": body.order_id, "status": "received"}