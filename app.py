import json
import os
import sqlite3
from datetime import date
from io import BytesIO

from flask import Flask, g, redirect, render_template, request, send_file, url_for
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as pdf_canvas

app = Flask(__name__)
DATABASE = os.environ.get("RAILWAY_DATABASE_PATH", "portal.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT    NOT NULL,
    spouse_name             TEXT    DEFAULT '',
    dob                     TEXT    DEFAULT '',
    spouse_dob              TEXT    DEFAULT '',
    ssn_last4               TEXT    DEFAULT '',
    spouse_ssn_last4        TEXT    DEFAULT '',
    monthly_salary          REAL    DEFAULT 0,
    expense_budget          REAL    DEFAULT 0,
    private_reserve_target  REAL    DEFAULT 0,
    created_at              TEXT    DEFAULT CURRENT_TIMESTAMP,
    last_report_date        TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     INTEGER NOT NULL REFERENCES clients(id),
    category      TEXT    NOT NULL CHECK(category IN ('retirement','non_retirement','trust','liability')),
    owner         TEXT    DEFAULT 'client1' CHECK(owner IN ('client1','client2','joint')),
    name          TEXT    NOT NULL,
    last_four     TEXT    DEFAULT '',
    interest_rate REAL    DEFAULT 0,
    last_balance  REAL    DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    generated_at TEXT    DEFAULT CURRENT_TIMESTAMP,
    quarter      TEXT    DEFAULT '',
    report_data  TEXT    DEFAULT '{}'
);
"""

def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db

@app.teardown_appcontext
def close_db(_):
    db = getattr(g, "_db", None)
    if db:
        db.close()

def init_db():
    with sqlite3.connect(DATABASE) as db:
        db.executescript(SCHEMA)

try:
    init_db()
except Exception as exc:
    print(f"Warning: DB init failed - {exc}")

@app.route("/")
def index():
    return redirect(url_for("clients"))

@app.route("/clients")
def clients():
    rows = get_db().execute("SELECT * FROM clients ORDER BY name").fetchall()
    return render_template("clients.html", clients=rows)

@app.route("/clients/new", methods=["GET", "POST"])
def new_client():
    if request.method == "POST":
        db = get_db()
        cur = db.execute(
            """INSERT INTO clients
               (name, spouse_name, dob, spouse_dob, ssn_last4, spouse_ssn_last4,
                monthly_salary, expense_budget, private_reserve_target)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            _client_vals(request.form),
        )
        _save_accounts(db, cur.lastrowid, request.form)
        db.commit()
        return redirect(url_for("client_detail", cid=cur.lastrowid))
    return render_template("client_form.html", client=None, accounts=[])

@app.route("/clients/<int:cid>")
def client_detail(cid):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not client:
        return "Client not found", 404
    accounts = db.execute(
        "SELECT * FROM accounts WHERE client_id=? ORDER BY category, owner, name", (cid,),
    ).fetchall()
    reports = db.execute(
        "SELECT id, quarter, generated_at FROM reports "
        "WHERE client_id=? ORDER BY generated_at DESC LIMIT 10", (cid,),
    ).fetchall()
    return render_template("client_detail.html", client=client, accounts=accounts, reports=reports)

@app.route("/clients/<int:cid>/edit", methods=["GET", "POST"])
def edit_client(cid):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not client:
        return "Client not found", 404
    if request.method == "POST":
        db.execute(
            """UPDATE clients SET name=?, spouse_name=?, dob=?, spouse_dob=?, ssn_last4=?,
               spouse_ssn_last4=?, monthly_salary=?, expense_budget=?,
               private_reserve_target=? WHERE id=?""",
            _client_vals(request.form) + (cid,),
        )
        db.execute("DELETE FROM accounts WHERE client_id=?", (cid,))
        _save_accounts(db, cid, request.form)
        db.commit()
        return redirect(url_for("client_detail", cid=cid))
    accounts = db.execute(
        "SELECT * FROM accounts WHERE client_id=? ORDER BY category, owner, name", (cid,),
    ).fetchall()
    return render_template("client_form.html", client=client, accounts=accounts)

