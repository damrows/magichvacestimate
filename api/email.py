"""
Magic HVAC Estimate — Email API
Vercel serverless function: /api/email
Sends estimate email via SendGrid (or fallback SMTP).
Rate limited: 5 emails per IP per hour.
"""

import json
import os
import time
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

_rate_store: dict = {}
RATE_LIMIT = 5
RATE_WINDOW = 3600


def _check_rate(ip: str) -> bool:
    now = time.time()
    window_start = now - RATE_WINDOW
    hits = _rate_store.get(ip, [])
    hits = [t for t in hits if t > window_start]
    if len(hits) >= RATE_LIMIT:
        return False
    hits.append(now)
    _rate_store[ip] = hits
    return True


def _build_html(body: dict) -> str:
    name = (body.get("name") or "there").split()[0]
    service = body.get("service", "hvac")
    sqft = body.get("sqft", "")
    tiers = body.get("tiers", [])
    appt_date = body.get("apptDate", "")
    appt_time = body.get("apptTime", "")
    duct_adj = body.get("ductAdj", 0)

    svc_label = {
        "furnace": "Furnace", "ac": "Air Conditioning", "heat_pump": "Heat Pump",
        "full_system": "Full HVAC System", "mini_split": "Mini Split", "air_handler": "Air Handler"
    }.get(service, "HVAC")

    tier_rows = ""
    for t in tiers:
        sale = t.get("sale", 0)
        list_p = t.get("list", 0)
        monthly = t.get("monthly", 0)
        tier_rows += f"""
        <tr>
          <td style="padding:12px;border-bottom:1px solid #f1f5f9;font-weight:600;color:#1e293b;">{t.get('label','')}</td>
          <td style="padding:12px;border-bottom:1px solid #f1f5f9;color:#64748b;font-size:13px;">{t.get('series','')}</td>
          <td style="padding:12px;border-bottom:1px solid #f1f5f9;text-align:right;"><span style="text-decoration:line-through;color:#94a3b8;">${list_p:,}</span></td>
          <td style="padding:12px;border-bottom:1px solid #f1f5f9;text-align:right;font-weight:700;color:#0057b7;font-size:16px;">${sale:,}</td>
          <td style="padding:12px;border-bottom:1px solid #f1f5f9;text-align:right;color:#64748b;">${monthly}/mo</td>
        </tr>"""

    appt_block = ""
    if appt_date:
        appt_block = f"<p style='background:#eff6ff;border-radius:10px;padding:14px 18px;margin:20px 0;'><strong>&#128197; Your appointment:</strong> {appt_date} &bull; {appt_time}</p>"

    duct_block = ""
    if duct_adj:
        duct_block = f"<p style='background:#fef3c7;border-radius:10px;padding:14px 18px;margin:10px 0;'>&#9888;&#65039; Ductwork + HERS Energy Test included: <strong>+${duct_adj:,}</strong></p>"

    sqft_label = f" ({sqft})" if sqft else ""

    return f"""<!DOCTYPE html><html><body style="margin:0;padding:20px;background:#f8fafc;font-family:-apple-system,sans-serif;">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08);">
  <div style="background:#0057b7;padding:32px 40px;">
    <h1 style="color:#fff;margin:0 0 4px;font-size:22px;">Your {svc_label} Estimate</h1>
    <p style="color:#bfdbfe;margin:0;font-size:14px;">Magic Plumbing, Heating &amp; Cooling &bull; CA Lic #698806</p>
  </div>
  <div style="padding:32px 40px;">
    <p style="font-size:16px;color:#1e293b;margin-top:0;">Hi {name},</p>
    <p style="color:#475569;">Here are the {svc_label} options we matched to your home{sqft_label}. All prices include installation, permits, and our full warranty.</p>
    {duct_block}
    <table style="width:100%;border-collapse:collapse;margin:20px 0;font-size:14px;">
      <thead><tr style="background:#f8fafc;">
        <th style="padding:10px 12px;text-align:left;font-size:11px;color:#64748b;letter-spacing:.05em;">TIER</th>
        <th style="padding:10px 12px;text-align:left;font-size:11px;color:#64748b;letter-spacing:.05em;">SYSTEM</th>
        <th style="padding:10px 12px;text-align:right;font-size:11px;color:#64748b;letter-spacing:.05em;">LIST</th>
        <th style="padding:10px 12px;text-align:right;font-size:11px;color:#64748b;letter-spacing:.05em;">YOUR PRICE</th>
        <th style="padding:10px 12px;text-align:right;font-size:11px;color:#64748b;letter-spacing:.05em;">MONTHLY</th>
      </tr></thead>
      <tbody>{tier_rows}</tbody>
    </table>
    {appt_block}
    <p style="color:#94a3b8;font-size:13px;">Pricing is a starting estimate based on your home size. Final pricing confirmed at your free in-home visit — no obligation to proceed.</p>
    <div style="text-align:center;margin:28px 0;">
      <a href="tel:+14152266533" style="display:inline-block;background:#0057b7;color:#fff;padding:14px 32px;border-radius:10px;font-weight:700;text-decoration:none;font-size:16px;">Call Us: (415) 226-6533</a>
    </div>
    <p style="color:#94a3b8;font-size:12px;text-align:center;margin:0;">Magic Plumbing, Heating &amp; Cooling &bull; San Francisco Bay Area &bull; <a href="https://magicplumbing.com" style="color:#0057b7;">magicplumbing.com</a></p>
  </div>
</div>
</body></html>"""


def _send_via_sendgrid(to_email: str, subject: str, html: str, name: str) -> bool:
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        return False

    payload = json.dumps({
        "personalizations": [{"to": [{"email": to_email, "name": name}]}],
        "from": {"email": "estimates@magicplumbing.com", "name": "Magic Plumbing"},
        "reply_to": {"email": "info@magicplumbing.com"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}]
    }).encode()

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        ip = self.headers.get("x-forwarded-for", self.client_address[0]).split(",")[0].strip()
        if not _check_rate(ip):
            self._json({"ok": False, "error": "rate limited"}, 429)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}

        to_email = (body.get("email") or "").strip()
        name = (body.get("name") or "there").split()[0]
        service = body.get("service", "hvac")

        if not to_email or "@" not in to_email:
            self._json({"ok": False, "error": "invalid email"}, 400)
            return

        svc_label = {
            "furnace": "Furnace", "ac": "Air Conditioning", "heat_pump": "Heat Pump",
            "full_system": "Full HVAC System", "mini_split": "Mini Split", "air_handler": "Air Handler"
        }.get(service, "HVAC")

        html = _build_html(body)
        subject = f"Your Magic Plumbing {svc_label} Estimate"
        sent = _send_via_sendgrid(to_email, subject, html, name)

        if sent:
            self._json({"ok": True})
        else:
            self._json({"ok": False, "error": "email service not configured"}, 503)

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
        pass
