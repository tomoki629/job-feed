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


GRADE_RE = re.compile(r"\b(P[\s-]?[1-7]|D[\s-]?[12]|NO[\s-]?[A-E]|G[\s-]?[1-7]|GS[\s-]?[1-7]|IPSA[\s-]?(?:8|9|1[0-3])|NPSA[\s-]?(?:[5-9]|1[0-2])|ICS[\s-]?(?:[6-9]|1[0-3])|SB[\s-]?[1-5]|LICA[\s-]?(?:[6-9]|1[0-2])|IICA[\s-]?[1-4]|L[\s-]?[3-9]|PL[\s-]?[1-6]|EL[\s-]?[1-2]|UNV|ASG|USG)\b", re.I)


def grade_of(text):
    """Return the first professional grade found in the text, normalised (P4, D1, NO-C, IPSA-11)."""
    if not text:
        return None
    m = re.search(r"(?:Grade|Level|Contract level|Job level|Post level)\s*[:\-]?\s*(" + GRADE_RE.pattern[3:-3] + r")", text, re.I)
    if not m:
        m = GRADE_RE.search(text)
    if not m:
        return None
    g = re.sub(r"[\s-]+", "", m.group(1).upper())
    g = re.sub(r"^(NO|IPSA|NPSA|ICS|SB|LICA|IICA|GS|PL|EL)", r"\1-", g)
    return g


ELIG_RE = re.compile(r"(open to [^.;\n]{0,80}|tier\s*[0-3][^.;\n]{0,60}|internal (?:candidates|applicants|staff)[^.;\n]{0,60}|only (?:nationals|citizens)[^.;\n]{0,60}|nationals? of [^.;\n]{0,60}|national (?:professional|position|personnel|post|staff)[^.;\n]{0,40}|roster[^.;\n]{0,40})", re.I)


def eligibility_of(text):
    if not text:
        return ""
    hits = [m.group(1).strip() for m in ELIG_RE.finditer(text)]
    return "; ".join(dict.fromkeys(hits))[:300]


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
    # HTML fallback: the public listing pages, newest first, then each posting page for dates
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}
    links = []
    for page in range(0, 6):
        try:
            r = requests.get(f"https://reliefweb.int/jobs?page={page}", headers=hdr, timeout=60)
            if page == 0:
                print("DEBUG reliefweb html", r.status_code, len(r.text), repr(r.text[:160]), file=sys.stderr)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            found = 0
            for a in soup.select("article a[href*='/job/'], h3 a[href*='/job/'], a.rw-river-article__title[href], a[href*='reliefweb.int/job/']"):
                href = a.get("href", "")
                if href.startswith("/"):
                    href = "https://reliefweb.int" + href
                if "/job/" in href and href not in [l[0] for l in links]:
                    links.append((href, a.get_text(" ", strip=True)))
                    found += 1
            if found == 0:
                break
            time.sleep(1.5)
        except Exception as e:
            print("reliefweb list failed", page, e, file=sys.stderr)
            break
    print("DEBUG reliefweb links", len(links), file=sys.stderr)
    for href, title in links[:180]:
        try:
            d = requests.get(href, headers=hdr, timeout=60)
            if d.status_code != 200:
                continue
            ds = BeautifulSoup(d.text, "html.parser")
            t = ds.get_text(" ", strip=True)
            def field(label):
                m = re.search(label + r"\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", t, re.I)
                return norm_date(m.group(1)) if m else None
            org = ""
            mo = re.search(r"Organi[sz]ation\s*[:\-]?\s*(.+?)\s+(?:Posted|Closing date|Country|Job type)", t)
            if mo:
                org = mo.group(1).strip()[:120]
            loc = ""
            ml = re.search(r"(?:Country|Countries)\s*[:\-]?\s*(.+?)\s+(?:City|Source|Organi|Posted|Closing)", t)
            if ml:
                loc = ml.group(1).strip()[:80]
            mc = re.search(r"City\s*[:\-]?\s*(.+?)\s+(?:Source|Organi|Posted|Closing|Job)", t)
            if mc:
                loc = (mc.group(1).strip()[:60] + ", " + loc).strip(", ")
            body_i = t.find(title) if title in t else 0
            out.append({"source": "ReliefWeb", "title": title or (ds.title.get_text(strip=True) if ds.title else ""), "org": org,
                        "location": loc, "country": "", "posted": field(r"Posted"), "closing": field(r"Closing date"),
                        "url": href, "grade": grade_of(t[body_i:body_i + 6000]), "eligibility": eligibility_of(t[body_i:body_i + 6000]),
                        "summary": t[body_i:body_i + 500]})
            time.sleep(1)
        except Exception as e:
            print("reliefweb detail failed", href, e, file=sys.stderr)
    print("DEBUG reliefweb records", len(out), "with closing", sum(1 for r in out if r["closing"]), file=sys.stderr)
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
            body = t[t.find(rec["title"]):] if rec["title"] in t else t
            g = grade_of(body[:6000])
            if g:
                rec["grade"] = g
            rec["eligibility"] = eligibility_of(body[:6000])
            rec["summary"] = body[:500]
            fetched += 1
            time.sleep(1)
        except Exception as e:
            print("unjobs detail failed", rec["url"], e, file=sys.stderr)
    print("DEBUG unjobs details fetched", fetched, "with closing", sum(1 for r in out if r["closing"]), file=sys.stderr)
    return out


# ---------------------------------------------------------------- unjobnet.org
DATE_ANY = r"(\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})"


