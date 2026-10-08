#!/usr/bin/env python3
"""Broader sources for the job feed: company and foundation career boards (Workday,
Greenhouse, Lever), LinkedIn public listings, academic boards, IFI and think-tank
career pages. Each source is best-effort and logs what it found.

Postings on pages that print no date are dated by first sighting: seen.json maps a
URL to the day the collector first saw it, and that day becomes the posting date.
"""
import datetime as dt
import json
import os
import re
import sys
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
TODAY = dt.date.today()
SEEN_PATH = "seen.json"

CLOSE_LABEL = r"(?:Closing date|Closing Date|Deadline for applications?|Application deadline|Applications? close|Apply by|Apply before|Closes|Deadline|Closing|Date limite|締切|応募締切|Expires|Expiry date)"
POST_LABEL = r"(?:Posted on|Posted|Published on|Published|Date posted|Posting date|Date published|Placed on|Placed On|Opened|Open date|Start date of posting|掲載日)"
LOC_LABEL = r"(?:Location|Locations|Duty station|Duty Station|Based in|Office|Place of work|勤務地)"


TOPICAL = re.compile(r"(humanitarian|development|social|livelihood|employment|labour|labor|decent work|policy|partnership|sustainab|esg|human rights|resilien|crisis|disaster|emergency|refugee|displace|migration|protection|inclusion|grant|philanthrop|programme|program\b|programs|impact|advocacy|governance|economist|economic|research fellow|lecturer|professor|fellow|peace|conflict|fragil|recovery|resource mobili|fundrais|donor|foundation|country director|regional director|head of|chief of|deputy director|director|coordinator|safeguard|just transition|supply chain|due diligence|csr|responsib|climate|gender|youth|skills|enterprise|entrepreneur|cash|nexus|public works|infrastructure|international|global|asia|pacific|myanmar|thailand|africa|middle east|government affairs|public affairs|public policy|external relations|stakeholder|strategy|portfolio|operations director|managing director|executive director|chief executive|secretary general|国際|人道|開発|政策)", re.I)
NOISE = re.compile(r"\b(engineer|engineering|software|developer|data (?:scientist|engineer|analyst)|machine learning|AI |cyber|IT |ICT|devops|cloud|accountant|accounting|receivable|payable|bookkeep|nurse|nursing|clinical|biostat|epidemiolog|laborator|semiconductor|chemist|physic|pharma|sales|marketing|tax|audit|legal counsel|paralegal|intern\b|internship|trainee|graduate programme|apprentice|driver|cleaner|security guard|receptionist|secretary|executive assistant|administrative assistant|customer|retail|warehouse|logistics (?:officer|assistant)|mechanic|electrician|architect|surveyor|quantity|design(?:er)?\b|UX|product manager|brand|ecommerce|e-commerce|supply chain analyst|procurement (?:officer|assistant)|payroll|recruiter|talent acquisition|HR (?:officer|assistant|business)|people operations)\b", re.I)


def topical(rec):
    """Stricter relevance for the broad sources: the title must be on topic and not a technical or support role."""
    title = rec.get("title", "")
    if NOISE.search(title) and not re.search(r"(director|head of|chief|senior manager|lead)", title, re.I):
        return False
    return bool(TOPICAL.search(title))


def log(*a):
    print("DEBUG", *a, file=sys.stderr)


def fx():
    """Helpers from fetch_feed (imported lazily to avoid a circular import at load time)."""
    import fetch_feed
    return fetch_feed


def clean_ordinals(text):
    return re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", text)


def load_seen():
    try:
        return json.load(open(SEEN_PATH))
    except Exception:
        return {}


def save_seen(seen):
    cutoff = (TODAY - dt.timedelta(days=90)).isoformat()
    seen = {u: d for u, d in seen.items() if d >= cutoff}
    json.dump(seen, open(SEEN_PATH, "w"), indent=0, sort_keys=True)


def first_seen(seen, url):
    if url not in seen:
        seen[url] = TODAY.isoformat()
    return seen[url]


