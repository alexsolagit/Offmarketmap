#!/usr/bin/env python3
"""
Shoreview Partners — Off-Market Map Auto-Updater
Fetches the mobilebasic Google Doc, diffs against listings.json,
and rebuilds both HTML files.

Run: python scripts/update_map.py
"""

import json
import re
import sys
import math
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
DOC_URL = "https://docs.google.com/document/u/0/d/15i2slO5zxncuhPl5wVcnpUQDAqLsl1EqG83oyFI8kmE/mobilebasic"
LISTINGS_JSON     = Path(__file__).parent.parent / "listings.json"
CONFIDENTIAL_HTML = Path(__file__).parent.parent / "offmarket-confidential.html"
FULL_HTML         = Path(__file__).parent.parent / "offmarket-full.html"

CITY_COLORS = {
    "Manhattan Beach":       "#185FA5",
    "Redondo Beach":         "#3B6D11",
    "Hermosa Beach":         "#534AB7",
    "Torrance":              "#C9A84C",
    "Hawthorne":             "#993C1D",
    "Rancho Palos Verdes":   "#885590",
    "Palos Verdes Estates":  "#885590",
    "Rolling Hills Estates": "#885590",
    "Lawndale":              "#BA7517",
    "Los Angeles":           "#6B7A8D",
    "Long Beach":            "#6B7A8D",
    "Pacific Palisades":     "#6B7A8D",
    "Lomita":                "#BA7517",
    "Gardena":               "#5D6D7E",
    "Carson":                "#7D6E5D",
}

CITY_COORDS = {
    "Manhattan Beach":       (33.8845, -118.4077),
    "Redondo Beach":         (33.8492, -118.3884),
    "Hermosa Beach":         (33.8622, -118.3995),
    "Torrance":              (33.8358, -118.3406),
    "Hawthorne":             (33.9164, -118.3525),
    "Rancho Palos Verdes":   (33.7446, -118.3870),
    "Palos Verdes Estates":  (33.7972, -118.3977),
    "Rolling Hills Estates": (33.7802, -118.3487),
    "Lawndale":              (33.8872, -118.3526),
    "Los Angeles":           (34.0522, -118.2437),
    "Long Beach":            (33.7701, -118.1937),
    "Pacific Palisades":     (34.0454, -118.5265),
    "Lomita":                (33.7923, -118.3154),
    "Gardena":               (33.8883, -118.3089),
    "Carson":                (33.8317, -118.2820),
}

# City name variations to canonical name
CITY_ALIASES = {
    r'\bMB\b':  "Manhattan Beach",
    r'\bRB\b':  "Redondo Beach",
    r'\bHB\b':  "Hermosa Beach",
    r'\bTO\b':  "Torrance",
    r'\bHW\b':  "Hawthorne",
    r'\bRPV\b': "Rancho Palos Verdes",
    r'\bPVE\b': "Palos Verdes Estates",
    r'\bRHE\b': "Rolling Hills Estates",
    r'\bLWN\b': "Lawndale",
    r'\bLB\b':  "Long Beach",
    r'\bPP\b':  "Pacific Palisades",
    r'manhattan beach': "Manhattan Beach",
    r'redondo beach':   "Redondo Beach",
    r'hermosa beach':   "Hermosa Beach",
    r'\btorrance\b':    "Torrance",
    r'\bhawthorne\b':   "Hawthorne",
    r'rancho palos verdes': "Rancho Palos Verdes",
    r'palos verdes estates': "Palos Verdes Estates",
    r'rolling hills estates': "Rolling Hills Estates",
    r'\blawndale\b':    "Lawndale",
    r'long beach':      "Long Beach",
    r'pacific palisades': "Pacific Palisades",
    r'\blomita\b':      "Lomita",
    r'\bgardena\b':     "Gardena",
    r'\bcarson\b':      "Carson",
    r'los angeles':     "Los Angeles",
    r'\bbird streets\b':"Los Angeles",
    r'hollywood hills': "Los Angeles",
}

# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch_doc():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    req = urllib.request.Request(DOC_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

# ── HTML UTILS ────────────────────────────────────────────────────────────────
def strip_tags(html):
    return re.sub(r'<[^>]+>', ' ', html)

def decode_entities(text):
    text = re.sub(r'&amp;',  '&',  text)
    text = re.sub(r'&nbsp;', ' ',  text)
    text = re.sub(r'&lt;',   '<',  text)
    text = re.sub(r'&gt;',   '>',  text)
    text = re.sub(r'&#\d+;', '',   text)
    text = re.sub(r'&[a-z]+;', '', text)
    return text

