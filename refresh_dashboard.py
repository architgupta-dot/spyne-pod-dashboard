#!/usr/bin/env python3
"""
Spyne POD Dashboard — live HubSpot refresh script.

Pulls deals + recent contacts straight from the HubSpot API (no manual
copy/paste, no transcription risk) and rewrites the data arrays inside
index.html in place. Designed to run in GitHub Actions on a schedule
or via manual dispatch (see .github/workflows/refresh.yml).

Required env var:
    HUBSPOT_TOKEN   - HubSpot Private App access token
                       scopes needed: crm.objects.deals.read,
                                      crm.objects.contacts.read,
                                      crm.objects.owners.read
"""
import os
import re
import sys
import json
from datetime import datetime, timezone, timedelta

import requests

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN")
if not HUBSPOT_TOKEN:
    sys.exit("ERROR: HUBSPOT_TOKEN env var not set")

HS_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_TOKEN}",
    "Content-Type": "application/json",
}

# The 12 POD owners shown as filter pills on the dashboard.
OWNER_IDS = [
    "67333606", "160768701", "160575588", "160419465",
    "160043135", "79528942", "160214774", "160353848",
    "66975998", "159865948", "69016314", "160673631",
]

DEAL_PIPELINE = "1001348836"  # the POD's main pipeline

# Stage groupings used by the dashboard's derived tabs.
STAGE_DEMO = {"1534611153", "1534611151", "1534610164", "1534610165", "1534610163"}
STAGE_IN_DISCUSSION = {"1534611154"}
STAGE_FUTURE_PROSPECT = {"1534611159", "1534462676"}
STAGE_CLOSED = {"1534611156", "1534462673"}
ACTIVE_STAGES = {
    "1534611153", "1534611154", "1534611155", "1534611159",
    "1534611162", "1534611164", "1534611151", "1534610164",
}

HS_URL_TMPL = "https://app.hubspot.com/contacts/242626590/record/0-3/{id}"
HS_CONTACT_URL_TMPL = "https://app.hubspot.com/contacts/242626590/record/0-1/{id}"


