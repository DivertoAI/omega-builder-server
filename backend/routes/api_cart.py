from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/api", tags=["cart"])

class CartItem(BaseModel):
    product_id: str
    qty: int

class CartIn(BaseModel):
    items: List[CartItem]

@router.post("/cart")
def update_cart(cart: CartIn):
    total = 0.0
    for it in cart.items:
        price = 9.99  # stubbed price
        total += price * max(it.qty, 0)
    return {"ok": True, "total": round(total, 2), "items": [it.dict() for it in cart.items]}