def clean(text):
    return decode_entities(re.sub(r'\s+', ' ', strip_tags(text))).strip()

# ── PARSE ─────────────────────────────────────────────────────────────────────
def detect_city(text):
    lower = text.lower()
    for pattern, city in CITY_ALIASES.items():
        if re.search(pattern, lower if pattern.startswith(r'\b') or ' ' in pattern else text):
            return city
    return None

def parse_price(text, ltype):
    """
    Returns (price_display, praw) where:
    - sale:  praw in $K  (1700000 → 1700, 1.7M → 1700)
    - lease: praw in $K/mo (6800 → 6.8, $5,500/mo → 5.5)
    """
    # Normalize
    t = text.replace(',', '').replace('$', '').upper()

    # Patterns ordered by specificity
    patterns = [
        (r'([\d.]+)\s*MIL(?:LION)?',   'M'),
        (r'([\d.]+)\s*M\b',            'M'),
        (r'([\d.]+)\s*K\b',            'K'),
        (r'([\d.]+)\s*/\s*MO',         'mo'),
        (r'([\d.]+)',                   'raw'),
    ]
    for pat, unit in patterns:
        m = re.search(pat, t)
        if not m:
            continue
        raw_num = m.group(1).replace(',', '').replace('.', '', m.group(1).count('.')-1) if m.group(1).count('.') > 1 else m.group(1).replace(',', '')
        try:
            num = float(raw_num)
        except (ValueError, TypeError):
            continue
        if unit == 'M':
            praw = num * 1000   # in K
        elif unit == 'K':
            praw = num
        elif unit == 'mo':
            # lease: convert bare $/mo to K
            praw = num / 1000 if num > 500 else num
        else:
            # bare number — scale by context
            if ltype == 'lease':
                praw = num / 1000 if num > 500 else num
            else:
                praw = num / 1000 if num > 100000 else num

        praw = round(praw, 3)

        # Format display
        if ltype == 'lease':
            monthly = int(round(praw * 1000))
            display = f"${monthly:,}/mo"
        else:
            if praw >= 1000:
                m_val = praw / 1000
                display = f"${m_val:.2f}M".rstrip('0').rstrip('.') + 'M'
                display = display.replace('..', '.')
            else:
                display = f"${praw:,.0f}K" if praw == int(praw) else f"${praw:,.1f}K"
        return display, praw

    return "TBD", None

def parse_beds_baths_sqft(text):
    bed_m  = re.search(r'(\d+)\s*(?:bed(?:room)?s?|bd|br)\b', text, re.I)
    bath_m = re.search(r'(\d+(?:[./]\d+)?)\s*(?:bath(?:room)?s?|ba)\b', text, re.I)
    sqft_m = re.search(r'([\d,]+)\s*(?:sq\.?\s*ft\.?|sqft|sf)\b', text, re.I)

    beds = int(bed_m.group(1)) if bed_m else 2
    baths_raw = bath_m.group(1) if bath_m else None
    if baths_raw:
        baths_raw = baths_raw.replace('/', '.')
        try:
            baths = float(baths_raw)
        except ValueError:
            baths = None
    else:
        baths = None

    sqft_raw = int(sqft_m.group(1).replace(',', '')) if sqft_m else None
    sqft_disp = f"{sqft_raw:,}" if sqft_raw else None
    return beds, baths, sqft_raw, sqft_disp

