"""
Magic HVAC Estimate — Pricing API
Vercel serverless function: /api/price
Returns customer-safe pricing only. Dealer costs never leave this file.
Rate limited: 30 requests per IP per hour.
"""

import json
import math
import os
import time
from http.server import BaseHTTPRequestHandler

# ---------- Simple in-memory rate limiter ----------
# Vercel functions are stateless between cold starts but this catches burst abuse
_rate_store: dict = {}
RATE_LIMIT = 30       # requests
RATE_WINDOW = 3600    # seconds (1 hour)


def _check_rate(ip: str) -> bool:
    """Return True if request is allowed, False if rate limited."""
    now = time.time()
    window_start = now - RATE_WINDOW
    hits = _rate_store.get(ip, [])
    hits = [t for t in hits if t > window_start]
    if len(hits) >= RATE_LIMIT:
        return False
    hits.append(now)
    _rate_store[ip] = hits
    return True


# ---------- Pricing data ----------
_PD = None


def _load_pd():
    global _PD
    if _PD is None:
        data_path = os.path.join(os.path.dirname(__file__), "..", "data", "pricing-data.json")
        with open(data_path) as f:
            _PD = json.load(f)
    return _PD


def _calc_mo(amt, apr_pct=6.99, months=144):
    if apr_pct == 0:
        return round(amt / months)
    r = (apr_pct / 100) / 12
    return round(amt * (r * (1 + r) ** months) / ((1 + r) ** months - 1))


def _get_pricing(service, sqft, rooms, current, is_dev=False):
    PD = _load_pd()

    # --- resolve data block ---
    if service == "mini_split":
        data = PD.get("miniSplit", {}).get(rooms)
    elif service == "air_handler":
        data = PD.get("airHandler", {}).get(sqft)
    elif service == "full_system":
        fs_map = {
            "Under 800": "1.5T/40K", "800\u20131,200": "2T/60K",
            "1,200\u20131,800": "2.5T/80K", "1,800\u20132,500": "3T/80K",
            "2,500\u20133,500": "3.5T/100K", "Over 3,500": "4T/100K",
        }
        data = PD.get("fullSystem", {}).get(fs_map.get(sqft, sqft))
    elif service == "ac":
        ac_map = {
            "Under 1,200": "1.5T", "1,200\u20132,000": "2T",
            "2,000\u20133,000": "3T", "Over 3,000": "4T",
        }
        data = PD.get("ac", {}).get(ac_map.get(sqft, sqft))
    elif service == "heat_pump":
        hp_map = {
            "Under 800": "1.5T", "800\u20131,200": "2T",
            "1,200\u20132,000": "2.5T", "2,000\u20133,000": "3T", "Over 3,000": "4T",
        }
        data = PD.get("heatPump", {}).get(hp_map.get(sqft, sqft))
    elif service == "furnace":
        btu_map = {
            "Under 800": "40K", "800\u20131,200": "60K",
            "1,200\u20131,800": "80K", "1,800\u20132,500": "80K",
            "2,500\u20133,500": "100K", "Over 3,500": "100K",
        }
        data = PD.get("furnace", {}).get(btu_map.get(sqft, sqft))
    else:
        data = None

    if not data:
        return None

    # --- ductwork add-on ---
    need_duct = current == "needs_ductwork"
    duct_map = {
        "Under 800": 6950, "800\u20131,200": 7950,
        "1,200\u20131,800": 9450, "1,800\u20132,500": 11950,
        "2,500\u20133,500": 15450, "Over 3,500": 20450,
        "Under 1,200": 7950, "1,200\u20132,000": 9450,
        "2,000\u20133,000": 11950, "Over 3,000": 15450,
        "2T": 7950, "3T": 11950, "4T": 15450,
        "1": 7950, "2": 9450, "3": 11950, "4": 15450, "5": 15450, "6": 20450,
    }
    duct_adj = duct_map.get(str(sqft), 9450) if need_duct else 0

    # --- build tier output ---
    tiers = {}
    for tier_key, d in data.items():
        if not d or not d.get("base"):
            continue
        base = d["base"] + duct_adj
        financed = math.ceil(base * 1.08 / 100) * 100
        list_p = math.ceil(financed * 1.10 / 100) * 100
        sale = round(list_p * 0.90 / 100) * 100
        monthly = _calc_mo(financed)
        monthly_999 = _calc_mo(financed, 9.99, 120)

        tier_data = {
            "sale": sale,
            "list": list_p,
            "financedBase": financed,
            "monthly": monthly,
            "monthly999": monthly_999,
            "efficiency": d.get("efficiency", ""),
            "desc": d.get("desc", ""),
            "series": d.get("series", d.get("outdoorSeries", "")),
            "stage": d.get("efficiency") or d.get("desc") or "",
            "laborHrs": d.get("laborHrs"),
            "sqft": d.get("sqft", ""),
            "outdoorModel": d.get("outdoorModel", ""),
            "indoorModel": d.get("indoorModel", ""),
            "model": d.get("model", ""),
        }

        # Dev mode: add full breakdown — gated by server secret
        dev_secret = os.environ.get("DEV_SECRET", "")
        if is_dev and dev_secret and is_dev == dev_secret:
            labor_hrs = d.get("laborHrs", 4)
            equip_sell = round(d.get("dealerCost", 0) * 1.4)
            mat_pct = 0.30 if service == "mini_split" else 0.25
            tier_data.update({
                "dealerCost": d.get("dealerCost", 0),
                "equipSell": equip_sell,
                "materialsCost": round(equip_sell * mat_pct),
                "laborCost": (labor_hrs or 0) * 650,
                "permitCost": 450,
                "basePrice": d["base"],
            })

        tiers[tier_key] = tier_data

    return {"tiers": tiers, "ductwork": duct_adj}


# ---------- Vercel handler ----------
class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        # Rate limiting
        ip = self.headers.get("x-forwarded-for", self.client_address[0]).split(",")[0].strip()
        if not _check_rate(ip):
            self._json({"error": "rate limited"}, 429)
            return

        # Parse body
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}

        service = body.get("service", "")
        sqft = body.get("sqft", "")
        rooms = str(body.get("rooms") or "1")
        current = body.get("current", "")
        is_dev = body.get("dev", False)

        result = _get_pricing(service, sqft, rooms, current, is_dev)
        if result is None:
            self._json({"error": "no data"}, 404)
            return

        self._json(result, 200)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # suppress default logging
