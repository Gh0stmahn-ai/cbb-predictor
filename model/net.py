"""Official NCAA NET rankings (ncaa.com), matched to ESPN team ids."""
import re, difflib, io, urllib.request
import pandas as pd

URL = "https://www.ncaa.com/rankings/basketball-men/d1/ncaa-mens-basketball-net-rankings"
ABBR = [("st.", "state"), ("ky.", "kentucky"), ("miss.", "mississippi"), ("val.", "valley"), ("la.", "louisiana"),
        ("ga.", "georgia"), ("conn.", "connecticut"), ("col.", "college"), ("fla.", "florida"), ("ill.", "illinois"),
        ("mich.", "michigan"), ("ala.", "alabama"), ("ariz.", "arizona"), ("ark.", "arkansas"), ("caro.", "carolina"),
        ("tenn.", "tennessee"), ("tex.", "texas"), ("ind.", "indiana"), ("wash.", "washington"), ("colo.", "colorado"),
        ("okla.", "oklahoma"), ("so.", "southern"), ("int'l", "international"), ("u.", "university"), ("n.c.", "north carolina")]
ALIAS = {"miami (fl)": "miami", "southern california": "usc", "uni": "northern iowa", "sfa": "stephen f austin",
         "uncw": "unc wilmington", "utrgv": "ut rio grande valley", "col. of charleston": "charleston", "lmu (ca)": "loyola marymount",
         "northern ky.": "northern kentucky", "csun": "cal state northridge", "queens (nc)": "queens", "liu": "long island university",
         "uiw": "incarnate word", "central conn. st.": "central connecticut", "southeastern la.": "southeastern louisiana",
         "west ga.": "west georgia", "niu": "northern illinois", "army west point": "army", "alcorn": "alcorn state",
         "umes": "maryland eastern shore", "ulm": "ul monroe", "lindenwood": "lindenwood", "miami (oh)": "miami oh",
         "fiu": "florida international", "fgcu": "florida gulf coast", "etsu": "east tennessee state", "uic": "uic",
         "umkc": "kansas city", "app state": "app state", "ole miss": "ole miss", "uconn": "uconn", "lsu": "lsu",
         "nc state": "nc state", "vmi": "vmi", "byu": "byu", "tcu": "tcu", "smu": "smu", "ucf": "ucf", "unlv": "unlv",
         "utep": "utep", "utsa": "utsa", "siue": "siue", "njit": "njit", "iupui": "iu indianapolis", "iu indy": "iu indianapolis",
         "omaha": "omaha", "st. thomas (mn)": "st thomas minnesota", "saint mary's (ca)": "saint marys", "seattle u": "seattle u",
         "loyola chicago": "loyola chicago", "loyola maryland": "loyola maryland", "mississippi val.": "mississippi valley state",
         "western ky.": "western kentucky", "eastern ky.": "eastern kentucky", "southern miss.": "southern miss",
         "southern ind.": "southern indiana", "saint francis": "saint francis", "san francisco": "san francisco",
         "american": "american university", "lamar university": "lamar",
         "st. john's (ny)": "st. john's", "st. thomas (mn)": "st. thomas", "a&m-corpus christi": "texas a&m-corpus christi",
         "new orleans": "lsu new orleans", "southeast mo. st.": "southeast missouri state", "southern u.": "southern",
         "csu bakersfield": "cal state bakersfield", "southeastern la.": "se louisiana", "southern ind.": "southern indiana",
         "saint francis": "saint francis pa", "queens (nc)": "queens university"}


def norm(s):
    s = s.lower().replace("&", "and").replace("’", "").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def expand(s):
    s = s.lower()
    if s in ALIAS:
        return norm(ALIAS[s])
    for a, b in ABBR:
        s = re.sub(r"(?<![a-z])" + re.escape(a) + r"(?![a-z])", b, s)
    return norm(s)


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    t = pd.read_html(io.StringIO(html))[0]
    m = re.search(r"Through Games ([A-Za-z]+\.? \d{1,2} \d{4})", html)
    return t, (m.group(1) if m else None)


def match(net_table, espn_teams):
    """espn_teams: list of ESPN team dicts. Returns {espn_id: row dict} with no duplicate assignments."""
    cand = {}
    for t in espn_teams:
        for k in ("location", "displayName", "shortDisplayName", "abbreviation"):
            v = t.get(k)
            if v:
                cand.setdefault(norm(v), set()).add(int(t["id"]))
    cand = {k: next(iter(v)) for k, v in cand.items() if len(v) == 1}  # drop ambiguous keys
    out, used = {}, set()
    rows = net_table.to_dict("records")
    for r in rows:
        n = expand(str(r["School"]))
        tid = cand.get(n)
        if tid is None:
            c = difflib.get_close_matches(n, list(cand), n=2, cutoff=0.9)
            if len(c) == 1:
                tid = cand[c[0]]
        if tid is None or tid in used:
            continue
        used.add(tid)
        out[tid] = r
    return out
