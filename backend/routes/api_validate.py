from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["validate"])

@router.post("/orders/{order_id}/validate")
def validate(order_id: str):
    return {"ok": True, "order_id": order_id, "status": "validated"}