@app.route("/clients/<int:cid>/report")
def report_form(cid):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not client:
        return "Client not found", 404
    accounts = db.execute(
        "SELECT * FROM accounts WHERE client_id=? ORDER BY category, owner, name", (cid,),
    ).fetchall()
    quarter = f"Q{((date.today().month - 1) // 3) + 1} {date.today().year}"
    return render_template("report_form.html", client=client, accounts=accounts, quarter=quarter)

@app.route("/clients/<int:cid>/generate", methods=["POST"])
def generate(cid):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not client:
        return "Client not found", 404
    accounts = db.execute(
        "SELECT * FROM accounts WHERE client_id=? ORDER BY category, owner, name", (cid,),
    ).fetchall()

    balances = {}
    for acc in accounts:
        raw = request.form.get(f"bal_{acc['id']}")
        val = float(raw) if raw else acc["last_balance"]
        balances[acc["id"]] = val
        db.execute("UPDATE accounts SET last_balance=? WHERE id=?", (val, acc["id"]))

    inflow  = float(request.form.get("inflow")     or client["monthly_salary"])
    outflow = float(request.form.get("outflow")    or client["expense_budget"])
    pr_bal  = float(request.form.get("pr_balance") or 0)
    excess  = max(inflow - outflow, 0)

    def _sum(cat, owner=None):
        return sum(
            balances[a["id"]] for a in accounts
            if a["category"] == cat and (owner is None or a["owner"] == owner)
        )

    ret_c1  = _sum("retirement", "client1")
    ret_c2  = _sum("retirement", "client2")
    non_ret = _sum("non_retirement")
    trust   = _sum("trust")
    liabs   = _sum("liability")
    total   = ret_c1 + ret_c2 + non_ret + trust

    quarter = request.form.get("quarter",
        f"Q{((date.today().month - 1) // 3) + 1} {date.today().year}")

    data = {
        "client_name": client["name"], "spouse_name": client["spouse_name"] or "",
        "dob": client["dob"] or "", "spouse_dob": client["spouse_dob"] or "",
        "ssn_last4": client["ssn_last4"] or "", "spouse_ssn_last4": client["spouse_ssn_last4"] or "",
        "inflow": inflow, "outflow": outflow, "excess": excess, "pr_balance": pr_bal,
        "pr_target": client["private_reserve_target"],
        "ret_c1": ret_c1, "ret_c2": ret_c2, "non_ret": non_ret,
        "trust": trust, "liabilities": liabs, "grand_total": total,
        "accounts": [
            {"id": a["id"], "category": a["category"], "owner": a["owner"],
             "name": a["name"], "last_four": a["last_four"],
             "interest_rate": a["interest_rate"], "balance": balances[a["id"]]}
            for a in accounts
        ],
    }

    db.execute("INSERT INTO reports (client_id, quarter, report_data) VALUES (?,?,?)",
               (cid, quarter, json.dumps(data)))
    db.execute("UPDATE clients SET last_report_date=? WHERE id=?",
               (date.today().isoformat(), cid))
    db.commit()

    buf = BytesIO()
    _generate_pdf(buf, data)
    buf.seek(0)
    safe_name = client["name"].replace(" ", "_")
    safe_q = quarter.replace(" ", "_")
    return send_file(buf, as_attachment=True,
                     download_name=f"{safe_name}_SACS_TCC_{safe_q}.pdf",
                     mimetype="application/pdf")

def _client_vals(form):
    return (
        form["name"], form.get("spouse_name", ""), form.get("dob", ""),
        form.get("spouse_dob", ""), form.get("ssn_last4", ""),
        form.get("spouse_ssn_last4", ""),
        float(form.get("monthly_salary") or 0),
        float(form.get("expense_budget") or 0),
        float(form.get("private_reserve_target") or 0),
    )

def _save_accounts(db, client_id, form):
    i = 0
    while f"acc_name_{i}" in form:
        name = form.get(f"acc_name_{i}", "").strip()
        if name:
            db.execute(
                """INSERT INTO accounts
                   (client_id, category, owner, name, last_four, interest_rate, last_balance)
                   VALUES (?,?,?,?,?,?,?)""",
                (client_id, form.get(f"acc_cat_{i}", "retirement"),
                 form.get(f"acc_owner_{i}", "client1"), name,
                 form.get(f"acc_last4_{i}", ""),
                 float(form.get(f"acc_rate_{i}") or 0),
                 float(form.get(f"acc_bal_{i}") or 0)),
            )
        i += 1