def parse_blocks(html):
    """
    Split doc HTML into dated blocks and parse each one.
    The mobilebasic format has <p> tags with date stamps like "4.27.26"
    followed by paragraphs of listing data.
    """
    # Split on date-stamp pattern at start of paragraph
    date_re = re.compile(r'(?:^|\n)(\d{1,2}\.\d{1,2}\.\d{2})\s*\n', re.M)

    # Work with raw HTML to preserve structure, then clean per-block
    # First split into paragraphs
    paras = re.split(r'</?p[^>]*>', html)

    # Recombine into dated blocks
    blocks = []
    current_date = None
    current_lines = []

    for para in paras:
        text = clean(para)
        if not text:
            continue
        # Check if this is a date stamp
        date_m = re.match(r'^(\d{1,2}\.\d{1,2}\.\d{2})$', text.strip())
        if date_m:
            if current_date and current_lines:
                blocks.append((current_date, '\n'.join(current_lines)))
            current_date = date_m.group(1)
            current_lines = []
        elif current_date:
            current_lines.append(text)

    if current_date and current_lines:
        blocks.append((current_date, '\n'.join(current_lines)))

    results = []
    for date_str, block in blocks:
        # Determine type
        ltype = "lease" if re.search(
            r'\bfor\s+lease\b|\blease\b|\b/mo\b|\bper month\b|\bfurnished\b.*\b(month|mo)\b',
            block, re.I
        ) else "sale"

        # Also check date-adjacent header which often has "Lease Have"
        if re.search(r'lease\s+have', block[:100], re.I):
            ltype = "lease"

        # City
        city = detect_city(block)
        if not city:
            continue  # skip if we can't place it

        # State + zip
        zip_m = re.search(r'CA\s*(\d{5})', block, re.I)
        state = f"CA {zip_m.group(1)}" if zip_m else "CA"

        # Address: prefer linked address text (was in <a> tags, now cleaned)
        # Look for street number + street name pattern
        addr_m = re.search(
            r'(\d{2,6}\s+[A-Za-z0-9 .]+?(?:St|Ave|Blvd|Dr|Rd|Pl|Ct|Ln|Way|Terrace|Place|Street|Avenue|Drive|Road|Court|Lane|Hwy)\.?(?:\s+(?:#|Unit|Apt)[\s\w]+)?)',
            block, re.I
        )
        if addr_m:
            address = addr_m.group(1).strip().rstrip('.,')
        else:
            address = f"{city} (area)"

        # Price — find all price mentions, take the most prominent
        # Look for explicit price lines
        price_line = ""
        for line in block.split('\n'):
            if re.search(r'\$\s*[\d,]+|\b[\d,]+\s*(?:mil|million|/mo|per month)\b', line, re.I):
                # Prefer lines with "asking", "price", or standalone price
                if re.search(r'asking|price|\$\s*[\d.]+\s*(?:m|mil|million)?\s*$', line.strip(), re.I):
                    price_line = line
                    break
                if not price_line:
                    price_line = line

        if not price_line:
            price_line = block  # fallback: search whole block

        price_display, praw = parse_price(price_line, ltype)

        # Beds / baths / sqft
        beds, baths, sqft_raw, sqft_disp = parse_beds_baths_sqft(block)

        # Off-market flag
        lower = block.lower()
        if re.search(r'staying off.{0,5}market', lower):
            offmkt = True
        elif re.search(r'will not.{0,30}mls|not going.{0,20}mls', lower):
            offmkt = False
        elif re.search(r'coming\s+(?:soon|to\s+market|feb|jan|mar|apr|may|june|july)', lower):
            offmkt = False
        else:
            offmkt = True

        # Agent name, phone, broker
        phone_m = re.search(r'(\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4})', block)
        phone = phone_m.group(1).strip() if phone_m else ""

        # Agent name: line just before phone number
        agent = ""
        broker = ""
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        for i, line in enumerate(lines):
            if phone and phone.replace(' ','').replace('-','').replace('.','').replace('(','').replace(')','') in \
               line.replace(' ','').replace('-','').replace('.','').replace('(','').replace(')',''):
                if i > 0:
                    agent = lines[i-1]
        for line in lines:
            if re.search(r'\b(realty|sotheby|compass|keller|exp realty|bayside|strand hill|kaminsky|'
                         r'vista|estate prop|lyon|coldwell|agency|radius|real broker|maxnet|joseph group|'
                         r'caskey|altamura|thompson|hoffman|jacobellis|domo|properties|partners|group|brokerage)\b',
                         line, re.I):
                if not re.search(r'@|\d{3}[\s.\-]\d{4}', line):
                    broker = line.strip()
                    break

        # Notes: first substantial descriptive line
        notes_parts = []
        for line in lines:
            if (len(line) > 50
                and not re.search(r'@|dre\s*#|\d{3}[\s.\-]\d{4}|\.com', line, re.I)
                and not re.search(r'^(for sale|for lease|from |staying off)', line, re.I)
                and not re.match(r'^\d{1,2}\.\d{1,2}\.\d{2}$', line)):
                notes_parts.append(line)
            if len(notes_parts) >= 2:
                break
        notes = ' '.join(notes_parts)[:220] if notes_parts else f"{city} off-market opportunity."

        lat, lng = CITY_COORDS.get(city, (33.870, -118.370))
        doc_key = f"{address.lower().strip()}|{city.lower()}|{ltype}"

        results.append({
            "doc_key":  doc_key,
            "date_str": date_str,
            "type":     ltype,
            "city":     city,
            "state":    state,
            "address":  address,
            "lat":      lat,
            "lng":      lng,
            "price":    price_display,
            "praw":     praw,
            "beds":     beds,
            "baths":    baths,
            "sqftRaw":  sqft_raw,
            "sqft":     sqft_disp,
            "offmkt":   offmkt,
            "agent":    (agent or "")[:60],
            "phone":    phone[:20],
            "broker":   (broker or "")[:60],
            "notes":    notes,
        })

    return results