def get(url, **kw):
    kw.setdefault("timeout", 40)
    kw.setdefault("headers", UA)
    return requests.get(url, **kw)


def page_text(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


MDY_SOURCES = {"World Bank", "Workday: Gates Foundation", "Greenhouse: Human Rights Watch"}


def parse_detail(text, rec, seen):
    """Fill posted, closing, location, grade, eligibility and summary from a detail page's text."""
    f = fx()
    t = clean_ordinals(text)
    if rec.get("source") in MDY_SOURCES or "Eastern Time" in t:
        t = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", lambda m: f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}", t)
    rec["closing"] = rec.get("closing") or f.find_date(CLOSE_LABEL, t)
    rec["posted"] = rec.get("posted") or f.find_date(POST_LABEL, t)
    if not rec.get("location"):
        m = re.search(LOC_LABEL + r"\s*[:\-]\s*([A-Z][A-Za-z ,'()\-/]{2,60}?)(?=\s{2,}|\s+(?:[A-Z][a-z]+\s*:|Posted|Closing|Deadline|Apply|Job|Contract|Grade|Salary|Type)|$)", t)
        if m:
            rec["location"] = m.group(1).strip(" ,")
    if not rec.get("grade"):
        rec["grade"] = f.grade_of(t[:6000])
    rec["eligibility"] = rec.get("eligibility") or f.eligibility_of(t[:8000])
    m = re.search(r"(?:salary|remuneration|compensation|pay)\s*[:\-]?\s*([^.]{0,120}?(?:USD|US\$|\$|EUR|€|GBP|£|CHF|JPY|¥|THB)\s?[\d,]{3,}[^.]{0,60})", t, re.I)
    if m:
        rec["pay"] = re.sub(r"\s+", " ", m.group(1))[:160]
    m = re.search(r"(\d{1,2})\+?\s*(?:to\s*\d{1,2}\s*)?years?(?:'| of)?\s+(?:relevant |professional |progressively responsible |work |related )*experience", t, re.I)
    if m:
        rec["experience_years"] = int(m.group(1))
    if not rec.get("posted") and not rec.get("closing"):
        rec["posted"] = first_seen(seen, rec["url"])
        rec["dated_by"] = "first seen"
    start = t.find(rec["title"]) if rec.get("title") else -1
    rec["summary"] = (t[start:] if start >= 0 else t)[:500]
    return rec


def mk(source, title, url, org="", location="", country="", **extra):
    rec = {"source": source, "title": re.sub(r"\s+", " ", title or "").strip()[:160], "org": org, "location": location,
           "country": country, "posted": None, "closing": None, "url": url, "grade": None, "eligibility": "", "summary": ""}
    rec.update(extra)
    return rec


