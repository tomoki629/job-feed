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


def txt(v):
    """Coerce any API value (str, dict, list, None) to a short plain string."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for k in ("name", "value", "label", "title", "en"):
            if k in v and isinstance(v[k], str):
                return v[k].strip()
        return " ".join(txt(x) for x in v.values() if isinstance(x, (str, dict, list)))[:200]
    if isinstance(v, list):
        return ", ".join(t for t in (txt(x) for x in v) if t)
    return str(v)


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
    if not c and p and (TODAY - dt.date.fromisoformat(p)).days > 21:
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
    for appname in ("rwint-user-0", "apidoc", "rw-user-0"):
        try:
            offset, ok = 0, False
            while True:
                body["offset"] = offset
                r = requests.post(f"https://api.reliefweb.int/v2/jobs?appname={appname}", json=body, headers=UA, timeout=60)
                if r.status_code != 200:
                    print("DEBUG reliefweb", appname, r.status_code, r.text[:200], file=sys.stderr)
                    break
                ok = True
                data = r.json().get("data", [])
                for it in data:
                    f = it.get("fields", {})
                    out.append({
                        "source": "ReliefWeb", "title": txt(f.get("title")), "org": txt(f.get("source")),
                        "location": txt(f.get("city")), "country": txt(f.get("country")),
                        "posted": norm_date((f.get("date") or {}).get("created")),
                        "closing": norm_date((f.get("date") or {}).get("closing")),
                        "url": txt(f.get("url")), "grade": grade_of(txt(f.get("title")) + " " + (f.get("body") or "")[:400]),
                        "summary": re.sub(r"\s+", " ", (f.get("body") or ""))[:400],
                    })
                if len(data) < body["limit"] or offset > 5000:
                    break
                offset += body["limit"]
                time.sleep(1)
            if ok:
                return out
        except Exception as e:
            print("reliefweb api failed", appname, e, file=sys.stderr)
    # RSS fallback: newest postings with closing date inside the description
    try:
        r = requests.get("https://reliefweb.int/jobs/rss.xml", headers=UA, timeout=60)
        print("DEBUG reliefweb rss", r.status_code, len(r.text), repr(r.text[:200]), file=sys.stderr)
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.find_all("item"):
            desc = BeautifulSoup(item.description.get_text() if item.description else "", "html.parser").get_text(" ", strip=True)
            m = re.search(r"Closing date:?\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", desc)
            org = ""
            mo = re.search(r"Organization:?\s*([^|]+?)(?:\s{2,}|Closing|Country|$)", desc)
            if mo:
                org = mo.group(1).strip()
            out.append({"source": "ReliefWeb", "title": item.title.get_text(strip=True) if item.title else "", "org": org,
                        "location": "", "country": "", "posted": norm_date(item.pubDate.get_text() if item.pubDate else None),
                        "closing": norm_date(m.group(1)) if m else None, "url": item.link.get_text(strip=True) if item.link else "",
                        "grade": grade_of(desc[:400]), "summary": desc[:400]})
    except Exception as e:
        print("reliefweb rss failed", e, file=sys.stderr)
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
                print("unjobs status", r.status_code, url, file=sys.stderr)
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for job in soup.select("div.job"):
                a = job.find("a", href=True)
                if not a:
                    continue
                parts = [p.strip() for p in job.get_text(" | ", strip=True).split("|") if p.strip()]
                title = a.get_text(strip=True)
                org = ""
                for p in parts[1:]:
                    if p.lower().startswith("updated") or re.match(r"\d{4}-\d{2}-\d{2}", p):
                        break
                    org = p
                m = re.search(r"(\d{4}-\d{2}-\d{2})T", job.get_text(" ", strip=True))
                posted = m.group(1) if m else None
                out.append({
                    "source": "unjobs.org", "title": title, "org": org,
                    "location": url.rsplit("/", 1)[-1].replace("-", " ").title() if "duty_stations" in url else "",
                    "country": "", "posted": posted, "closing": None, "url": a["href"],
                    "grade": grade_of(title), "summary": "",
                })
            time.sleep(1.5)
        except Exception as e:
            print("unjobs page failed", url, e, file=sys.stderr)
    # closing dates live on the detail pages: fetch them for recent, relevant, non-junior postings
    seen = set()
    fetched = 0
    for rec in out:
        if rec["url"] in seen or not rec["posted"] or (TODAY - dt.date.fromisoformat(rec["posted"])).days > 21:
            continue
        if not relevant(rec):
            continue
        seen.add(rec["url"])
        if fetched >= 250:
            break
        try:
            d = requests.get(rec["url"], headers=UA, timeout=45)
            t = BeautifulSoup(d.text, "html.parser").get_text(" ", strip=True)
            if fetched < 2:
                i = t.lower().find("closing")
                print("DEBUG unjobs detail status", d.status_code, "len", len(t), "around closing:", repr(t[max(0, i-80):i+160]) if i >= 0 else repr(t[:300]), file=sys.stderr)
            m = re.search(r"(?:Closing date|Deadline|Apply by|Application deadline)[^0-9A-Za-z]{0,10}(?:[A-Za-z]+,?\s*)?(\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", t, re.I)
            if m:
                rec["closing"] = norm_date(m.group(1).replace(".", ""))
            g = grade_of(t[:2500])
            if g:
                rec["grade"] = g
            rec["summary"] = t[t.find(rec["title"]):][:400] if rec["title"] in t else t[:400]
            fetched += 1
            time.sleep(1)
        except Exception as e:
            print("unjobs detail failed", rec["url"], e, file=sys.stderr)
    print("DEBUG unjobs details fetched", fetched, "with closing", sum(1 for r in out if r["closing"]), file=sys.stderr)
    return out


# ---------------------------------------------------------------- ILO (SuccessFactors)
def ilo():
    out = []
    try:
        r = requests.get("https://jobs.ilo.org/search/?q=&sortColumn=referencedate&sortOrder=desc&startrow=0", headers=UA, timeout=45)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("tr.data-row")
        print("DEBUG ilo rows", len(rows), "status", r.status_code, "head", repr(r.text[:300]), file=sys.stderr)
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
        if items:
            print("DEBUG undp keys:", sorted(items[0].keys()), file=sys.stderr)
            print("DEBUG undp sample:", {k: items[0][k] for k in items[0] if "Date" in k or "date" in k}, file=sys.stderr)
        for n, it in enumerate(items):
            url = f"https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/{it.get('Id')}"
            closing = it.get("PostingEndDate") or it.get("ExpirationDate")
            it["_closing"] = closing
            out.append({"source": "UNDP", "title": it.get("Title", ""), "org": "UNDP",
                        "location": it.get("PrimaryLocation", ""), "country": it.get("PrimaryLocationCountry", ""),
                        "posted": norm_date(it.get("PostedDate")), "closing": norm_date(it.get("_closing")),
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
        payload = {"filterConfig": {"keyword": "", "jn": [], "jc": [], "jf": [], "jl": [], "dept": [], "jo": []}, "pagination": {"page": 0, "itemPerPage": 500, "sortBy": "startDate", "sortDirection": -1}}
        r = requests.post(api, json=payload, headers={**UA, "Content-Type": "application/json"}, timeout=60)
        r.raise_for_status()
        for it in r.json().get("data", {}).get("list", []):
            title = txt(it.get("jobTitle"))
            loc = re.sub(r"^\d+\s+", "", txt(it.get("dutyStation")))
            loc = re.sub(r"\s+[0-9a-f]{8,}$", "", loc).title()
            out.append({"source": "UN Careers", "title": title, "org": txt(it.get("jn")) or "United Nations Secretariat",
                        "location": loc, "country": "",
                        "posted": norm_date(it.get("startDate")), "closing": norm_date(it.get("endDate")),
                        "url": f"https://careers.un.org/jobSearchDescription/{it.get('jobId')}?language=en",
                        "grade": txt(it.get("jc")) or grade_of(title), "summary": txt(it.get("jf"))})
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
        for k in ("title", "org", "location", "country", "url", "summary"):
            r[k] = txt(r.get(k))
        if r.get("grade") is not None and not isinstance(r["grade"], str):
            r["grade"] = txt(r["grade"]) or None
        key = (r.get("title", "").lower().strip(), r.get("org", "").lower().strip())
        if key in seen or not r.get("title"):
            continue
        if not current(r) or not relevant(r):
            continue
        seen.add(key)
        kept.append(r)
    kept.sort(key=lambda r: (r.get("posted") or ""), reverse=True)
    json.dump({"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "count": len(kept), "jobs": kept},
              open("feed.json", "w"), ensure_ascii=False, indent=1)
    print("kept", len(kept), file=sys.stderr)


if __name__ == "__main__":
    main()
