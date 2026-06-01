#!/usr/bin/env python3
import re, base64, requests
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
today = datetime.now(IST)

date_full  = today.strftime("%b %d, %Y")
date_short = today.strftime("%b %-d")
date_iso   = today.strftime("%Y-%m-%d")
month_year = today.strftime("%b \'%y")

with open("index.html", "r") as f:
    html = f.read()

html = re.sub(r'(Spyne POD — Sales Operations · )[\w\d ,]+', r'\g<1>' + date_full, html)
html = re.sub(r'(Refreshed )[\w\d ]+(  · Live)', r'\g<1>' + date_short + r'\g<2>', html)
html = re.sub(r'(All active deals — )[\w\d ,]+', r'\g<1>' + date_full, html)
html = re.sub(r'(Last 30 Days · )[\w\d ]+', r'\g<1>' + date_short, html)
html = re.sub(r'(Refreshed )[\w\d ]+(?= *</span>)', r'\g<1>' + date_short, html)
html = re.sub(r'new Date\("[0-9]{4}-[0-9]{2}-[0-9]{2}"\)', f'new Date("{date_iso}")', html)
html = re.sub(r"(Closing )[A-Z][a-z]+ '[0-9]{2}", r'\g<1>' + month_year, html)

with open("index.html", "w") as f:
    f.write(html)

print(f"✓ Updated to {date_full}")