BRAND=colors.HexColor("#1B4F8A"); BRAND_LT=colors.HexColor("#D6E4F7")
GRN=colors.HexColor("#2E7D32");   GRN_LT=colors.HexColor("#E8F5E9")
RED=colors.HexColor("#C62828");   RED_LT=colors.HexColor("#FFEBEE")
BLU=colors.HexColor("#1565C0");   BLU_LT=colors.HexColor("#E3F2FD")
GRAY=colors.HexColor("#EEEEEE");  GRAY_BD=colors.HexColor("#BDBDBD")
WHITE=colors.white; BLACK=colors.black

def _fmt(n): return f"${n:,.0f}"

def _bubble(c, x, y, r, fill, stroke, label, amount, sub=None):
    c.setFillColor(fill); c.setStrokeColor(stroke); c.setLineWidth(2)
    c.circle(x, y, r, fill=1, stroke=1)
    c.setFillColor(stroke); c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(x, y+14, label)
    c.setFont("Helvetica-Bold", 13); c.drawCentredString(x, y-2, _fmt(amount))
    if sub:
        c.setFillColor(BLACK); c.setFont("Helvetica", 8)
        c.drawCentredString(x, y-18, sub)

def _arrow(c, x1, x2, y, col, label=None):
    c.setStrokeColor(col); c.setFillColor(col); c.setLineWidth(2)
    c.line(x1, y, x2-8, y)
    p = c.beginPath(); p.moveTo(x2,y); p.lineTo(x2-10,y+5); p.lineTo(x2-10,y-5); p.close()
    c.drawPath(p, fill=1, stroke=0)
    if label:
        c.setFillColor(col); c.setFont("Helvetica", 8)
        c.drawCentredString((x1+x2)/2, y+7, label)

def _gray_box(c, x, y, w, h, label, value):
    c.setFillColor(GRAY); c.setStrokeColor(GRAY_BD); c.setLineWidth(1)
    c.roundRect(x, y, w, h, 6, fill=1, stroke=1)
    c.setFillColor(BLACK); c.setFont("Helvetica", 8)
    c.drawCentredString(x+w/2, y+h-13, label)
    c.setFont("Helvetica-Bold", 13); c.drawCentredString(x+w/2, y+h/2-7, _fmt(value))

def _acc_box(c, x, y, name, last4, balance, rate=None):
    BW, BH = 148, 54
    c.setFillColor(BRAND_LT); c.setStrokeColor(BRAND); c.setLineWidth(1)
    c.roundRect(x, y, BW, BH, 7, fill=1, stroke=1)
    c.setFillColor(BRAND); c.setFont("Helvetica-Bold", 8)
    lbl = f"{name}  ...{last4}" if last4 else name
    c.drawCentredString(x+BW/2, y+BH-14, lbl)
    c.setFont("Helvetica-Bold", 12); c.drawCentredString(x+BW/2, y+BH/2-5, _fmt(balance))
    if rate:
        c.setFillColor(RED); c.setFont("Helvetica", 8)
        c.drawCentredString(x+BW/2, y+8, f"{rate:.2f}% APR")