# ------------------------------------------------------------------ Workday
# (tenant, wd host number, site name, organisation label). Unverified entries are
# tried and logged; a 404 simply skips the board.
WORKDAY = [
    ("gatesfoundation", "wd1", "Gates", "Gates Foundation"),
    ("fordfoundation", "wd1", "FordFoundationCareerPage", "Ford Foundation"),
    ("osf", "wd5", "OSF", "Open Society Foundations"),
    ("rockefellerfoundation", "wd1", "RockefellerFoundation", "Rockefeller Foundation"),
    ("mastercardfdn", "wd3", "MastercardFoundation", "Mastercard Foundation"),
    ("worldvision", "wd1", "WorldVisionInternational", "World Vision International"),
    ("ifrc", "wd3", "IFRC", "IFRC"),
    ("care", "wd1", "CARE", "CARE"),
    ("wwf", "wd3", "WWF", "WWF"),
    ("wellcome", "wd3", "Wellcome", "Wellcome"),
    ("path", "wd1", "PATH", "PATH"),
    ("ihme", "wd1", "IHME", "IHME"),
    ("unilever", "wd3", "Unilever", "Unilever"),
    ("mckinsey", "wd1", "McKinsey", "McKinsey"),
    ("bcg", "wd1", "BCG", "BCG"),
    ("oecd", "wd3", "External", "OECD"),
    ("icrc", "wd3", "External", "ICRC"),
    ("adb", "wd3", "External", "Asian Development Bank"),
    ("ebrd", "wd3", "External", "EBRD"),
    ("savethechildren", "wd3", "External", "Save the Children"),
    ("mercycorps", "wd1", "External", "Mercy Corps"),
    ("oxfam", "wd3", "External", "Oxfam"),
    ("irc", "wd5", "External", "International Rescue Committee"),
    ("nrc", "wd3", "External", "Norwegian Refugee Council"),
    ("drc", "wd3", "External", "Danish Refugee Council"),
    ("plan", "wd3", "External", "Plan International"),
    ("unilever", "wd3", "External", "Unilever"),
    ("nestle", "wd3", "External", "Nestle"),
    ("hm", "wd3", "External", "H&M Group"),
    ("ikea", "wd3", "External", "IKEA"),
    ("ingka", "wd3", "External", "Ingka Group (IKEA)"),
    ("maersk", "wd3", "External", "Maersk"),
    ("novonordisk", "wd3", "External", "Novo Nordisk"),
    ("sony", "wd1", "External", "Sony"),
    ("toyota", "wd3", "External", "Toyota"),
    ("hitachi", "wd3", "External", "Hitachi"),
    ("standardchartered", "wd3", "External", "Standard Chartered"),
    ("hsbc", "wd3", "External", "HSBC"),
    ("thomsonreuters", "wd3", "External", "Thomson Reuters Foundation / Thomson Reuters"),
]


def discover_workday_sites(s, base):
    """Find a tenant's career site names from the redirect of the bare host and from its HTML."""
    names = []
    try:
        r = s.get(base + "/", timeout=30, allow_redirects=True)
        m = re.search(r"myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)", r.url)
        if m:
            names.append(m.group(1))
        for m in re.finditer(r"myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)", r.text):
            if m.group(1) not in names and m.group(1) not in ("wday", "en-US"):
                names.append(m.group(1))
    except Exception as e:
        log("workday discover failed", base, e)
    return names[:4]


def workday():
    out = []
    for tenant, wd, site, org in WORKDAY:
        base = f"https://{tenant}.{wd}.myworkdayjobs.com"
        try:
            s = requests.Session()
            s.headers.update(UA)
            r = None
            for cand in [site] + discover_workday_sites(s, base):
                api = f"{base}/wday/cxs/{tenant}/{cand}/jobs"
                s.get(f"{base}/{cand}", timeout=30)
                r = s.post(api, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}, timeout=40)
                if r.status_code == 200:
                    site = cand
                    break
                log("workday", tenant, cand, r.status_code)
            if r is None or r.status_code != 200:
                continue
            data = r.json()
            total = data.get("total", 0)
            jobs = list(data.get("jobPostings", []))
            offset = 20
            while offset < min(total, 200):
                r = s.post(api, json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""}, timeout=40)
                jobs.extend(r.json().get("jobPostings", []))
                offset += 20
            n = 0
            for j in jobs:
                title = j.get("title", "")
                path = j.get("externalPath", "")
                posted = j.get("postedOn", "")
                m = re.search(r"(\d+)\+? days? ago", posted, re.I)
                if m:
                    pd = (TODAY - dt.timedelta(days=int(m.group(1)))).isoformat()
                elif re.search(r"today|yesterday", posted, re.I):
                    pd = (TODAY - dt.timedelta(days=1 if "yesterday" in posted.lower() else 0)).isoformat()
                else:
                    pd = fx().norm_date(posted)
                url = f"{base}/{site}{path}" if path.startswith("/") else path
                rec = mk("Workday: " + org, title, url, org=org, location=j.get("locationsText", ""), posted=pd,
                         summary=(j.get("bulletFields") or [""])[0])
                out.append(rec)
                n += 1
            log("workday", tenant, "jobs", n, "of", total)
        except Exception as e:
            log("workday", tenant, "failed", e)
    return out


