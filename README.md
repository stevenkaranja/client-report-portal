# Client Report Portal

A web application that generates structured quarterly financial reports as downloadable PDFs, built for financial planning firms serving high net worth clients.

---

## The Problem

Financial advisors preparing quarterly client reports spend a full day manually pulling balances from multiple sources (bank, brokerage, Zillow), calculating totals in spreadsheets, and assembling reports in Canva. The process is slow, error-prone, and does not scale.

This portal cuts report preparation from a full day to under an hour by storing client profiles once, pre-populating each quarter's data entry form, automating all calculations, and generating print-ready PDFs in seconds.

---

## How It Works

```
Client setup (one-time)
  Store profile: names, DOB, SSN last 4, account structure, salary, expense budget

Quarterly report generation
  Pre-populated form with last known balances as reference
  Team enters updated balances from bank / brokerage / Zillow
  All math automated: excess, net worth, per-spouse retirement totals
  Two-page PDF downloaded instantly

SACS (Page 1): Inflow -> Outflow -> Private Reserve  (colour-coded bubble diagram)
TCC  (Page 2): Net worth breakdown by account type   (account boxes + summary totals)
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, Flask |
| Database | SQLite (client profiles, account structures, report history) |
| PDF generation | ReportLab |
| Frontend | HTML, CSS, vanilla JS (no framework) |
| Deployment | Nixpacks (Railway / Render compatible) |

---

## Key Features

- **Client profile database** - enter static info once; it pre-populates every quarterly form
- **Per-spouse account tracking** - Client 1 and Client 2 retirement totals calculated separately, matching the TCC visual layout
- **PRD-compliant calculations** - liabilities shown separately and never deducted from net worth; non-retirement total excludes trust accounts
- **Visual SACS PDF** - colour-coded bubble diagram (green Inflow, red Outflow, blue Private Reserve) with connecting arrows
- **Visual TCC PDF** - branded account boxes with last 4 digits, gray summary boxes per category, grand total footer
- **Report history** - each quarterly report stored; last known balances carried forward as reference
- **Real-time excess preview** - the report form updates the cash flow excess live as values change
- **Stateless PDF generation** - no files written to disk; PDF streamed directly as a download
- **Cloud-ready** - Procfile and nixpacks.toml for zero-config deployment to Railway or Render

---

## Project Structure

```
app.py                  # Flask routes, SQLite helpers, PDF generation
templates/
  base.html             # Shared layout and navigation
  clients.html          # Client list
  client_form.html      # Add / edit client with dynamic account rows
  client_detail.html    # Client profile and report history
  report_form.html      # Quarterly data entry with live excess preview
static/
  style.css             # Professional CSS (no framework)
requirements.txt        # Flask, ReportLab, gunicorn
Procfile                # Web process definition
nixpacks.toml           # Build config
```

---

## Setup

### Local development

```bash
git clone https://github.com/stevenkaranja/client-report-portal.git
cd client-report-portal
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Visit `http://localhost:5000`. The SQLite database (`portal.db`) is created automatically on first run.

### Deploy to Railway

1. Push this repo to GitHub
2. Connect in Railway - it detects `nixpacks.toml` and builds automatically
3. Set `RAILWAY_DATABASE_PATH=/data/portal.db` as an environment variable pointing to a Railway volume

---

## Calculations

All math is deterministic - no AI involved.

| Metric | Formula |
|--------|---------|
| Cash Flow Excess | max(Inflow - Outflow, 0) |
| Client 1 Retirement Total | Sum of all retirement accounts owned by Client 1 |
| Client 2 Retirement Total | Sum of all retirement accounts owned by Client 2 |
| Non-Retirement Total | Sum of non-retirement accounts only (trust excluded) |
| Grand Total Net Worth | C1 Retirement + C2 Retirement + Non-Retirement + Trust |
| Liabilities | Displayed separately - not deducted from net worth |

---

## Design Decisions

**SQLite over a hosted database** - with a small client base and quarterly report cadence, a file-based database is the right fit. Zero infrastructure overhead, trivially backupable, straightforward to migrate to PostgreSQL if the client base grows.

**No authentication in V1** - the portal is used by a small internal team. Authentication is a standard V2 addition and was not the core constraint to solve.

**ReportLab over WeasyPrint** - ReportLab's canvas API gives precise pixel-level control over the SACS bubble diagram and TCC account box layout. WeasyPrint works well for HTML-to-PDF but is harder to control for fixed-coordinate visual diagrams.

**Dynamic account rows** - each client has a different mix of IRA, Roth IRA, 401K, brokerage, and trust accounts. The form supports 1-6 accounts per category using a vanilla JS add/remove pattern rather than a frontend framework, keeping the stack simple.

---

## License

MIT