def _generate_pdf(buffer, d):
    c = pdf_canvas.Canvas(buffer, pagesize=letter)
    W, H = letter
    today = date.today().strftime("%B %d, %Y")

    c.setFillColor(BRAND); c.rect(0, H-74, W, 74, fill=1, stroke=0)
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 18)
    c.drawString(36, H-38, "Simple Automated Cash Flow System (SACS)")
    c.setFont("Helvetica", 11)
    c.drawString(36, H-58, f"Client: {d['client_name']}")
    c.drawRightString(W-36, H-58, today)
    c.setFillColor(BLACK); c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(W/2, H-102, "Monthly Cash Flow")

    by = H-248; r = 72
    _bubble(c, 112, by, r, GRN_LT, GRN, "INFLOW",          d["inflow"],     "Monthly take-home")
    _arrow( c, 112+r, 306-r, by, RED, f"Outflow  {_fmt(d['outflow'])}")
    _bubble(c, 306, by, r, RED_LT, RED, "OUTFLOW",         d["outflow"],    "Monthly expenses")
    _arrow( c, 306+r, 500-r, by, BLU, f"Excess  {_fmt(d['excess'])}")
    _bubble(c, 500, by, r, BLU_LT, BLU, "PRIVATE RESERVE", d["pr_balance"], "Current balance")

    bx, bby, bw, bh = 212, by-r-72, 188, 58
    c.setFillColor(BLU_LT); c.setStrokeColor(BLU); c.setLineWidth(1.5)
    c.roundRect(bx, bby, bw, bh, 8, fill=1, stroke=1)
    c.setFillColor(BLU); c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(bx+bw/2, bby+bh-14, "Monthly Contribution to Reserve")
    c.setFont("Helvetica-Bold", 16); c.drawCentredString(bx+bw/2, bby+bh/2-5, _fmt(d["excess"]))
    c.setFillColor(BLACK); c.setFont("Helvetica", 8)
    c.drawCentredString(bx+bw/2, bby+8, f"Target: {_fmt(d['pr_target'])}")
    c.setFillColor(GRAY_BD); c.setFont("Helvetica-Oblique", 8)
    c.drawCentredString(W/2, bby-18, "Excess = Inflow - Outflow  |  All calculations automated")
    c.showPage()

    c.setFillColor(BRAND); c.rect(0, H-74, W, 74, fill=1, stroke=0)
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 18)
    c.drawString(36, H-38, "Total Client Chart (TCC) - Net Worth Overview")
    c.setFont("Helvetica", 11)
    c.drawString(36, H-58, f"Client: {d['client_name']}")
    c.drawRightString(W-36, H-58, today)

    accs = d["accounts"]
    def cat(category, owner=None):
        return [a for a in accs if a["category"]==category and (owner is None or a["owner"]==owner)]

    cy = H-95
    def _section_label(text):
        nonlocal cy
        c.setFillColor(BLACK); c.setFont("Helvetica-Bold", 12)
        c.drawString(36, cy, text); cy -= 18

    def _acc_row(label, items):
        nonlocal cy
        if not items: return
        if label:
            c.setFillColor(BRAND); c.setFont("Helvetica-Bold", 9)
            c.drawString(36, cy, label); cy -= 14
        x = 36
        for acc in items:
            if x+148 > W-20: x=36; cy-=64
            _acc_box(c, x, cy-54, acc["name"], acc["last_four"],
                     acc["balance"], acc["interest_rate"] or None)
            x += 158
        cy -= 64

    _section_label("Retirement Accounts")
    _acc_row(f"{d['client_name']} (Client 1)", cat("retirement","client1"))
    _acc_row(f"{d['spouse_name'] or 'Spouse'} (Client 2)", cat("retirement","client2"))
    _gray_box(c, 36, cy-44, 170, 44, "Client 1 Retirement Total", d["ret_c1"])
    if cat("retirement","client2"):
        _gray_box(c, 222, cy-44, 170, 44, "Client 2 Retirement Total", d["ret_c2"])
    cy -= 58

    nr = cat("non_retirement")
    if nr:
        _section_label("Non-Retirement Accounts"); _acc_row("", nr)
        _gray_box(c, 36, cy-44, 170, 44, "Non-Retirement Total", d["non_ret"]); cy -= 58

    tr = cat("trust")
    if tr: _section_label("Trust / Property"); _acc_row("", tr)

    lb = cat("liability")
    if lb: _section_label("Liabilities  (shown separately - not deducted from net worth)"); _acc_row("", lb)

    if cy < 90: c.showPage(); cy = H-60
    _gray_box(c, W/2-115, cy-55, 230, 55, "GRAND TOTAL NET WORTH", d["grand_total"])
    c.setFillColor(GRAY_BD); c.setFont("Helvetica", 8)
    c.drawCentredString(W/2, cy-64,
        "C1 Retirement + C2 Retirement + Non-Retirement + Trust  |  Liabilities excluded")
    c.save()

if __name__ == "__main__":
    init_db()
    app.run(debug=True)