# ------------------------------------------------------------------ Greenhouse and Lever
GREENHOUSE = [("rockefellerfoundation", "Rockefeller Foundation"), ("acumen", "Acumen"), ("ashoka", "Ashoka"),
              ("mercycorps", "Mercy Corps"), ("tent", "Tent Partnership for Refugees"), ("givedirectly", "GiveDirectly"),
              ("idinsight", "IDinsight"), ("openphilanthropy", "Open Philanthropy"), ("evidenceaction", "Evidence Action"),
              ("clintonhealthaccessinitiative", "Clinton Health Access Initiative"), ("ideo", "IDEO"), ("dalberg", "Dalberg"),
              ("brac", "BRAC"), ("oneacrefund", "One Acre Fund"), ("precisiondevelopment", "Precision Development"),
              ("innovationsforpovertyaction", "Innovations for Poverty Action"), ("thelifeyoucansave", "The Life You Can Save"),
              ("globalwitness", "Global Witness"), ("humanrightswatch", "Human Rights Watch"), ("crisisgroup", "International Crisis Group")]
LEVER = [("acumen", "Acumen"), ("ashoka", "Ashoka"), ("givedirectly", "GiveDirectly"), ("idinsight", "IDinsight"),
         ("dalberg", "Dalberg"), ("oneacrefund", "One Acre Fund"), ("brac", "BRAC"), ("poverty-action", "Innovations for Poverty Action"),
         ("crisisgroup", "International Crisis Group"), ("mercycorps", "Mercy Corps"), ("ycombinator", "Y Combinator")]


def greenhouse():
    out = []
    for slug, org in GREENHOUSE:
        try:
            r = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
            if r.status_code != 200:
                log("greenhouse", slug, r.status_code)
                continue
            jobs = r.json().get("jobs", [])
            for j in jobs:
                content = re.sub(r"<[^>]+>", " ", j.get("content", "") or "")
                content = re.sub(r"&[a-z]+;|&#\d+;", " ", content)
                rec = mk("Greenhouse: " + org, j.get("title", ""), j.get("absolute_url", ""), org=org,
                         location=(j.get("location") or {}).get("name", ""),
                         posted=fx().norm_date((j.get("updated_at") or j.get("first_published") or "")[:10]),
                         summary=re.sub(r"\s+", " ", content)[:500])
                m = re.search(r"(\d{1,2})\+?\s*years?", content, re.I)
                if m:
                    rec["experience_years"] = int(m.group(1))
                out.append(rec)
            log("greenhouse", slug, "jobs", len(jobs))
        except Exception as e:
            log("greenhouse", slug, "failed", e)
    return out


