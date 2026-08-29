import json
import re

# Apni sites ki raw list yahan paste karo (har line ek URL)
raw_urls = """
https://railway.com
https://www.google.com
# ... baaki URLs yahan paste karein ...
"""

urls = [line.strip() for line in raw_urls.splitlines() if line.strip() and line.strip().startswith("http")]

sites = []
seen = set()
for url in urls:
    url = url.rstrip("/")
    if url in seen:
        continue
    seen.add(url)
    # name = subdomain (myshopify.com se pehle wala)
    domain = url.replace("https://", "").replace("http://", "")
    name = domain.split(".")[0]
    sites.append({
        "name": name,
        "checkout_url": url + "/",
        "price": "AUTO",
        "gate": "Shopify Payments"
    })

with open("sites.json", "w", encoding="utf-8") as f:
    json.dump(sites, f, indent=2)

print(f"Created sites.json with {len(sites)} unique sites")
