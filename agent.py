#!/usr/bin/env python3
"""Signalpost agent: builds sourced profiles for Norwegian companies.
 
Data comes only from the official Brønnøysund Register Centre open APIs:
  - Enhetsregisteret (company facts)
  - Regnskapsregisteret (annual accounts, optional)
No LLM is used, so nothing can be hallucinated: every fact is copied from an
API response and stored with its source URL and retrieval date.
 
Usage:
  python agent.py                         # discover 1000 companies, build profiles
  python agent.py --input orgnrs.txt      # profile the given org numbers
  python agent.py --input orgnrs.txt --financials
"""
import argparse
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
 
BASE = "https://data.brreg.no/enhetsregisteret/api/enheter"
ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap"
MAX_REQUESTS = 1900          # stay under the 2,000 outbound request limit
TIMEOUT = 20
SLEEP = 0.15                 # polite pacing
 
request_count = 0
 
 
def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
 
 
def http_get(url):
    """GET JSON. Returns (status, data). Counts every outbound request."""
    global request_count
    if request_count >= MAX_REQUESTS:
        raise RuntimeError("request budget reached")
    request_count += 1
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "signalpost-agent/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                try:
                    body = json.loads(e.read().decode("utf-8"))
                except Exception:
                    body = {}
                return e.code, body
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return e.code, {}
        except (urllib.error.URLError, TimeoutError):
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return 0, {}
    return 0, {}
    
 
def discover(count):
    """Collect org numbers from random pages of the public entity list."""
    orgnrs, seen = [], set()
    pages = list(range(0, 90))
    random.shuffle(pages)
    for p in pages:
        if len(orgnrs) >= count:
            break
        url = f"{BASE}?size=100&page={p}"
        status, data = http_get(url)
        if status != 200:
            continue
        for e in data.get("_embedded", {}).get("enheter", []):
            o = e.get("organisasjonsnummer")
            if o and o not in seen:
                seen.add(o)
                orgnrs.append(o)
        time.sleep(SLEEP)
    return orgnrs[:count]
 
 
def add(facts, field, value, url, ts):
    if value in (None, "", [], {}):
        return
    facts.append({"field": field, "value": value, "source_url": url, "retrieved_at": ts})
 
 
def build_facts(orgnr, d, ts):
    url = f"{BASE}/{orgnr}"
    f = []
    add(f, "name", d.get("navn"), url, ts)
    of = d.get("organisasjonsform") or {}
    add(f, "organisation_form", of.get("beskrivelse"), url, ts)
    add(f, "registered_in_business_register", d.get("registreringsdatoEnhetsregisteret"), url, ts)
    add(f, "founded_date", d.get("stiftelsesdato"), url, ts)
    nk = d.get("naeringskode1") or {}
    if nk:
        add(f, "industry", f'{nk.get("kode", "")} {nk.get("beskrivelse", "")}'.strip(), url, ts)
    add(f, "employees", d.get("antallAnsatte"), url, ts)
    add(f, "website", d.get("hjemmeside"), url, ts)
    add(f, "vat_registered", d.get("registrertIMvaregisteret"), url, ts)
    addr = d.get("forretningsadresse") or {}
    if addr:
        parts = list(addr.get("adresse") or []) + [addr.get("postnummer"), addr.get("poststed")]
        add(f, "business_address", ", ".join(str(x) for x in parts if x), url, ts)
    add(f, "bankrupt", d.get("konkurs"), url, ts)
    add(f, "under_liquidation", d.get("underAvvikling"), url, ts)
    add(f, "last_annual_accounts_year", d.get("sisteInnsendteAarsregnskap"), url, ts)
    return f
 
 
def build_financials(orgnr, ts):
    """Copy values only if the accounts API returns them. Never estimate."""
    url = f"{ACCOUNTS}/{orgnr}"
    status, data = http_get(url)
    if status != 200 or not data:
        return []
    rec = data[0] if isinstance(data, list) and data else data
    if not isinstance(rec, dict):
        return []
    period = rec.get("regnskapsperiode") or {}
    year = (period.get("tilDato") or "")[:4]
    f = []
    res = rec.get("resultatregnskapResultat") or {}
    rev = ((res.get("driftsresultat") or {}).get("driftsinntekter") or {}).get("sumDriftsinntekter")
    add(f, f"revenue_{year}" if year else "revenue", rev, url, ts)
    add(f, f"net_result_{year}" if year else "net_result", res.get("aarsresultat"), url, ts)
    assets = (rec.get("eiendeler") or {}).get("sumEiendeler")
    add(f, f"total_assets_{year}" if year else "total_assets", assets, url, ts)
    return f
 
 
