from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import datetime, date, timedelta
import json
from collections import defaultdict
import os

APP_NAME = "La Mila Grosa"
DB_PATH = "mila.db"

app = Flask(__name__)
app.secret_key = "mila-grosa-secret"

# La contraseña para borrar todas las ventas no se versiona: vive en
# secretos_local.py (está en .gitignore) o en la variable MILA_PASSWORD_BORRAR_TODO.
try:
    from secretos_local import PASSWORD_BORRAR_TODO
except ImportError:
    PASSWORD_BORRAR_TODO = os.environ.get("MILA_PASSWORD_BORRAR_TODO", "")


# ---------------- DB ----------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, col, coltype):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        conn.commit()


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            total REAL NOT NULL,
            metodo_pago TEXT,
            monto_pagado REAL,
            vuelto REAL,
            nota TEXT,
            items_json TEXT
        )
    """)

    # migraciones suaves
    ensure_column(conn, "ventas", "metodo_pago", "TEXT")
    ensure_column(conn, "ventas", "monto_pagado", "REAL")
    ensure_column(conn, "ventas", "vuelto", "REAL")
    ensure_column(conn, "ventas", "nota", "TEXT")
    ensure_column(conn, "ventas", "items_json", "TEXT")

    conn.commit()
    conn.close()


@app.before_request
def before():
    init_db()


# ---------------- INICIO ----------------

@app.get("/")
def index():
    conn = get_db()
    hoy = date.today().isoformat()

    total_hoy = conn.execute("""
        SELECT COALESCE(SUM(total),0) AS t
        FROM ventas
        WHERE substr(fecha,1,10)=?
    """, (hoy,)).fetchone()["t"]

    rows = conn.execute("""
        SELECT items_json
        FROM ventas
        WHERE substr(fecha,1,10)=?
          AND items_json IS NOT NULL
          AND items_json <> ''
    """, (hoy,)).fetchall()

    sum_por_producto = defaultdict(float)

    for r in rows:
        try:
            items = json.loads(r["items_json"])
            for it in items:
                prod = it.get("producto")
                imp = float(it.get("importe") or 0)
                if prod:
                    sum_por_producto[prod] += imp
        except Exception:
            continue

    top = [{"nombre": k, "total": v} for k, v in sum_por_producto.items()]

    conn.close()
    return render_template(
        "index.html",
        app_name=APP_NAME,
        total_hoy=total_hoy,
        top=top,
        hoy=hoy
    )


# ---------------- NUEVA VENTA ----------------

@app.get("/venta/nueva")
def venta_nueva():
    return render_template("venta_nueva.html", app_name=APP_NAME)


@app.post("/venta/nueva")
def venta_nueva_post():
    items_json = request.form.get("items_json")
    metodo_pago = request.form.get("metodo_pago") or "EFECTIVO"
    monto_pagado = request.form.get("monto_pagado", type=float)
    nota = request.form.get("nota")

    try:
        items = json.loads(items_json)
    except Exception:
        flash("Error en los items de la venta.", "danger")
        return redirect(url_for("venta_nueva"))

    if not items or not isinstance(items, list):
        flash("No hay productos agregados.", "danger")
        return redirect(url_for("venta_nueva"))

    total = sum(float(it.get("importe", 0)) for it in items)

    if total <= 0:
        flash("El total es inválido.", "danger")
        return redirect(url_for("venta_nueva"))

    vuelto = None

    if metodo_pago == "EFECTIVO":
        if monto_pagado is None:
            flash("En efectivo tenés que indicar cuánto pagó.", "danger")
            return redirect(url_for("venta_nueva"))
        if monto_pagado < total:
            flash("El monto pagado es menor al total.", "danger")
            return redirect(url_for("venta_nueva"))
        vuelto = round(monto_pagado - total, 2)
    else:
        monto_pagado = total
        vuelto = None

    conn = get_db()
    conn.execute("""
        INSERT INTO ventas
        (fecha, total, metodo_pago, monto_pagado, vuelto, nota, items_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(timespec="seconds"),
        total,
        metodo_pago,
        monto_pagado,
        vuelto,
        nota,
        items_json
    ))
    conn.commit()
    conn.close()

    flash("Venta ingresada correctamente.", "success")
    return redirect(url_for("ventas"))


# ---------------- LISTADO DE VENTAS ----------------

@app.get("/ventas")
def ventas():
    conn = get_db()
    hoy = date.today().isoformat()
    ayer = (date.today() - timedelta(days=1)).isoformat()

    rows = conn.execute("""
        SELECT *
        FROM ventas
        ORDER BY id DESC
        LIMIT 200
    """).fetchall()

    total_hoy = conn.execute("""
        SELECT COALESCE(SUM(total),0) AS t
        FROM ventas
        WHERE substr(fecha,1,10)=?
    """, (hoy,)).fetchone()["t"]

    items_by_venta = {}
    for r in rows:
        try:
            ij = r["items_json"]
            if ij:
                items_by_venta[r["id"]] = json.loads(ij)
        except Exception:
            continue

    conn.close()
    return render_template(
        "ventas.html",
        app_name=APP_NAME,
        rows=rows,
        hoy=hoy,
        ayer=ayer,
        total_hoy=total_hoy,
        items_by_venta=items_by_venta
    )


@app.post("/ventas/<int:venta_id>/borrar")
def borrar_venta(venta_id):
    conn = get_db()
    conn.execute("DELETE FROM ventas WHERE id=?", (venta_id,))
    conn.commit()
    conn.close()
    flash("Venta borrada.", "warning")
    return redirect(url_for("ventas"))


# ---------------- RESET TOTAL ----------------

@app.post("/ventas/borrar_todo")
def borrar_todo():
    password = request.form.get("password")
    if not PASSWORD_BORRAR_TODO or password != PASSWORD_BORRAR_TODO:
        flash("Contraseña incorrecta.", "danger")
        return redirect(url_for("ventas"))

    conn = get_db()
    conn.execute("DELETE FROM ventas")
    conn.commit()
    conn.close()

    flash("Todas las ventas fueron borradas.", "success")
    return redirect(url_for("index"))


# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
