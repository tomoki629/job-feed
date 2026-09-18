#!/usr/bin/env python3
"""Collect current job postings from public sources into feed.json.

Runs on GitHub Actions (normal internet access). Each source is best-effort:
a failure in one source never stops the others. Every record carries the
posting date and the closing date as published by the source, so the job
scan can trust them without opening the page itself.

Record fields: source, title, org, location, country, posted (YYYY-MM-DD or null),
closing (YYYY-MM-DD or null), url, grade, summary.
"""
import datetime as dt
import json
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

TODAY = dt.date.today()
MAX_AGE_DAYS = 45
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-feed/1.0; +https://github.com/tomoki629/job-feed)"}

KEYWORDS = [
    "employment", "livelihood", "social protection", "crisis", "recovery", "resilience",
    "labour", "labor", "decent work", "humanitarian", "programme", "program ", "project manager",
    "coordinator", "partnership", "policy", "resource mobili", "cash", "displacement", "refugee",
    "disaster", "emergency", "head of", "country director", "deputy", "advisor", "adviser",
    "specialist", "economic inclusion", "social development", "safeguard", "fragil", "peace",
    "infrastructure", "public works", "skills", "enterprise", "nexus", "migration", "protection",
    "chief", "director", "manager", "officer", "analyst", "fellow", "researcher",
]
JUNIOR = re.compile(r"\b(intern|internship|volunteer|UNV\b|junior|assistant|driver|clerk|secretary|G[1-7]\b|GS-?[1-7]\b|P-?[12]\b|NO-?[AB]\b|SB-?[1-3]\b)", re.I)


def norm_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s[:len(dt.datetime.now().strftime(fmt))] if "%z" not in fmt else s, fmt).date().isoformat()
        except Exception:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", s)
    if m:
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                return dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt).date().isoformat()
            except Exception:
                pass
    return None


def relevant(rec):
    text = f"{rec.get('title','')} {rec.get('summary','')}".lower()
    if JUNIOR.search(rec.get("title", "")):
        return False
    return any(k in text for k in KEYWORDS)


def current(rec):
    c = rec.get("closing")
    p = rec.get("posted")
    if c and dt.date.fromisoformat(c) < TODAY:
        return False
    if p and (TODAY - dt.date.fromisoformat(p)).days > MAX_AGE_DAYS:
        return False
    if not c and not p:
        return False
    return True


def grade_of(text):
    m = re.search(r"\b(P-?[3-7]|D-?[12]|NO-?[CD]|G-?7|IPSA-?1[0-2]|NPSA-?1[0-2]|SB-?[45]|L-?[6-9]|ICS-?1[0-2]|GS-?7|PL-?[1-6]|EL-?[1-2])\b", text, re.I)
    return m.group(1).upper() if m else None


# ---------------------------------------------------------------- ReliefWeb
def reliefweb():
    out = []
    since = (TODAY - dt.timedelta(days=MAX_AGE_DAYS)).strftime("%Y-%m-%dT00:00:00+00:00")
    body = {
        "limit": 1000,
        "sort": ["date.created:desc"],
        "filter": {"field": "date.created", "value": {"from": since}},
        "fields": {"include": ["title", "source.name", "city.name", "country.name", "date.created", "date.closing", "url", "career_categories.name", "experience.name", "body"]},
    }
    offset = 0
    while True:
        body["offset"] = offset
        r = requests.post("https://api.reliefweb.int/v1/jobs?appname=job-feed", json=body, headers=UA, timeout=60)
        r.raise_for_status()
        data = r.json().get("data", [])
        for it in data:
            f = it.get("fields", {})
            out.append({
                "source": "ReliefWeb",
                "title": f.get("title", ""),
                "org": ", ".join(s.get("name", "") for s in f.get("source", [])) if isinstance(f.get("source"), list) else (f.get("source", {}) or {}).get("name", ""),
                "location": ", ".join(c.get("name", "") for c in f.get("city", [])) if f.get("city") else "",
                "country": ", ".join(c.get("name", "") for c in f.get("country", [])) if f.get("country") else "",
                "posted": norm_date(f.get("date", {}).get("created")),
                "closing": norm_date(f.get("date", {}).get("closing")),
                "url": f.get("url", ""),
                "grade": grade_of(f.get("title", "") + " " + (f.get("body") or "")[:400]),
                "summary": re.sub(r"\s+", " ", (f.get("body") or ""))[:400],
            })
        if len(data) < body["limit"] or offset > 5000:
            break
        offset += body["limit"]
        time.sleep(1)
    return out


