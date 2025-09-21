from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/api", tags=["checkout"])

class Item(BaseModel):
    product_id: str
    qty: int

class CheckoutIn(BaseModel):
    items: List[Item]

@router.post("/checkout")
def checkout(body: CheckoutIn):
    order_id = "ord-" + hex(abs(hash(str(body))))[2:10]
    return {"ok": True, "order_id": order_id, "status": "created"}