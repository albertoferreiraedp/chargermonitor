#!/usr/bin/env python3
"""
REVE charger-status scraper.

Loads each location page on mapareve.es (a JavaScript app), waits for the
charging-points panel to render, and extracts, per charging point:
    operator, location id, charging-point id, raw status, classified status,
    "since" timestamp, power, connector type.

Each run appends one row PER CHARGING POINT to data/readings.csv, tagged with
the UTC time of the scrape. The dashboard reads that CSV.

Run locally:    python scraper.py
Debug one URL:  python scraper.py --debug   (dumps rendered HTML to debug/)
"""

import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).parent
DATA_DIR = ROOT / "data"
DEBUG_DIR = ROOT / "debug"
CSV_PATH = DATA_DIR / "readings.csv"
LOCATIONS_PATH = ROOT / "locations.json"

CSV_FIELDS = [
    "scraped_utc",      # when we took the reading (ISO 8601, UTC)
    "cluster",          # proximity group, e.g. "Location 1" (from locations.json)
    "location_id",      # UUID from the URL
    "location_label",   # friendly name you set in locations.json (optional)
    "operator",         # read from the page title, e.g. "EDP Charge"
    "panel_time",       # snapshot time shown on the page, e.g. "28/05/2026 22:00"
    "charge_point_id",  # e.g. ES*GFX*E01429*1
    "status_raw",       # exact text shown, e.g. "Available"
    "status",           # classified: available / occupied / out_of_service / reserved / unknown
    "since",            # "available/occupied since" timestamp, if shown
    "power_kw",         # e.g. 50
    "connector",        # e.g. CCS2
    "error",            # populated only if a location failed to scrape
]

# --- status classification -------------------------------------------------
# Maps the raw label (English or Spanish) to a normalized bucket. If you see a
# new wording in status_raw that lands in "unknown", add it here.
STATUS_RULES = [
    ("available",      ["available", "disponible", "libre"]),
    ("occupied",       ["occupied", "in use", "charging", "ocupado", "cargando", "en uso"]),
    ("out_of_service", ["out of service", "out of order", "unavailable", "fuera de servicio",
                        "no operativo", "averiado", "inoperativo", "fault"]),
    ("reserved",       ["reserved", "reservado"]),
    ("unknown",        ["unknown", "desconocido"]),
]


def classify(raw: str) -> str:
    low = (raw or "").strip().lower()
    for label, needles in STATUS_RULES:
        if any(n in low for n in needles):
            return label
    return "unknown"


# Regex for Spanish EVSE / charge-point IDs like ES*GFX*E01429*1
ID_RE = re.compile(r"ES\*[A-Z0-9]+\*[A-Z0-9]+\*\w+", re.IGNORECASE)
DATETIME_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}")
POWER_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*kW", re.IGNORECASE)
CONNECTOR_RE = re.compile(r"\b(CCS2|CCS|CHAdeMO|Type ?2|Mennekes|Schuko|GB/T|Tesla)\b", re.IGNORECASE)
# Words that look like a status when scanning lines
STATUS_WORDS_RE = re.compile(
    r"^(available|occupied|in use|charging|reserved|unknown|out of service|out of order|"
    r"unavailable|disponible|ocupado|cargando|reservado|desconocido|fuera de servicio|"
    r"no operativo|averiado)\b",
    re.IGNORECASE,
)