def lever():
    out = []
    for slug, org in LEVER:
        try:
            r = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            if r.status_code != 200:
                log("lever", slug, r.status_code)
                continue
            jobs = r.json()
            for j in jobs:
                created = j.get("createdAt")
                pd = dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc).date().isoformat() if created else None
                cats = j.get("categories") or {}
                rec = mk("Lever: " + org, j.get("text", ""), j.get("hostedUrl", ""), org=org,
                         location=cats.get("location", ""), posted=pd,
                         summary=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", j.get("descriptionPlain", "") or j.get("description", "")))[:500])
                out.append(rec)
            log("lever", slug, "jobs", len(jobs))
        except Exception as e:
            log("lever", slug, "failed", e)
    return out


# ------------------------------------------------------------------ LinkedIn public listings
LINKEDIN_QUERIES = [
    ("humanitarian director", "Bangkok, Thailand"), ("social protection", "Bangkok, Thailand"),
    ("sustainability partnerships director", "Bangkok, Thailand"), ("development policy", "Geneva, Switzerland"),
    ("humanitarian partnerships", "Switzerland"), ("ESG human rights supply chain", "Singapore"),
    ("resilience livelihoods program director", "Asia"), ("corporate social responsibility director", "Tokyo, Japan"),
    ("international development senior manager", "London, United Kingdom"), ("social impact director", "Paris, France"),
    ("labour rights", "Europe"), ("foundation program director", "Europe"), ("disaster risk", "Asia"),
    ("economic inclusion refugees", "Europe"), ("policy advisor crisis", "Brussels, Belgium"),
]


def linkedin():
    out = []
    total = 0
    for kw, loc in LINKEDIN_QUERIES:
        try:
            r = get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                    params={"keywords": kw, "location": loc, "f_E": "4,5,6", "f_TPR": "r2592000", "start": 0}, timeout=40)
            if r.status_code != 200:
                log("linkedin", kw, loc, r.status_code)
                continue
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("li")
            n = 0
            for c in cards:
                a = c.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
                if not a:
                    continue
                title = (c.select_one(".base-search-card__title") or a).get_text(" ", strip=True)
                org = (c.select_one(".base-search-card__subtitle") or c.select_one("h4"))
                org = org.get_text(" ", strip=True) if org else ""
                location = c.select_one(".job-search-card__location")
                location = location.get_text(" ", strip=True) if location else loc
                t = c.select_one("time")
                posted = fx().norm_date(t.get("datetime")) if t and t.get("datetime") else None
                url = a.get("href", "").split("?")[0]
                rec = mk("LinkedIn", title, url, org=org, location=location, posted=posted, summary=f"{title} at {org}, {location}. Found by LinkedIn search for '{kw}' in {loc}.")
                out.append(rec)
                n += 1
            total += n
            log("linkedin", kw, "|", loc, "cards", n)
            time.sleep(2)
        except Exception as e:
            log("linkedin", kw, "failed", e)
    log("linkedin total", total)
    return out


