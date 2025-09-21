from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["orders"])

MOCK_ORDERS = [
    {"id":"ord-1001","total":27.48,"status":"pending"},
    {"id":"ord-1002","total":14.75,"status":"ready"}
]

@router.get("/orders")
def list_orders():
    return {"items": MOCK_ORDERS}