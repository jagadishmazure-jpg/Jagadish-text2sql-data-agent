"""Build the synthetic retail SQLite database.

All data is fictional and deterministic (seeded RNG): rebuilding with the
same seed produces byte-identical row contents.
"""
from __future__ import annotations

import os
import random
import sqlite3

SEED = 42

REGIONS = ["North", "South", "East", "West"]
CATEGORIES = ["Electronics", "Home", "Apparel", "Sports", "Books"]
PRODUCT_NAMES = [
    "AeroLamp", "BoltBox", "CloudKettle", "DriftDesk", "EchoMug",
    "FluxFan", "GlowGlobe", "HexaHat", "IonIron", "JoltJuicer",
    "KiteKite", "LumenLamp", "MotoMat", "NovaNotebook", "OrbitOven",
    "PixelPillow", "QuarkQuilt", "RidgeRack", "SolarScale", "TurboTowel",
    "UltraUmbrella", "VoltVase", "WaveWallet", "XenoXbox", "YonderYoyo",
    "ZephyrZip", "AlphaApron", "BetaBottle", "CinderChair", "DeltaDrone",
]
FIRST = ["Ava", "Liam", "Maya", "Noah", "Zoe", "Eli", "Ivy", "Owen",
         "Ruby", "Finn", "Nora", "Kai", "Lena", "Milo", "Tara"]
LAST = ["Carter", "Nguyen", "Patel", "Garcia", "Kim", "Singh", "Lopez",
        "Rossi", "Haddad", "Novak", "Silva", "Bakker", "Costa", "Weber"]
TICKET_SUBJECTS = [
    "Late delivery", "Damaged item", "Wrong size", "Refund request",
    "Missing parts", "Billing question", "Warranty claim", "Return label",
]
TICKET_STATUS = ["open", "pending", "closed"]
ORDER_STATUS = ["shipped", "delivered", "cancelled"]


def _date(rng: random.Random, year: int = 2024) -> str:
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    return f"{year}-{month:02d}-{day:02d}"


def build_sample_db(path: str, seed: int = SEED) -> str:
    """Create (or recreate) the synthetic retail database at ``path``."""
    rng = random.Random(seed)
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    cur = conn.cursor()

    cur.execute("""CREATE TABLE customers (
        customer_id INTEGER PRIMARY KEY, name TEXT, email TEXT,
        phone TEXT, region TEXT, signup_date TEXT)""")
    cur.execute("""CREATE TABLE products (
        product_id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL)""")
    cur.execute("""CREATE TABLE orders (
        order_id INTEGER PRIMARY KEY, customer_id INTEGER, product_id INTEGER,
        quantity INTEGER, total REAL, order_date TEXT, region TEXT, status TEXT,
        FOREIGN KEY(customer_id) REFERENCES customers(customer_id),
        FOREIGN KEY(product_id) REFERENCES products(product_id))""")
    cur.execute("""CREATE TABLE support_tickets (
        ticket_id INTEGER PRIMARY KEY, customer_id INTEGER, subject TEXT,
        status TEXT, priority TEXT, created_date TEXT,
        FOREIGN KEY(customer_id) REFERENCES customers(customer_id))""")

    customers = []
    for i in range(1, 51):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        slug = name.lower().replace(" ", ".")
        customers.append((
            i, name, f"{slug}{i}@example.com",
            f"555-01{rng.randint(10, 99)}-{rng.randint(1000, 9999)}",
            rng.choice(REGIONS), _date(rng),
        ))
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", customers)

    products = []
    for i in range(1, 31):
        products.append((
            i, PRODUCT_NAMES[i - 1], rng.choice(CATEGORIES),
            round(rng.uniform(9.99, 499.99), 2),
        ))
    cur.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
    price_by_id = {p[0]: p[3] for p in products}

    orders = []
    for i in range(1, 201):
        pid = rng.randint(1, 30)
        qty = rng.randint(1, 5)
        total = round(price_by_id[pid] * qty, 2)
        orders.append((
            i, rng.randint(1, 50), pid, qty, total, _date(rng),
            rng.choice(REGIONS), rng.choice(ORDER_STATUS),
        ))
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", orders)

    tickets = []
    for i in range(1, 41):
        tickets.append((
            i, rng.randint(1, 50), rng.choice(TICKET_SUBJECTS),
            rng.choice(TICKET_STATUS),
            rng.choice(["low", "medium", "high"]), _date(rng),
        ))
    cur.executemany("INSERT INTO support_tickets VALUES (?,?,?,?,?,?)", tickets)

    conn.commit()
    conn.close()
    return path


def table_counts(path: str) -> dict:
    conn = sqlite3.connect(path)
    out = {}
    for t in ("customers", "products", "orders", "support_tickets"):
        out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    conn.close()
    return out
