# Xpress'd Custom Designs — standalone storefront + order dashboard

This is a **deployable starter application**, not a currently hosted payment-enabled store. It is entirely independent of GoDaddy/Shopify. Includes 99 products from the prior Xpress'd catalog, personalization/photo upload, cart, order storage, owner dashboard, order status notifications via SMTP, PayPal Orders API with server-side capture verification, and manual-payment reference workflows for Zelle, Chime, Venmo and Cash App.

## Run locally

1. Install Python 3.11+.
2. `python -m venv .venv` then activate it.
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in settings. Load them into your shell (`set -a; . ./.env; set +a` on Linux/macOS) or use your hosting provider's environment-variable settings. Do not commit `.env`.
5. Generate an admin password hash using the command in `.env.example`. Set `SECRET_KEY` to a secure random string. For local HTTP testing, set `HTTPS_ONLY=0`; production **must** use HTTPS and `HTTPS_ONLY=1`.
6. `python app.py` and visit http://127.0.0.1:5000. Select **Owner** to access dashboard.
7. Production: deploy behind HTTPS using `gunicorn app:app` on a host with persistent storage and a properly configured `DATABASE_PATH` and `UPLOAD_DIR`. SQLite is suitable for a low-volume single-instance setup; use managed database/object storage for multiple replicas and backups.

## Payments

- **PayPal**: create REST credentials at PayPal Developer; set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`. Start in `PAYPAL_MODE=sandbox`. The browser approves an order; the server captures it and verifies amount, currency and status before marking paid. Verify merchant eligibility and live account before switching to live.
- **Venmo**: the manual-reference option is implemented. Automated Venmo checkout requires a supported provider/merchant integration and is **not** implemented here.
- **Zelle, Chime and Cash App**: these are **manual-transfer options**, not embedded, verified payment gateways. Enter the business-approved payment destination in the corresponding `PAY_*` variable. A customer may submit a transfer reference, but only an owner can verify the transaction in the actual account and mark the order paid. Do not fulfill on customer-provided references alone.
- Payment credentials must be stored only on the server, never in frontend files.

## Notifications

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` and `OWNER_EMAIL` for new-order, reference-submitted, payment-confirmed and status-update emails. The dashboard also contains a recent event feed and manual refresh. **There are no push notifications or SMS** in this version. SMTP failure is logged, not guaranteed delivery.

## Important production checklist

- Confirm sales tax, shipping/pickup rules, product pricing and inventory. **The checkout currently assumes no shipping/tax; do not launch paid sales until legally applicable totals are configured.**
- Confirm policies: refunds, custom-order approval, turnaround time, privacy, and customer contact consent.
- Configure and test PayPal sandbox, then live; confirm Venmo and other transfer handles belong to the business.
- Use HTTPS, persistent backed-up database/uploads, secret management, rate limiting/WAF, monitoring, and proper access controls.
- Admin has one password-protected account; add multi-factor authentication and account recovery before serious production use.
- Photo uploads are size/type checked and private behind owner login; production should also scan files and configure retention/deletion.
- To make this a truly installable PWA, add proper icons and a service worker. Manifest is included, but offline functionality is not.
- Update catalog images (currently category icons), and configure prices for quote-only products.

## Files

`app.py` — Flask server and PayPal integration
`templates/index.html` — mobile storefront + dashboard
`catalog.json` — product catalog
`.env.example` — configuration template
`requirements.txt` — dependencies