# ---------------------------------------------------------------- unjobs.org
UNJOBS_PAGES = [
    "https://unjobs.org/duty_stations/thailand", "https://unjobs.org/duty_stations/geneva",
    "https://unjobs.org/duty_stations/paris", "https://unjobs.org/duty_stations/rome",
    "https://unjobs.org/duty_stations/brussels", "https://unjobs.org/duty_stations/london",
    "https://unjobs.org/duty_stations/vienna", "https://unjobs.org/duty_stations/bonn",
    "https://unjobs.org/duty_stations/copenhagen", "https://unjobs.org/duty_stations/philippines",
    "https://unjobs.org/duty_stations/indonesia", "https://unjobs.org/duty_stations/malaysia",
    "https://unjobs.org/duty_stations/viet-nam", "https://unjobs.org/duty_stations/cambodia",
    "https://unjobs.org/duty_stations/myanmar", "https://unjobs.org/duty_stations/japan",
    "https://unjobs.org/duty_stations/nepal", "https://unjobs.org/duty_stations/jordan",
    "https://unjobs.org/duty_stations/lebanon", "https://unjobs.org/duty_stations/turkey",
    "https://unjobs.org/duty_stations/home-based", "https://unjobs.org/organizations/ilo",
    "https://unjobs.org/organizations/undp", "https://unjobs.org/organizations/unhcr",
    "https://unjobs.org/organizations/wfp", "https://unjobs.org/organizations/iom",
    "https://unjobs.org/organizations/unops", "https://unjobs.org/organizations/adb",
    "https://unjobs.org/organizations/aiib", "https://unjobs.org/organizations/world-bank",
]


def unjobs():
    out = []
    for url in UNJOBS_PAGES:
        try:
            r = requests.get(url, headers=UA, timeout=45)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for job in soup.select("div.job"):
                a = job.find("a", href=True)
                if not a:
                    continue
                text = job.get_text(" ", strip=True)
                closing = None
                m = re.search(r"Closing date:?\s*([^\n|]+?\d{4})", text)
                if m:
                    closing = norm_date(m.group(1))
                posted = None
                m2 = re.search(r"Updated:?\s*([^\n|]+?\d{4})", text)
                if m2:
                    posted = norm_date(m2.group(1))
                org = ""
                b = job.find("br")
                if b and b.next_sibling and isinstance(b.next_sibling, str):
                    org = b.next_sibling.strip()
                out.append({
                    "source": "unjobs.org", "title": a.get_text(strip=True), "org": org,
                    "location": url.rsplit("/", 1)[-1].replace("-", " ").title() if "duty_stations" in url else "",
                    "country": "", "posted": posted, "closing": closing, "url": a["href"],
                    "grade": grade_of(a.get_text(strip=True)), "summary": text[:300],
                })
            time.sleep(1.5)
        except Exception as e:
            print("unjobs page failed", url, e, file=sys.stderr)
    return out


# ---------------------------------------------------------------- ILO (SuccessFactors)
def ilo():
    out = []
    try:
        r = requests.get("https://jobs.ilo.org/search/?q=&sortColumn=referencedate&sortOrder=desc&startrow=0", headers=UA, timeout=45)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("tr.data-row")
        for row in rows[:80]:
            a = row.select_one("a.jobTitle-link")
            if not a:
                continue
            url = "https://jobs.ilo.org" + a["href"]
            loc = row.select_one("span.jobLocation")
            date = row.select_one("span.jobDate")
            rec = {"source": "ILO", "title": a.get_text(strip=True), "org": "ILO",
                   "location": loc.get_text(strip=True) if loc else "", "country": "",
                   "posted": norm_date(date.get_text(strip=True)) if date else None, "closing": None,
                   "url": url, "grade": grade_of(a.get_text(strip=True)), "summary": ""}
            try:
                d = requests.get(url, headers=UA, timeout=45)
                txt = BeautifulSoup(d.text, "html.parser").get_text(" ", strip=True)
                m = re.search(r"(?:Application deadline|Closing date|Deadline)[^0-9]{0,40}(\d{1,2}\s+[A-Za-z]+\s+\d{4})", txt, re.I)
                if m:
                    rec["closing"] = norm_date(m.group(1))
                g = grade_of(txt[:3000])
                if g:
                    rec["grade"] = g
                rec["summary"] = txt[:400]
                time.sleep(0.7)
            except Exception as e:
                print("ilo detail failed", url, e, file=sys.stderr)
            out.append(rec)
    except Exception as e:
        print("ilo failed", e, file=sys.stderr)
    return out