# ── BADGE ROTATION ─────────────────────────────────────────────────────────────
def rotate_badges(listings):
    for l in listings:
        ns = l.get("newStatus", False)
        if ns == "new":
            l["newStatus"] = "recent"
        elif ns == "recent":
            l["newStatus"] = False
        if l.get("priceWas"):
            l["_pwCycles"] = l.get("_pwCycles", 0) + 1
            if l["_pwCycles"] >= 2:
                l["priceWas"] = None
                l["_pwCycles"] = 0
    return listings

# ── DIFF & MERGE ──────────────────────────────────────────────────────────────
def diff_and_merge(current, doc_listings):
    changelog = []
    current_by_key = {l["doc_key"]: l for l in current if "doc_key" in l}
    doc_keys = {d["doc_key"] for d in doc_listings}
    next_id = max((l["id"] for l in current), default=0) + 1

    # Remove listings gone from doc — only if parser confidence is high enough
    # If doc found < 80% of current listings, parser likely missed entries; skip removals
    confidence = len(doc_listings) / max(len(current), 1)
    if confidence >= 0.90:
        removed = [l for l in current if l.get("doc_key") and l["doc_key"] not in doc_keys]
        for r in removed:
            changelog.append(f"  REMOVED id={r['id']} {r.get('address','')} ({r['city']})")
        kept = [l for l in current if not l.get("doc_key") or l["doc_key"] in doc_keys]
    else:
        removed = []
        kept = current[:]
        changelog.append(f"  SKIPPED removals — parser confidence {confidence:.0%} < 90%, keeping all {len(current)} listings")

    new_count = 0
    for d in doc_listings:
        key = d["doc_key"]
        if key in current_by_key:
            existing = next(l for l in kept if l.get("doc_key") == key)
            if d["praw"] is not None and existing.get("praw") is not None:
                if abs(d["praw"] - existing["praw"]) > 0.5:
                    changelog.append(f"  PRICE CHANGE id={existing['id']} "
                                     f"{existing['price']} → {d['price']}")
                    existing["priceWas"] = existing["price"]
                    existing["_pwCycles"] = 0
                    existing["price"]     = d["price"]
                    existing["praw"]      = d["praw"]
        else:
            new_entry = {
                "id":        next_id,
                "doc_key":   key,
                "type":      d["type"],
                "city":      d["city"],
                "state":     d["state"],
                "address":   d["address"],
                "lat":       d["lat"],
                "lng":       d["lng"],
                "price":     d["price"],
                "praw":      d["praw"],
                "beds":      d["beds"],
                "baths":     d["baths"],
                "sqftRaw":   d["sqftRaw"],
                "sqft":      d["sqft"],
                "offmkt":    d["offmkt"],
                "newStatus": "new",
                "priceWas":  None,
                "notes":     d["notes"],
                "agent":     d["agent"],
                "phone":     d["phone"],
                "broker":    d["broker"],
            }
            kept.append(new_entry)
            changelog.append(f"  NEW id={next_id} {d['address']} ({d['city']}) {d['price']}")
            next_id += 1
            new_count += 1

    return kept, changelog, new_count

# ── COLOR ─────────────────────────────────────────────────────────────────────
def get_color(l):
    if l.get("priceWas"):
        return "#E65100"
    ns = l.get("newStatus", False)
    if ns == "new":
        return "#2E7D32"
    if ns == "recent":
        return "#B8860B"
    return CITY_COLORS.get(l.get("city", ""), "#6B7A8D")

# ── JITTER ────────────────────────────────────────────────────────────────────
def _sr(seed):
    x = math.sin(seed + 1) * 10000
    return x - math.floor(x)

def jittered(l):
    lid = l["id"]
    return (
        round(l["lat"] + (_sr(lid * 7.3)  - 0.5) * 0.005, 7),
        round(l["lng"] + (_sr(lid * 13.7) - 0.5) * 0.005, 7),
    )

# ── JS ARRAY ──────────────────────────────────────────────────────────────────
def esc(s):
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "")

