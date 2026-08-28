import os
import re
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Checker API (Custom)",
    description="Direct API for checker_bridge replacement",
    version="1.0.0"
)

# ---------- CORS ----------
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Environment Config ----------
DEFAULT_RESPONSE = os.getenv("DEFAULT_RESPONSE", "Approved")
DEFAULT_STATUS = os.getenv("DEFAULT_STATUS", "ORDER_PAID")
DEFAULT_GATE = os.getenv("GATE", "Shopify Payments")

# Response types map
RESPONSE_TYPES = {
    "charged": "CHARGED",
    "live": "live",
    "approved": "Approved",
}

# Prices mapping (env me comma separated "VISA=2.95,MASTER=3,AMEX=5")
price_map_env = os.getenv("PRICE_MAP", "VISA=2.95,MASTERCARD=3,AMEX=5")
PRICE_MAP = {}
for pair in price_map_env.split(","):
    if "=" in pair:
        k, v = pair.split("=", 1)
        PRICE_MAP[k.strip().upper()] = v.strip()

# Allowed prices list (aapke diye gaye saare prices)
ALLOWED_PRICES = os.getenv(
    "ALLOWED_PRICES",
    "1,2,2.95,3,5,10,15,20,50"
).split(",")
ALLOWED_PRICES = [p.strip() for p in ALLOWED_PRICES if p.strip()]

def detect_card_type(card_number: str) -> str:
    """Return VISA, MASTERCARD, AMEX or UNKNOWN based on card number."""
    card = re.sub(r"\D", "", card_number)
    if not card:
        return "UNKNOWN"
    if card.startswith(("34", "37")):
        return "AMEX"
    if card.startswith("4"):
        return "VISA"
    if card.startswith(("5", "2")):
        return "MASTERCARD"
    return "UNKNOWN"

@app.get("/")
async def checker(request: Request):
    """
    Accepts query params:
      - card / bin / number : card number (full or BIN)
      - price (optional)     : if provided and in allowed list, override
      - response_type (optional): charged / live / approved
    Returns JSON: Response, Price, Gate, Status
    """
    params = request.query_params

    # Extract card number
    card = params.get("card") or params.get("number") or params.get("bin") or ""
    card_type = detect_card_type(card) if card else "UNKNOWN"

    # Determine price
    price = params.get("price", "").strip()
    if price and price in ALLOWED_PRICES:
        final_price = price
    else:
        final_price = PRICE_MAP.get(card_type, PRICE_MAP.get("DEFAULT", "2.95"))

    # Determine response text based on response_type param
    response_type = params.get("response_type", "").strip().lower()
    response = RESPONSE_TYPES.get(response_type, DEFAULT_RESPONSE)

    status = params.get("Status", DEFAULT_STATUS)
    gate = params.get("Gate", DEFAULT_GATE)

    return {
        "Response": response,
        "Price": final_price,
        "Gate": gate,
        "Status": status,
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "checker-api-custom"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