# ------------------------------------------------------------------ generic page readers (HTML or headless browser)
# name, organisation label, listing URLs, regex a job link must match, use the browser, max detail pages
GENERIC = [
    ("jobs.ac.uk", "", ["https://www.jobs.ac.uk/search/?keywords=international+development&sort=re",
                        "https://www.jobs.ac.uk/search/?keywords=humanitarian&sort=re",
                        "https://www.jobs.ac.uk/search/?keywords=labour+employment+policy&sort=re",
                        "https://www.jobs.ac.uk/search/?keywords=migration+displacement&sort=re",
                        "https://www.jobs.ac.uk/search/?keywords=social+protection&sort=re"], r"jobs\.ac\.uk/job/", False, 40),
    ("EURAXESS", "", ["https://euraxess.ec.europa.eu/jobs/search?keywords=development%20policy",
                      "https://euraxess.ec.europa.eu/jobs/search?keywords=humanitarian",
                      "https://euraxess.ec.europa.eu/jobs/search?keywords=labour%20economics",
                      "https://euraxess.ec.europa.eu/jobs/search?keywords=migration"], r"euraxess\.ec\.europa\.eu/jobs/\d+", True, 30),
    ("AcademicPositions", "", ["https://academicpositions.com/find-jobs?keywords=development+studies",
                               "https://academicpositions.com/find-jobs?keywords=international+relations",
                               "https://academicpositions.com/find-jobs?keywords=labour"], r"academicpositions\.com/ad/", True, 25),
    ("AIIB", "Asian Infrastructure Investment Bank", ["https://www.aiib.org/en/opportunities/career/job-vacancy/index.html"], r"/job-vacancy/.+\.html", True, 25),
    ("World Bank", "World Bank Group", ["https://worldbankgroup.csod.com/ux/ats/careersite/1/home?c=worldbankgroup"], r"/requisition/", True, 30),
    ("ADB", "Asian Development Bank", ["https://www.adb.org/work-with-us/careers/current-opportunities", "https://www.adb.org/work-with-us/careers/job-opportunities"], r"adb\.org/(?:work-with-us/careers/|.*job)", True, 25),
    ("EBRD", "EBRD", ["https://www.ebrd.com/home/careers/job-opportunities.html", "https://jobs.ebrd.com/search/"], r"jobs\.ebrd\.com/job/|/careers/.+\.html", True, 20),
    ("IsDB", "Islamic Development Bank", ["https://www.isdb.org/careers", "https://careers.isdb.org/"], r"careers\.isdb\.org/|isdb\.org/careers/", True, 20),
    ("OECD", "OECD", ["https://oecd.taleo.net/careersection/ext/joblist.ftl"], r"jobdetail\.ftl\?job=", True, 25),
    ("ICRC", "ICRC", ["https://careers.icrc.org/search/?q=&sortColumn=referencedate&sortDirection=desc"], r"careers\.icrc\.org/job/", False, 30),
    ("IFRC", "IFRC", ["https://www.ifrc.org/jobs"], r"ifrc\.org/jobs/|myworkdayjobs|ifrc\.org/.+job", True, 20),
    ("Impactpool", "", ["https://www.impactpool.org/search?q=humanitarian", "https://www.impactpool.org/search?q=social+protection",
                        "https://www.impactpool.org/search?q=livelihoods", "https://www.impactpool.org/search?q=resource+mobilization",
                        "https://www.impactpool.org/search?q=employment"], r"impactpool\.org/jobs/\d+", True, 40),
    ("Devex", "", ["https://www.devex.com/jobs/search?filter%5Bkeywords%5D=humanitarian", "https://www.devex.com/jobs/search?filter%5Bkeywords%5D=social+protection",
                   "https://www.devex.com/jobs/search?filter%5Bkeywords%5D=livelihoods", "https://www.devex.com/jobs/search?filter%5Bkeywords%5D=labour"], r"devex\.com/jobs/[a-z0-9-]+-\d+", True, 30),
    ("Bond", "", ["https://www.bond.org.uk/jobs/"], r"bond\.org\.uk/jobs/[a-z0-9-]+/?$", True, 25),
    ("CharityJob", "", ["https://www.charityjob.co.uk/jobs/international-development?keywords=humanitarian",
                        "https://www.charityjob.co.uk/jobs?keywords=head+of+programmes+international"], r"charityjob\.co\.uk/jobs/[a-z0-9-]+/\d+", True, 25),
    ("ODI", "ODI", ["https://odi.org/en/about/jobs/"], r"odi\.org/en/about/jobs/.+", True, 10),
    ("IDS", "Institute of Development Studies", ["https://www.ids.ac.uk/jobs/"], r"ids\.ac\.uk/jobs/.+", True, 10),
    ("Chatham House", "Chatham House", ["https://www.chathamhouse.org/about-us/jobs"], r"chathamhouse\.org/about-us/jobs/.+", True, 10),
    ("SEI", "Stockholm Environment Institute", ["https://www.sei.org/about/careers/"], r"sei\.org/about/careers/.+|sei\.org/jobs/.+", True, 10),
    ("ERIA", "ERIA", ["https://www.eria.org/about-eria/careers/"], r"eria\.org/.*(?:career|vacanc|job).+", True, 10),
    ("GGGI", "Global Green Growth Institute", ["https://gggi.org/careers/", "https://gggi.org/jobs/"], r"gggi\.org/(?:jobs?|careers?)/.+", True, 15),
    ("ADPC", "Asian Disaster Preparedness Center", ["https://www.adpc.net/igo/category/ID1/Job-Vacancies"], r"adpc\.net/.*(?:Job|vacanc).+", True, 10),
    ("IDLO", "IDLO", ["https://www.idlo.int/jobs"], r"idlo\.int/jobs/.+", True, 10),
    ("Tent Partnership", "Tent Partnership for Refugees", ["https://www.tent.org/careers/"], r"tent\.org/careers/.+|greenhouse|lever", True, 10),
    ("Aga Khan Development Network", "AKDN", ["https://www.akdn.org/careers", "https://the.akdn/en/how-we-work/careers"], r"akdn.*(?:job|vacanc|career/).+", True, 15),
    ("IRC", "International Rescue Committee", ["https://careers.rescue.org/us/en/search-results?keywords=director", "https://rescue.csod.com/ux/ats/careersite/1/home?c=rescue"], r"careers\.rescue\.org/.+/job/|/requisition/", True, 20),
    ("Save the Children", "Save the Children International", ["https://www.savethechildren.net/careers/apply", "https://stcuk.taleo.net/careersection/ex/joblist.ftl"], r"jobdetail\.ftl\?job=|savethechildren\.net/careers/.+", True, 20),
    ("Mercy Corps", "Mercy Corps", ["https://jobs.jobvite.com/mercycorps/search?q=&l="], r"jobvite\.com/mercycorps/job/", True, 20),
    ("Oxfam", "Oxfam", ["https://jobs.oxfam.org.uk/jobs/", "https://www.oxfam.org/en/jobs"], r"jobs\.oxfam\.org\.uk/vacancy/|oxfam\.org/en/jobs/.+", True, 15),
    ("Action Against Hunger", "Action Against Hunger", ["https://www.actionagainsthunger.org/about/careers/", "https://www.actioncontrelafaim.org/en/join-us/job-offers/"], r"(?:job|offer|vacanc).{3,}", True, 10),
    ("JICA PARTNER", "", ["https://partner.jica.go.jp/RecruitSearch?eventType=jobTypeNameSearch"], r"partner\.jica\.go\.jp/RecruitDetail", True, 15),
    ("MOFA IRC", "", ["https://www.mofa-irc.go.jp/jinji/kuseki.html", "https://www.mofa-irc.go.jp/"], r"mofa-irc\.go\.jp/.*(?:kuseki|vacanc|jinji/)\S+\.html", True, 15),
    ("cinfo", "", ["https://www.cinfo.ch/en/cinfoposte?search=humanitarian", "https://www.cinfo.ch/en/cinfoposte"], r"cinfo\.ch/en/cinfoposte/.+", True, 15),
    ("DevNetJobs", "", ["https://www.devnetjobs.org/", "https://www.devnetjobs.org/jobs_list.php"], r"devnetjobs\.org/jobs?[_/].+", True, 20),
    ("GlobalJobs", "", ["https://www.globaljobs.org/jobs?keywords=humanitarian"], r"globaljobs\.org/jobs/\d+", True, 15),
    ("Idealist", "", ["https://www.idealist.org/en/jobs?q=humanitarian%20director", "https://www.idealist.org/en/jobs?q=social%20protection"], r"idealist\.org/en/(?:nonprofit-job|job)/", True, 20),
    ("THE unijobs", "", ["https://www.timeshighereducation.com/unijobs/listings/international-development/", "https://www.timeshighereducation.com/unijobs/listings/?Keywords=migration"], r"unijobs/listings/.+/\d+", True, 20),
    ("Perrett Laver", "Perrett Laver (executive search)", ["https://candidates.perrettlaver.com/vacancies/"], r"perrettlaver\.com/vacancies/.+", True, 15),
    ("Oxford HR", "Oxford HR (executive search)", ["https://oxfordhr.com/jobs/"], r"oxfordhr\.com/jobs/.+", True, 15),
    ("SRI Executive", "SRI Executive (executive search)", ["https://www.sri-executive.com/vacancies/", "https://www.sri-executive.com/current-opportunities/"], r"sri-executive\.com/(?:vacanc|opportunit).+/.+", True, 15),
    ("Mission Talent", "Mission Talent (executive search)", ["https://missiontalent.com/jobs/"], r"missiontalent\.com/jobs?/.+", True, 15),
]