def parse_panel_text(text: str):
    """Parse the visible text of the charging-points panel into structured rows.

    The page renders, in order per charging point: the ID, then the status word,
    then an "...since:" line with a date. We scan line by line and group around
    each ID we find. This is resilient to most layout shifts because it keys off
    the ID pattern and the known status vocabulary rather than CSS classes.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    operator = None
    panel_time = None
    for ln in lines[:8]:
        if DATETIME_RE.search(ln) and panel_time is None:
            panel_time = DATETIME_RE.search(ln).group(0)
            break
        if operator is None and not ID_RE.search(ln):
            operator = ln  # first text line is the operator/network name
    if panel_time is None:
        m = DATETIME_RE.search(text)
        panel_time = m.group(0) if m else ""

    points = []
    i = 0
    while i < len(lines):
        if ID_RE.fullmatch(lines[i]) or ID_RE.match(lines[i]):
            cp_id = ID_RE.search(lines[i]).group(0)
            window = lines[i + 1 : i + 12]  # look ahead within this card
            status_raw = ""
            since = ""
            power = ""
            connector = ""
            for w in window:
                if not status_raw and STATUS_WORDS_RE.match(w):
                    status_raw = w
                if not since:
                    dm = DATETIME_RE.search(w)
                    if dm and ("since" in w.lower() or "desde" in w.lower() or status_raw):
                        since = dm.group(0)
                if not power:
                    pm = POWER_RE.search(w)
                    if pm:
                        power = pm.group(1).replace(",", ".")
                if not connector:
                    cm = CONNECTOR_RE.search(w)
                    if cm:
                        connector = cm.group(1)
                if ID_RE.search(w):  # next card started
                    break
            points.append({
                "charge_point_id": cp_id,
                "status_raw": status_raw,
                "status": classify(status_raw),
                "since": since,
                "power_kw": power,
                "connector": connector,
            })
            i += 1
        else:
            i += 1
    return operator or "", panel_time, points


def scrape_location(page, url, label, debug=False):
    page.goto(url, wait_until="networkidle", timeout=60_000)
    # Wait for the charging-points panel to appear. We wait on the visible text
    # "Charging points" / "Puntos de recarga" which the screenshot shows as a tab.
    try:
        page.wait_for_function(
            "() => /Charging points|Puntos de recarga/i.test(document.body.innerText)",
            timeout=30_000,
        )
    except Exception:
        time.sleep(5)  # last-resort settle

    panel_text = page.inner_text("body")

    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        loc_id = url.rstrip("/").split("/")[-1]
        (DEBUG_DIR / f"{loc_id}.html").write_text(page.content(), encoding="utf-8")
        (DEBUG_DIR / f"{loc_id}.txt").write_text(panel_text, encoding="utf-8")

    operator, panel_time, points = parse_panel_text(panel_text)
    return operator, panel_time, points


def load_locations():
    with open(LOCATIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def ensure_csv():
    DATA_DIR.mkdir(exist_ok=True)
    if not CSV_PATH.exists():
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def append_rows(rows):
    ensure_csv()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true",
                    help="dump rendered HTML/text per location to debug/")
    args = ap.parse_args()

    locations = load_locations()
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="en-GB",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        )
        page = ctx.new_page()
        for loc in locations:
            url = loc["url"]
            label = loc.get("label", "")
            cluster = loc.get("cluster", "")
            loc_id = url.rstrip("/").split("/")[-1]
            try:
                operator, panel_time, points = scrape_location(page, url, label, args.debug)
                # let the page-detected operator win, but keep a manual override if given
                operator = loc.get("operator") or operator
                if not points:
                    rows.append({"scraped_utc": now, "cluster": cluster, "location_id": loc_id,
                                 "location_label": label, "operator": operator,
                                 "panel_time": panel_time, "error": "no_points_parsed"})
                for pt in points:
                    rows.append({
                        "scraped_utc": now, "cluster": cluster, "location_id": loc_id,
                        "location_label": label, "operator": operator, "panel_time": panel_time,
                        **pt, "error": "",
                    })
                print(f"[ok] {label or loc_id}: {operator} -> {len(points)} points")
            except Exception as e:
                rows.append({"scraped_utc": now, "cluster": cluster, "location_id": loc_id,
                             "location_label": label, "operator": loc.get("operator", ""),
                             "error": f"{type(e).__name__}: {e}"})
                print(f"[err] {label or loc_id}: {e}", file=sys.stderr)
        browser.close()

    append_rows(rows)
    print(f"Wrote {len(rows)} rows to {CSV_PATH}")


if __name__ == "__main__":
    main()
