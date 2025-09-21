from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["products"])

MOCK_PRODUCTS = [
    {"id": "p-aspirin", "name": "Aspirin 100mg", "price": 4.99, "image": "/assets/aspirin.png", "rx_required": False},
    {"id": "p-amoxicillin", "name": "Amoxicillin 500mg", "price": 12.50, "image": "/assets/amox.png", "rx_required": True},
    {"id": "p-vitc", "name": "Vitamin C 1000mg", "price": 8.75, "image": "/assets/vitc.png", "rx_required": False}
]

@router.get("/products")
def list_products():
    return {"items": MOCK_PRODUCTS}