def collect_links(html_or_page, base, link_re, browser_page=None):
    """Return [(href, text)] for anchors matching link_re, from raw HTML or a Playwright page."""
    links = []
    rx = re.compile(link_re, re.I)
    if browser_page is not None:
        for a in browser_page.query_selector_all("a[href]"):
            try:
                href = a.get_attribute("href") or ""
                text = a.inner_text().strip()
            except Exception:
                continue
            links.append((href, text))
    else:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_or_page, "html.parser")
        for a in soup.select("a[href]"):
            links.append((a.get("href", ""), a.get_text(" ", strip=True)))
    out, seen = [], set()
    from urllib.parse import urljoin
    for href, text in links:
        full = urljoin(base, href)
        if not rx.search(full) or full in seen or full.rstrip("/") == base.rstrip("/"):
            continue
        if not text or len(text) < 6 or re.fullmatch(r"(read more|apply|view|details|more|learn more|apply now)", text, re.I):
            continue
        seen.add(full)
        out.append((full, text))
    return out


def generic(seen):
    out = []
    f = fx()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        log("generic: playwright unavailable", e)
        sync_playwright = None
    pw = sync_playwright().start() if sync_playwright else None
    browser = pw.chromium.launch(headless=True) if pw else None
    ctx = browser.new_context(user_agent=UA["User-Agent"], viewport={"width": 1280, "height": 900}, locale="en-US") if browser else None
    page = None
    for name, org, urls, link_re, use_browser, max_details in GENERIC:
        links = []
        if ctx is not None:
            try:
                if page is not None:
                    page.close()
            except Exception:
                pass
            page = ctx.new_page()
        for u in urls:
            try:
                if use_browser and page is not None:
                    try:
                        page.goto(u, wait_until="domcontentloaded", timeout=60000)
                    except Exception as e:
                        if "interrupted by another navigation" not in str(e):
                            raise
                    page.wait_for_timeout(4000)
                    got = collect_links(None, u, link_re, browser_page=page)
                    if not got:
                        hrefs = []
                        for a in page.query_selector_all("a[href]")[:400]:
                            h = a.get_attribute("href") or ""
                            if re.search(r"job|vacanc|career|position|opportunit|recruit", h, re.I):
                                hrefs.append(h)
                        log("generic", name, "no links; title", repr(page.title())[:80], "url", page.url[:100], "sample", hrefs[:6])
                    links += got
                else:
                    r = get(u)
                    if r.status_code != 200:
                        log("generic", name, "listing", u, r.status_code)
                        continue
                    links += collect_links(r.text, u, link_re)
            except Exception as e:
                log("generic", name, "listing failed", u, str(e)[:120])
        uniq, seen_u = [], set()
        for href, text in links:
            if href not in seen_u:
                seen_u.add(href)
                uniq.append((href, text))
        log("generic", name, "links", len(uniq), (uniq[0][1][:50] if uniq else ""))
        recs = []
        for href, text in uniq:
            rec = mk(name, text, href, org=org)
            if not f.relevant(rec) or not topical(rec):
                continue
            recs.append(rec)
        fetched = 0
        for rec in recs:
            if fetched >= max_details:
                break
            try:
                if use_browser and page is not None:
                    page.goto(rec["url"], wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2000)
                    t = re.sub(r"\s+", " ", page.inner_text("body"))
                    if not rec["org"]:
                        tt = page.title()
                        rec["org"] = (tt.split(" - ")[-1] if " - " in tt else tt.split(" | ")[-1] if " | " in tt else "")[:80]
                else:
                    r = get(rec["url"])
                    if r.status_code != 200:
                        continue
                    t = page_text(r.text)
                parse_detail(t, rec, seen)
                fetched += 1
            except Exception as e:
                log("generic", name, "detail failed", rec["url"][:80], str(e)[:80])
        kept = [r for r in recs if r.get("posted") or r.get("closing")]
        log("generic", name, "relevant", len(recs), "detailed", fetched, "dated", len(kept))
        out.extend(kept)
    if browser:
        browser.close()
    if pw:
        pw.stop()
    return out


def all_extra():
    seen = load_seen()
    out = []
    for fn in (workday, greenhouse, lever, linkedin):
        try:
            got = fn()
            log(fn.__name__, "records", len(got))
            out.extend(got)
        except Exception as e:
            log(fn.__name__, "failed", e)
    try:
        got = generic(seen)
        log("generic records", len(got))
        out.extend(got)
    except Exception as e:
        log("generic failed", e)
    before = len(out)
    out = [r for r in out if topical(r)]
    log("topical filter kept", len(out), "of", before)
    for rec in out:
        if not rec.get("posted") and not rec.get("closing"):
            rec["posted"] = first_seen(seen, rec["url"])
            rec["dated_by"] = "first seen"
    save_seen(seen)
    return out