def hs_search(object_type, filter_groups, properties, sorts=None, limit_total=None):
    """Paginate through a HubSpot CRM search endpoint, return list of results."""
    url = f"{HS_BASE}/crm/v3/objects/{object_type}/search"
    results = []
    after = None
    while True:
        body = {
            "filterGroups": filter_groups,
            "properties": properties,
            "limit": 200,
        }
        if sorts:
            body["sorts"] = sorts
        if after:
            body["after"] = after
        resp = requests.post(url, headers=HEADERS, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        if limit_total and len(results) >= limit_total:
            return results[:limit_total]
        paging = data.get("paging", {}).get("next", {}).get("after")
        if not paging:
            break
        after = paging
    return results


def fetch_deals():
    filter_groups = [{
        "filters": [
            {"propertyName": "hubspot_owner_id", "operator": "IN", "values": OWNER_IDS}
        ]
    }]
    props = ["dealname", "amount", "dealstage", "pipeline", "closedate",
             "hubspot_owner_id", "hs_lastmodifieddate", "createdate"]
    raw = hs_search("deals", filter_groups, props)
    deals = []
    for r in raw:
        p = r["properties"]
        name = (p.get("dealname") or "").strip()
        if not name:
            continue
        amt = p.get("amount")
        try:
            amt = int(float(amt)) if amt not in (None, "") else 0
        except ValueError:
            amt = 0
        deals.append({
            "id": r["id"],
            "n": name,
            "a": amt,
            "s": p.get("dealstage") or "",
            "o": p.get("hubspot_owner_id") or "",
            "c": (p.get("closedate") or "")[:10],
            "url": HS_URL_TMPL.format(id=r["id"]),
        })
    return deals


def fetch_recent_contacts(days=90):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    filter_groups = [{
        "filters": [
            {"propertyName": "hubspot_owner_id", "operator": "IN", "values": OWNER_IDS},
            {"propertyName": "createdate", "operator": "GTE", "value": since},
        ]
    }]
    props = ["firstname", "lastname", "email", "jobtitle", "company",
             "hubspot_owner_id", "createdate", "lifecyclestage", "hs_analytics_source"]
    sorts = [{"propertyName": "createdate", "direction": "DESCENDING"}]
    raw = hs_search("contacts", filter_groups, props, sorts=sorts, limit_total=500)
    leads = []
    for r in raw:
        p = r["properties"]
        fn = (p.get("firstname") or "").strip()
        ln = (p.get("lastname") or "").strip()
        email = p.get("email") or ""
        name = f"{fn} {ln}".strip() or email or r["id"]
        stage = p.get("lifecyclestage") or "lead"
        src_raw = p.get("hs_analytics_source") or "Offline"
        src = "Inbound" if src_raw not in ("OFFLINE",) else "Offline"
        leads.append({
            "id": r["id"],
            "n": name,
            "co": p.get("company") or "",
            "ti": p.get("jobtitle") or "",
            "em": email,
            "o": p.get("hubspot_owner_id") or "",
            "stage": stage,
            "cr": (p.get("createdate") or "")[:10],
            "src": src,
            "url": HS_CONTACT_URL_TMPL.format(id=r["id"]),
        })
    return leads


def js_escape(s):
    return json.dumps(s, ensure_ascii=False)


def deal_row_js(d):
    return (
        '{id:"%s",n:%s,a:%s,s:"%s",o:"%s",c:"%s",url:"%s"}'
        % (d["id"], js_escape(d["n"]), d["a"], d["s"], d["o"], d["c"], d["url"])
    )


def lead_row_js(l):
    return (
        '{id:"%s",n:%s,co:%s,ti:%s,em:%s,o:"%s",stage:"%s",cr:"%s",src:"%s",url:"%s"}'
        % (l["id"], js_escape(l["n"]), js_escape(l["co"]), js_escape(l["ti"]),
           js_escape(l["em"]), l["o"], l["stage"], l["cr"], l["src"], l["url"])
    )


def build_array_js(var_name, rows_js):
    inner = ",\n".join(rows_js)
    return f"const {var_name}=[\n{inner}\n];"


def replace_array(html, var_name, new_js):
    pattern = re.compile(
        r"const " + re.escape(var_name) + r"=\[.*?\];",
        re.DOTALL,
    )
    if not pattern.search(html):
        raise RuntimeError(f"Could not find array '{var_name}' in index.html")
    return pattern.sub(lambda m: new_js, html, count=1)


def main():
    print("Fetching deals from HubSpot...")
    deals = fetch_deals()
    print(f"  {len(deals)} deals fetched")

    print("Fetching recent contacts from HubSpot...")
    leads = fetch_recent_contacts(days=90)
    print(f"  {len(leads)} recent contacts fetched")

    inbound = [l for l in leads if l["cr"] >= (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")]

    demo_deals = [d for d in deals if d["s"] in STAGE_DEMO]
    in_discussion = sorted([d for d in deals if d["s"] in STAGE_IN_DISCUSSION], key=lambda d: -d["a"])
    future_prospect = sorted([d for d in deals if d["s"] in STAGE_FUTURE_PROSPECT], key=lambda d: -d["a"])
    closed_deals = sorted([d for d in deals if d["s"] in STAGE_CLOSED], key=lambda d: d["c"] or "", reverse=True)

    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    html = replace_array(html, "DEALS", build_array_js("DEALS", [deal_row_js(d) for d in deals]))
    html = replace_array(html, "DEMO_DEALS", build_array_js("DEMO_DEALS", [deal_row_js(d) for d in demo_deals]))
    html = replace_array(html, "IN_DISCUSSION_DEALS", build_array_js("IN_DISCUSSION_DEALS", [deal_row_js(d) for d in in_discussion]))
    html = replace_array(html, "FP_DEALS", build_array_js("FP_DEALS", [deal_row_js(d) for d in future_prospect]))
    html = replace_array(html, "CLOSED_DEALS", build_array_js("CLOSED_DEALS", [deal_row_js(d) for d in closed_deals]))
    html = replace_array(html, "LEADS_DATA", build_array_js("LEADS_DATA", [lead_row_js(l) for l in leads]))
    html = replace_array(html, "INBOUND_LEADS_DATA", build_array_js("INBOUND_LEADS_DATA", [lead_row_js(l) for l in inbound]))

    # Update date labels (same behavior as the old update_date.py)
    IST = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(IST)
    date_full = today.strftime("%b %d, %Y")
    date_short = today.strftime("%b %-d")
    date_iso = today.strftime("%Y-%m-%d")
    month_year = today.strftime("%b '%y")

    html = re.sub(r'(Spyne POD — Sales Operations · )[\w\d ,]+', r'\g<1>' + date_full, html)
    html = re.sub(r'(Refreshed )[\w\d ]+(  · Live)', r'\g<1>' + date_short + r'\g<2>', html)
    html = re.sub(r'(All active deals — )[\w\d ,]+', r'\g<1>' + date_full, html)
    html = re.sub(r'(Last 30 Days · )[\w\d ]+', r'\g<1>' + date_short, html)
    html = re.sub(r'(Refreshed )[\w\d ]+(?= *</span>)', r'\g<1>' + date_short, html)
    html = re.sub(r'new Date\("[0-9]{4}-[0-9]{2}-[0-9]{2}"\)', f'new Date("{date_iso}")', html)
    html = re.sub(r"(Closing )[A-Z][a-z]+ '[0-9]{2}", r'\g<1>' + month_year, html)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✓ index.html refreshed — {len(deals)} deals, {len(leads)} leads, {len(inbound)} inbound (last 30d)")


if __name__ == "__main__":
    main()