# ---------------------------------------------------------------- UNDP (Oracle HCM)
def undp():
    out = []
    try:
        api = ("https://estm.fa.em2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
               "?onlyData=true&expand=requisitionList.secondaryLocations,flexFieldsFacet.values"
               "&finder=findReqs;siteNumber=CX_1,facetsList=LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES%3BCATEGORIES%3BORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS,limit=200,sortBy=POSTING_DATES_DESC")
        r = requests.get(api, headers=UA, timeout=60)
        r.raise_for_status()
        items = r.json().get("items", [{}])[0].get("requisitionList", [])
        for it in items:
            url = f"https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/{it.get('Id')}"
            out.append({"source": "UNDP", "title": it.get("Title", ""), "org": "UNDP",
                        "location": it.get("PrimaryLocation", ""), "country": it.get("PrimaryLocationCountry", ""),
                        "posted": norm_date(it.get("PostedDate")), "closing": norm_date(it.get("PostingEndDate") or it.get("ExpirationDate")),
                        "url": url, "grade": grade_of(it.get("Title", "") + " " + (it.get("ShortDescriptionStr") or "")),
                        "summary": re.sub(r"<[^>]+>", " ", it.get("ShortDescriptionStr") or "")[:400]})
    except Exception as e:
        print("undp failed", e, file=sys.stderr)
    return out


# ---------------------------------------------------------------- UN Careers (Inspira)
def uncareers():
    out = []
    try:
        api = "https://careers.un.org/api/public/opening/jo/list/filteredV2/en"
        payload = {"filterConfig": {"keyword": "", "jn": [], "jc": [], "jf": [], "jl": [], "dept": [], "jo": []}, "pagination": {"page": 0, "itemPerPage": 200, "sortBy": "startDate", "sortDirection": -1}}
        r = requests.post(api, json=payload, headers={**UA, "Content-Type": "application/json"}, timeout=60)
        r.raise_for_status()
        for it in r.json().get("data", {}).get("list", []):
            out.append({"source": "UN Careers", "title": it.get("jobTitle", ""), "org": it.get("jn", "") or "United Nations Secretariat",
                        "location": it.get("dutyStation", [""])[0] if isinstance(it.get("dutyStation"), list) else str(it.get("dutyStation", "")),
                        "country": "", "posted": norm_date(it.get("startDate")), "closing": norm_date(it.get("endDate")),
                        "url": f"https://careers.un.org/jobSearchDescription/{it.get('jobId')}?language=en",
                        "grade": it.get("jc", "") or grade_of(it.get("jobTitle", "")), "summary": ""})
    except Exception as e:
        print("uncareers failed", e, file=sys.stderr)
    return out


def main():
    records = []
    for fn in (reliefweb, unjobs, ilo, undp, uncareers):
        try:
            got = fn()
            print(fn.__name__, len(got), file=sys.stderr)
            records.extend(got)
        except Exception as e:
            print(fn.__name__, "failed", e, file=sys.stderr)
    seen, kept = set(), []
    for r in records:
        key = (r.get("title", "").lower().strip(), r.get("org", "").lower().strip())
        if key in seen or not r.get("title"):
            continue
        if not current(r) or not relevant(r):
            continue
        seen.add(key)
        kept.append(r)
    kept.sort(key=lambda r: (r.get("posted") or ""), reverse=True)
    json.dump({"generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "count": len(kept), "jobs": kept},
              open("feed.json", "w"), ensure_ascii=False, indent=1)
    print("kept", len(kept), file=sys.stderr)


if __name__ == "__main__":
    main()