def find_date(label_pat, text):
    m = re.search(label_pat + r"[^0-9A-Za-z]{0,12}(?:[A-Za-z]+day,?\s*)?" + DATE_ANY, text, re.I)
    return norm_date(m.group(1).replace(".", "")) if m else None


def unjobnet():
    out = []
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
    queries = ["livelihoods", "employment", "social protection", "crisis", "recovery", "resilience", "partnerships", "programme manager", "head of office", "country director", "policy"]
    seen = set()
    for q in queries:
        try:
            r = requests.get("https://www.unjobnet.org/jobs", params={"keywords": q}, headers=hdr, timeout=60)
            if q == queries[0]:
                print("DEBUG unjobnet", r.status_code, len(r.text), repr(r.text[:160]), file=sys.stderr)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href*='/jobs/detail/']"):
                href = a.get("href", "")
                if href.startswith("/"):
                    href = "https://www.unjobnet.org" + href
                if href in seen:
                    continue
                seen.add(href)
                card = a.find_parent(["div", "li", "article"]) or a
                text = card.get_text(" ", strip=True)
                out.append({"source": "unjobnet.org", "title": a.get_text(" ", strip=True)[:160], "org": "", "location": "", "country": "",
                            "posted": find_date(r"(?:Posted|Published|Date posted|Updated)", text),
                            "closing": find_date(r"(?:Closing|Deadline|Apply by|Expires?)", text), "url": href,
                            "grade": grade_of(text), "eligibility": "", "summary": text[:300]})
            time.sleep(1.5)
        except Exception as e:
            print("unjobnet failed", q, e, file=sys.stderr)
    # detail pages for dates, org and grade
    for n, rec in enumerate(out[:150]):
        try:
            d = requests.get(rec["url"], headers=hdr, timeout=60)
            ds = BeautifulSoup(d.text, "html.parser")
            main = ds.find("main") or ds.find("article") or ds.body or ds
            t = main.get_text(" ", strip=True)
            if n < 2:
                print("DEBUG unjobnet detail:", repr(t[:900]), file=sys.stderr)
            rec["posted"] = find_date(r"(?:Posted|Published|Date posted|Updated|Created)", t) or rec["posted"]
            rec["closing"] = find_date(r"(?:Closing date|Closing|Deadline|Apply by|Application deadline|Expires?)", t) or rec["closing"]
            mo = re.search(r"(?:Organi[sz]ation|Employer|Agency)\s*[:\-]?\s*([A-Z][^:|\n]{2,80}?)\s+(?:Location|Country|Duty|Posted|Closing|Grade|Level|Type)", t)
            ml = re.search(r"(?:Location|Duty station|City)\s*[:\-]?\s*([A-Z][^:|\n]{2,60}?)\s+(?:Organi|Posted|Closing|Grade|Contract|Level|Type|Deadline)", t)
            rec["org"] = mo.group(1).strip() if mo else rec["org"]
            rec["location"] = ml.group(1).strip() if ml else rec["location"]
            rec["grade"] = grade_of(t[:6000]) or rec["grade"]
            rec["eligibility"] = eligibility_of(t[:6000])
            rec["summary"] = t[:500]
            time.sleep(1)
        except Exception as e:
            print("unjobnet detail failed", rec["url"], e, file=sys.stderr)
    print("DEBUG unjobnet records", len(out), "with closing", sum(1 for r in out if r["closing"]), file=sys.stderr)
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
                        "url": url, "grade": grade_of(it.get("Title", "") + " " + (it.get("ShortDescriptionStr") or "") + " " + (it.get("ExternalQualificationsStr") or "")[:1500]),
                        "eligibility": eligibility_of(it.get("Title", "") + " " + re.sub(r"<[^>]+>", " ", (it.get("ShortDescriptionStr") or "") + " " + (it.get("ExternalQualificationsStr") or ""))[:3000]),
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
        items = r.json().get("data", {}).get("list", [])
        if items:
            print("DEBUG uncareers keys:", sorted(items[0].keys()), file=sys.stderr)
            print("DEBUG uncareers sample:", {k: items[0][k] for k in items[0] if k not in ("jobDescription",)}, file=sys.stderr)
        for it in items:
            title = txt(it.get("jobTitle"))
            loc = re.sub(r"^\d+\s+", "", txt(it.get("dutyStation")))
            loc = re.sub(r"\s+[0-9a-f]{8,}$", "", loc).title()
            out.append({"source": "UN Careers", "title": title, "org": txt(it.get("jn")) or "United Nations Secretariat",
                        "location": loc, "country": "",
                        "posted": norm_date(it.get("startDate")), "closing": norm_date(it.get("endDate")),
                        "url": f"https://careers.un.org/jobSearchDescription/{it.get('jobId')}?language=en",
                        "grade": grade_of(" ".join(txt(it.get(k)) for k in ("jobLevel", "jl", "level", "grade", "jobTitle"))) or txt(it.get("jc")),
                        "eligibility": eligibility_of(title + " " + txt(it.get("jobDescription") or "")[:2000]), "summary": txt(it.get("jf"))})
    except Exception as e:
        print("uncareers failed", e, file=sys.stderr)
    return out


def main():
    records = []
    for fn in (reliefweb, unjobs, unjobnet, ilo, undp, uncareers):
        try:
            got = fn()
            print(fn.__name__, len(got), file=sys.stderr)
            records.extend(got)
        except Exception as e:
            print(fn.__name__, "failed", e, file=sys.stderr)
    seen, kept = set(), []
    for r in records:
        for k in ("title", "org", "location", "country", "url", "summary", "eligibility"):
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