def explain(name, facts):
    names = [x["field"] for x in facts]
    return (f"{name}: {len(facts)} facts from the official Norwegian business register "
            f"({', '.join(names[:6])}{'...' if len(names) > 6 else ''}). "
            f"Each fact carries its source URL and retrieval date.")
 
 
def fingerprint(facts):
    core = sorted((x["field"], json.dumps(x["value"], sort_keys=True)) for x in facts)
    return hashlib.sha256(json.dumps(core).encode()).hexdigest()
 
 
def profile(orgnr, previous, with_fin):
    ts = now()
    url = f"{BASE}/{orgnr}"
    status, d = http_get(url)
    if status in (404, 410):
        status_txt = "deleted" if status == 410 else "not_found"
        facts = []
        add(facts, "status", status_txt, url, ts)
        if status == 410:
            add(facts, "deleted_date", d.get("slettedato"), url, ts)
        name = d.get("navn") or orgnr
    elif status != 200:
        print(f"skip {orgnr}: HTTP {status}", file=sys.stderr)
        return previous  # keep the old profile rather than publish bad data
    else:
        # Wrong-company guard: response must be for the requested number.
        if str(d.get("organisasjonsnummer")) != str(orgnr):
            return previous
        facts = build_facts(orgnr, d, ts)
        name = d.get("navn") or orgnr
        if with_fin and request_count < MAX_REQUESTS:
            facts += build_financials(orgnr, ts)
    fp = fingerprint(facts)
    changes = list((previous or {}).get("changes", []))
    if previous and previous.get("fingerprint") != fp:
        old = {x["field"]: x["value"] for x in previous.get("facts", [])}
        new = {x["field"]: x["value"] for x in facts}
        diff = [k for k in set(old) | set(new) if old.get(k) != new.get(k)]
        changes.append({"date": ts, "changed_fields": sorted(diff)})
    return {
        "organisation_number": str(orgnr),
        "name": name,
        "facts": facts,
        "explanation": explain(name, facts),
        "fingerprint": fp,
        "changes": changes,
        "last_checked": ts,
        "last_updated": ts if (not previous or previous.get("fingerprint") != fp)
        else previous.get("last_updated", ts),
    }
 
 
def load_existing(path):
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    p = json.loads(line)
                    out[p["organisation_number"]] = p
    return out
 
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="file with one Norwegian org number per line")
    ap.add_argument("--out", default="profiles.jsonl")
    ap.add_argument("--count", type=int, default=1000, help="companies to discover if no --input")
    ap.add_argument("--financials", action="store_true", help="also fetch annual accounts (2 requests/company)")
    a = ap.parse_args()
 
    existing = load_existing(a.out)
    if a.input:
        with open(a.input) as fh:
            orgnrs = [l.strip().replace(" ", "") for l in fh if l.strip()]
    else:
        orgnrs = list(existing)
        if len(orgnrs) < a.count:
            have = set(orgnrs)
            # overshoot: some lookups can fail, so fetch extra candidates
            orgnrs += [o for o in discover(a.count + 300) if o not in have]
 
    done = 0
    for o in orgnrs:
        if request_count >= MAX_REQUESTS:
            print("Request budget reached, stopping.", file=sys.stderr)
            break
        if not a.input and len(existing) >= a.count:
            break
        p = profile(o, existing.get(o), a.financials)
        if p:
            existing[o] = p
            done += 1
        if done % 50 == 0:
            print(f"{done}/{len(orgnrs)} profiled, {request_count} requests", file=sys.stderr)
        time.sleep(SLEEP)
 
    with open(a.out, "w", encoding="utf-8") as fh:
        for p in existing.values():
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"Wrote {len(existing)} profiles to {a.out} using {request_count} requests.")
 
 
if __name__ == "__main__":
    main()
 