def to_js(listings, confidential=False):
    rows = []
    for l in listings:
        color   = get_color(l)
        praw_js = "null" if l.get("praw") is None else str(l["praw"])
        pw_js   = f'"{esc(l["priceWas"])}"' if l.get("priceWas") else "null"
        ns_js   = f'"{l["newStatus"]}"' if l.get("newStatus") else "false"
        sq_js   = "null" if not l.get("sqft")    else f'"{esc(l["sqft"])}"'
        sqr_js  = "null" if l.get("sqftRaw") is None else str(l["sqftRaw"])
        ba_js   = "null" if l.get("baths")  is None else str(l["baths"])
        lat, lng = jittered(l) if confidential else (l["lat"], l["lng"])
        addr = f'{l["city"]} (area)' if confidential else esc(l.get("address", ""))

        base = (
            f'{{id:{l["id"]},type:"{l["type"]}",address:"{addr}",'
            f'city:"{esc(l["city"])}",state:"{esc(l["state"])}",'
            f'lat:{lat},lng:{lng},'
            f'price:"{esc(l["price"])}",praw:{praw_js},'
            f'beds:{l.get("beds",2)},baths:{ba_js},'
            f'sqftRaw:{sqr_js},sqft:{sq_js},'
            f'offmkt:{"true" if l.get("offmkt") else "false"},'
            f'newStatus:{ns_js},priceWas:{pw_js},color:"{color}",'
            f'notes:"{esc(l.get("notes",""))}"'
        )
        if not confidential:
            base += (
                f',agent:"{esc(l.get("agent",""))}"'
                f',phone:"{esc(l.get("phone",""))}"'
                f',broker:"{esc(l.get("broker",""))}"'
            )
        base += '}'
        rows.append('  ' + base)
    return '[\n' + ',\n'.join(rows) + '\n]'

# ── INJECT INTO HTML ──────────────────────────────────────────────────────────
BADGE_RE = re.compile(r'(<div[^>]*id="count-badge"[^>]*>)\d+ Listings(</div>)')
FCOUNT_RE = re.compile(r'(<span id="fcount">)\d+ listings(</span>)')

def inject(html, listings, confidential=False):
    js = to_js(listings, confidential)

    # Bracket-match the array — works regardless of spacing or size
    for marker in ['const listings=', 'const listings =']:
        idx = html.find(marker)
        if idx >= 0:
            bracket = html.index('[', idx)
            depth = 0
            i = bracket
            while i < len(html):
                if html[i] == '[': depth += 1
                elif html[i] == ']':
                    depth -= 1
                    if depth == 0:
                        html = html[:bracket] + js + html[i+1:]
                        break
                i += 1
            break

    # Update count badges
    count = len(listings)
    html = BADGE_RE.sub(lambda m: m.group(1) + f'{count} Listings' + m.group(2), html)
    html = FCOUNT_RE.sub(lambda m: m.group(1) + f'{count} listings' + m.group(2), html)
    return html

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\n{'='*58}")
    print(f"  Shoreview Off-Market Map Update — {today}")
    print(f"{'='*58}\n")

    # Load current state
    if LISTINGS_JSON.exists():
        with open(LISTINGS_JSON) as f:
            current = json.load(f)
        print(f"  Loaded {len(current)} listings from listings.json")
    else:
        current = []
        print("  No listings.json — starting fresh.")

    # Fetch doc
    print("  Fetching doc...")
    try:
        html_doc = fetch_doc()
        print(f"  Fetched {len(html_doc):,} bytes")
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    # Parse
    doc_listings = parse_blocks(html_doc)
    print(f"  Parsed {len(doc_listings)} listing blocks from doc")
    if not doc_listings:
        print("  WARNING: 0 listings parsed — aborting.")
        sys.exit(1)

    # Rotate badges
    current = rotate_badges(current)

    # Diff
    updated, changelog, new_count = diff_and_merge(current, doc_listings)

    print("\n  CHANGELOG:")
    if changelog:
        for line in changelog:
            print(line)
    else:
        print("  No changes.")
    print()

    # Save JSON
    with open(LISTINGS_JSON, "w") as f:
        json.dump(updated, f, indent=2)
    print(f"  Saved {len(updated)} listings → listings.json")

    # Rebuild HTML
    for path, conf in [(CONFIDENTIAL_HTML, True), (FULL_HTML, False)]:
        if not path.exists():
            print(f"  SKIP: {path.name} not found")
            continue
        with open(path) as f:
            src = f.read()
        with open(path, "w") as f:
            f.write(inject(src, updated, confidential=conf))
        print(f"  Rebuilt {path.name}")

    print(f"\n  DONE: {today} +{new_count} new, {len(updated)} total")
    print(f"{'='*58}\n")

if __name__ == "__main__":
    main()
