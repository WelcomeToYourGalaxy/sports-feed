#!/usr/bin/env python3
"""
harvest_sports.py — the sports wire: who takes the money, who carries the cost,
and who is governing any of it.

The Sports Industry section is short, and its core claim is specific: up to one
in 42 student-athletes have been approached to fix or throw matches — roughly
one per one and a half teams, and only counting those who admitted it — with
many billions at stake. Fixing and the betting money behind it lead here
accordingly, followed by the governing bodies, doping, and the rest of the same
question.

The animal racing and rodeo material comes from a different part of the page,
the animal-industries section, which labels it "Animal Racing and Other Animal
Sports". It is carried here as one subject because it is the same activity: bred
and raced for sport and profit, injured and killed, and culled when
uncompetitive. The About panel says where it came from.

This is a feed on the industry, not on the games. Results, fixtures, transfers,
previews, player ratings and medal tables are refused — that is nearly all
sports coverage, and none of it is the subject.

    python3 harvest_sports.py
    python3 harvest_sports.py --dry-run
"""

import argparse
import gzip
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(HERE, "sources_sports.json")
OUT_PATH = os.path.join(HERE, "wire_sports.json")

RETAIN_DAYS = 45
MAX_ITEMS = 1200
WORKERS = 10         # a few hundred wires now
NOTABLE_SCORE = 3       # at or above this a story is marked as consequential

# --------------------------------------------------------------------------
# Plumbing: fetching, feed parsing, word-edge matching, fingerprints.
# --------------------------------------------------------------------------
USER_AGENT = ("Mozilla/5.0 (compatible; sports-feed/1.0; "
              "+https://github.com/WelcomeToYourGalaxy/space-life-news)")

TIMEOUT = 25

SNIPPET_CHARS = 240

TAG_RE = re.compile(r"<[^>]+>")

WS_RE = re.compile(r"\s+")

PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

def build_gnews_url(loc):
    q = loc["query"] + " when:30d"
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) +
            "&hl=" + loc["hl"] + "&gl=" + loc["gl"] + "&ceid=" + loc["ceid"])

def fetch(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                "Accept-Encoding": "gzip",
                "Accept-Language": "*",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw
        except Exception as exc:                       # noqa: BLE001 — report, don't crash the run
            last = exc
            time.sleep(1.5 * (attempt + 1))
    print("  ! unreachable: %s (%s)" % (url[:90], last), file=sys.stderr)
    return None

def strip_ns(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag

def text_of(el):
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", el.text or ""))).strip() if el is not None else ""

def child(node, *names):
    for kid in node:
        if strip_ns(kid.tag) in names:
            return kid
    return None

def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None



_YT_ID = re.compile(r"\s*\([A-Za-z0-9_-]{9,14}\)\s*$")
_HASHTAG = re.compile(r"#\S+")

def _is_video_result(title):
    """Google News mixes YouTube results in among the articles, and marks them
    by pasting a search query and the video id onto the end of the video title:

        <unrelated Japanese vlog title> Food Recall Salmonella Milk (JClqFGBDvh)

    The pasted query is not always the source's own query, so stripping words
    cannot reliably clear it — a football clip still arrives carrying "Food
    Recall Salmonella". The bracketed 9-14 character id is the dependable
    marker: no real headline ends that way. Hashtag pileups are the same class
    of upload-description noise.
    """
    if not title:
        return False
    if _YT_ID.search(title):
        return True
    return len(_HASHTAG.findall(title)) >= 3


# Stripping the pasted query word-by-word was tried and removed: real
# headlines end on query words all the time — "Regulator bans ad over
# misleading carbon neutral claim" lost four real words that way, and the
# shortened copy then failed to dedupe against the original. The video id
# is the only reliable marker, so detection rests on that alone.

def parse_feed(raw, src):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # Some publishers serve a stray byte before the declaration.
        try:
            root = ET.fromstring(raw[raw.index(b"<"):])
        except Exception:  # noqa: BLE001
            return []

    nodes = [n for n in root.iter() if strip_ns(n.tag) == "item"]
    atom = False
    if not nodes:
        nodes = [n for n in root.iter() if strip_ns(n.tag) == "entry"]
        atom = True

    out = []
    for n in nodes:
        title = text_of(child(n, "title"))
        if atom:
            link = ""
            for kid in n:
                if strip_ns(kid.tag) == "link" and kid.get("rel", "alternate") == "alternate":
                    link = kid.get("href", "")
                    break
        else:
            link_el = child(n, "link")
            link = (link_el.text or "").strip() if link_el is not None else ""
            if not link:
                link = text_of(child(n, "guid"))
        if not title or not link:
            continue

        outlet_el = child(n, "source")
        outlet = text_of(outlet_el) if outlet_el is not None else ""
        if outlet and title.endswith(" - " + outlet):
            title = title[: -(len(outlet) + 3)].strip()
        elif not outlet and src["name"].startswith("Google News") and " - " in title:
            # Google News appends the outlet to the headline when it omits <source>.
            head, _, tail = title.rpartition(" - ")
            if head and 2 <= len(tail) <= 45:
                title, outlet = head.strip(), tail.strip()

        stamp = parse_date(text_of(child(n, "pubDate", "published", "updated", "date")))
        snippet = text_of(child(n, "description", "summary", "content"))[:SNIPPET_CHARS]

        # Google News descriptions are usually the headline with the publisher's
        # name tacked on the end. That name is not part of the story, and it was
        # being read as geography: "The Guardian Nigeria News" placed a piece
        # about UK social mobility in Nigeria. Strip the publisher before the
        # text is ever classified or placed.
        for tail in (outlet, src["name"].replace("Google News \u00b7 ", "")):
            if tail and len(tail) > 3 and snippet.endswith(tail):
                snippet = snippet[: -len(tail)].strip(" -\u2013\u2014\u00b7|,")

        # Google News surfaces YouTube videos with the search query pasted onto
        # the end of the video title, followed by the video id in brackets:
        #   "<unrelated Japanese vlog title> Food Recall Salmonella Milk (JClqFGBDvh)"
        # Left alone, the query terms are read as if the story were about them,
        # so a football clip arrives filed under a food recall. Strip the id and
        # then any trailing run of words drawn from this source's own query.
        if _is_video_result(title):
            continue
        title = _YT_ID.sub("", title)
        snippet = _YT_ID.sub("", snippet)
        if not title.strip():
            continue

        out.append({
            "t": title,
            "u": link,
            "o": outlet or src["name"].replace("Google News · ", ""),
            "g": src["lang"],
            "r": src["region"],
            "k": src.get("kind", "news"),
            "d": stamp,
            "s": snippet,
            "w": src["name"],
        })
    return out

def _compile(term):
    if any(ord(ch) > 0x24F for ch in term):        # non-Latin script
        # substring matching is already prefix-like in scripts without word
        # breaks, so a trailing * is a no-op — strip it rather than search for
        # a literal asterisk, which is what used to happen.
        return term[:-1] if term.endswith("*") else term
    if term.endswith("*"):
        return re.compile(r"(?<![a-z0-9])" + re.escape(term[:-1]) + r"[a-z0-9\-]*", re.I)
    # A plain term also matches its simple plural. Without this, "polling
    # station" misses "polling stations" and "voter roll" misses "voter rolls",
    # which is how most headlines actually write them — the term looks present
    # to a reader and is invisible to the matcher. Use a trailing * for a real
    # prefix match; this only adds the regular plural.
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?:es|s)?(?![a-z0-9])", re.I)

def _compile_all(terms):
    return [_compile(t) for t in terms]

def hit(text, compiled):
    """True when any compiled term matches."""
    for c in compiled:
        if isinstance(c, str):
            if c in text:
                return True
        elif c.search(text):
            return True
    return False

def fingerprint(title):
    norm = PUNCT_RE.sub(" ", title.lower())
    return " ".join(WS_RE.sub(" ", norm).strip().split()[:9])

def canon_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query)
        query = [(k, v) for k, v in query if not k.lower().startswith("utm_")]
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"),
                                        urllib.parse.urlencode(query), ""))
    except Exception:  # noqa: BLE001
        return url


# --------------------------------------------------------------------------
# Where the story is, in three levels: region, subregion, place. A story
# naming a place files under the subregion and region above it, so the page
# can open a region and drill into it.
# --------------------------------------------------------------------------
# region → subregion → country, with the terms that match each country.
# Matching a country implies its subregion and its region, so a story naming
# Peru files under Peru, South America and Latin America at once.
GEO3 = [
 ("africa", "Africa", [
   ("africa-e", "East Africa", [
     ("ke","Kenya",["kenya","kenyan","nairobi","ogiek","maasai","samburu","turkana"]),
     ("tz","Tanzania",["tanzania","tanzanian","ngorongoro","hadza","serengeti"]),
     ("ug","Uganda",["uganda","ugandan","batwa uganda","karamoja"]),
     ("et","Ethiopia",["ethiopia","ethiopian","omo valley","oromia"]),
     ("so","Somalia",["somalia","somali","somaliland"]),
     ("rw","Rwanda",["rwanda","rwandan"]),
     ("bi","Burundi",["burundi"]),
     ("sd","Sudan",["sudan","sudanese","darfur"]),
     ("ss","South Sudan",["south sudan","dinka","nuer"]),
     ("mg","Madagascar",["madagascar","malagasy"]),
     ("mz","Mozambique",["mozambique","cabo delgado"]),
     ("zm","Zambia",["zambia","zambian"]),
     ("zw","Zimbabwe",["zimbabwe","zimbabwean"]),
     ("mw","Malawi",["malawi"]),
   ]),
   ("africa-w", "West Africa", [
     ("ng","Nigeria",["nigeria","nigerian","ogoni","niger delta","ijaw","nafdac"]),
     ("gh","Ghana",["ghana","ghanaian"]),
     ("ci","Côte d'Ivoire",["côte d'ivoire","ivory coast","ivorian"]),
     ("sn","Senegal",["senegal","senegalese","casamance"]),
     ("ml","Mali",["mali","malian","bamako","tuareg"]),
     ("bf","Burkina Faso",["burkina faso"]),
     ("ne","Niger",["niger republic","nigerien"]),
     ("lr","Liberia",["liberia","liberian"]),
     ("sl","Sierra Leone",["sierra leone"]),
     ("gn","Guinea",["guinea conakry","guinean"]),
     ("cm","Cameroon",["cameroon","cameroonian","baka"]),
   ]),
   ("africa-c", "Central Africa", [
     ("cd","DR Congo",["democratic republic of congo","drc","congolese","kivu","batwa"]),
     ("cg","Congo-Brazzaville",["republic of congo","brazzaville"]),
     ("ga","Gabon",["gabon","gabonese"]),
     ("cf","Central African Republic",["central african republic"]),
     ("td","Chad",["chad","chadian"]),
   ]),
   ("africa-s", "Southern Africa", [
     ("za","South Africa",["south africa","south african","khoisan","khoi","xolobeni","sahpra"]),
     ("bw","Botswana",["botswana","san people","central kalahari"]),
     ("na","Namibia",["namibia","namibian","himba","ovahimba"]),
     ("ao","Angola",["angola","angolan"]),
     ("ls","Lesotho",["lesotho"]),
   ]),
   ("africa-n", "North Africa", [
     ("ma","Morocco",["morocco","moroccan","amazigh","berber","western sahara","sahrawi"]),
     ("dz","Algeria",["algeria","algerian","kabyle"]),
     ("tn","Tunisia",["tunisia"]),
     ("ly","Libya",["libya","libyan","tuareg libya"]),
     ("eg","Egypt",["egypt","egyptian","nubian"]),
   ]),
 ]),
 ("americas-n", "North America", [
   ("na-us", "United States", [
     ("us","United States",["united states", "u.s.", "usa", "american", "washington dc", "fda", "usda", "cdc", "epa", "ftc", "cms", "nih", "osha", "fsis", "congress", "white house", "alabama", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida", "georgia", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "west virginia", "wisconsin", "wyoming"]),
     ("us-ak","Alaska",["alaska","alaskan","inupiat","yupik","gwich'in"]),
     ("us-sw","US Southwest",["navajo","diné","hopi","apache","arizona tribe","new mexico pueblo","tohono o'odham"]),
     ("us-pl","US Plains & Midwest",["standing rock","lakota","dakota access","oglala","cheyenne river","ojibwe","anishinaabe"]),
     ("us-pnw","US Pacific Northwest",["yakama","nez perce","puyallup","lummi","columbia river treaty","klamath"]),
     ("us-e","US East & South",["cherokee","seminole","lumbee","penobscot","wampanoag","mashpee"]),
     ("us-hi","Hawai'i",["native hawaiian","kanaka maoli","mauna kea","hawaii"]),
   ]),
   ("na-ca", "Canada", [
     ("ca","Canada",["canada", "canadian", "health canada", "cfia", "ottawa", "toronto", "montreal", "vancouver", "ontario", "alberta", "manitoba", "saskatchewan", "nova scotia", "new brunswick", "newfoundland"]),
     ("ca-bc","British Columbia",["british columbia","wet'suwet'en","haida","coastal gitxsan","secwepemc"]),
     ("ca-pr","Prairies",["alberta","saskatchewan","manitoba","treaty 8","treaty 6"]),
     ("ca-on","Ontario & Quebec",["ontario first nation","quebec","grassy narrows","innu","cree quebec","atikamekw"]),
     ("ca-n","Northern Canada",["nunavut","northwest territories","yukon","inuit nunangat","dene"]),
     ("ca-at","Atlantic Canada",["mi'kmaq","nova scotia","new brunswick","newfoundland","innu labrador"]),
   ]),
   ("na-mx", "Mexico", [
     ("mx","Mexico",["mexico", "mexican", "cofepris", "mexico city"]),
     ("mx-s","Southern Mexico",["chiapas","oaxaca","zapatista","zapoteco","mixe","tren maya","yucatán","maya"]),
     ("mx-n","Northern Mexico",["yaqui","rarámuri","tarahumara","sonora","chihuahua"]),
   ]),
 ]),
 ("americas-s", "Latin America & Caribbean", [
   ("la-amz", "Amazon Basin", [
     ("br-amz","Brazilian Amazon",["yanomami","munduruku","kayapó","xingu","terra indígena","amazônia","rondônia","pará"]),
     ("pe-amz","Peruvian Amazon",["loreto","ucayali","madre de dios","awajún","shipibo","kakataibo"]),
     ("co-amz","Colombian Amazon",["amazonas colombia","putumayo","caquetá"]),
     ("ec-amz","Ecuadorian Amazon",["yasuní","waorani","sarayaku","sucumbíos","achuar"]),
     ("bo-amz","Bolivian Amazon",["tipnis","beni","chiquitano","bolivian amazon"]),
     ("ve-amz","Venezuelan Amazon",["arco minero","amazonas venezuela","pemón"]),
   ]),
   ("la-and", "Andes & Southern Cone", [
     ("cl","Chile",["chile","chilean","mapuche","araucanía","wallmapu"]),
     ("ar","Argentina",["argentina","argentine","patagonia","qom","wichí"]),
     ("pe","Peru",["peru","peruvian","quechua","aymara peru"]),
     ("bo","Bolivia",["bolivia","bolivian","aymara","quechua bolivia"]),
     ("py","Paraguay",["paraguay","ayoreo","chaco paraguayo"]),
     ("uy","Uruguay",["uruguay"]),
   ]),
   ("la-ca", "Central America", [
     ("gt","Guatemala",["guatemala","guatemalan","ixil","k'iche'","q'eqchi'"]),
     ("hn","Honduras",["honduras","garífuna","lenca","berta cáceres"]),
     ("ni","Nicaragua",["nicaragua","miskito","bosawás"]),
     ("cr","Costa Rica",["costa rica","bribri","térraba"]),
     ("pa","Panama",["panama","guna","ngäbe","emberá"]),
     ("bz","Belize",["belize","maya belize"]),
     ("sv","El Salvador",["el salvador"]),
   ]),
   ("la-car", "Caribbean & Guianas", [
     ("gy","Guyana",["guyana","wapichan","rupununi"]),
     ("sr","Suriname",["suriname","saamaka","maroon suriname","kaliña"]),
     ("gf","French Guiana",["guyane","french guiana","wayana"]),
     ("do","Caribbean islands",["dominica kalinago","caribbean indigenous","taino","haiti","jamaica","puerto rico"]),
   ]),
   ("la-br", "Brazil (other)", [
     ("br","Brazil",["brazil", "brazilian", "anvisa", "brasilia", "sao paulo", "minas gerais", "bahia", "mato grosso"]),
     ("br-ne","Brazil northeast & cerrado",["cerrado","bahia indígena","maranhão","quilombola","pataxó","guarani-kaiowá","mato grosso do sul"]),
   ]),
 ]),
 ("asia-s", "South Asia", [
   ("sa-in", "India", [
     ("in","India",["india", "indian", "fssai", "cdsco", "sebi", "new delhi", "mumbai", "maharashtra", "uttar pradesh", "tamil nadu", "karnataka", "kerala", "west bengal", "gujarat", "rajasthan", "bihar", "andhra pradesh", "telangana"]),
     ("in-c","Central India",["chhattisgarh","jharkhand","odisha","madhya pradesh","hasdeo","niyamgiri","bastar"]),
     ("in-ne","Northeast India",["assam","manipur","nagaland","mizoram","meghalaya","arunachal"]),
     ("in-s","South & West India",["kerala adivasi","tamil nadu tribal","karnataka tribal","gujarat adivasi","maharashtra adivasi"]),
     ("in-h","Himalayan India",["ladakh","uttarakhand","himachal","sikkim"]),
   ]),
   ("sa-oth", "Rest of South Asia", [
     ("bd","Bangladesh",["bangladesh","chittagong hill tracts","jumma","chakma"]),
     ("np","Nepal",["nepal","tharu","newar","chepang"]),
     ("pk","Pakistan",["pakistan","balochistan","kalash"]),
     ("lk","Sri Lanka",["sri lanka","vedda"]),
     ("bt","Bhutan",["bhutan"]),
   ]),
 ]),
 ("asia-se", "Southeast Asia", [
   ("se-mar", "Maritime Southeast Asia", [
     ("id","Indonesia",["indonesia","indonesian","masyarakat adat","papua","west papua","kalimantan","dayak","sulawesi","sumatra","mentawai"]),
     ("ph","Philippines",["philippines","filipino","lumad","igorot","mindanao","cordillera","ancestral domain"]),
     ("my","Malaysia",["malaysia","sarawak","sabah","penan","orang asli","bakun"]),
     ("tl","Timor-Leste",["timor-leste","east timor"]),
     ("pg-ind","Papua New Guinea",["papua new guinea","bougainville","porgera"]),
   ]),
   ("se-main", "Mainland Southeast Asia", [
     ("th","Thailand",["thailand","karen thailand","bangkloi","chao lay","hill tribe"]),
     ("mm","Myanmar",["myanmar","burma","karen state","kachin","chin state","rakhine"]),
     ("vn","Vietnam",["vietnam","montagnard","central highlands vietnam"]),
     ("kh","Cambodia",["cambodia","bunong","ratanakiri"]),
     ("la","Laos",["laos","hmong laos"]),
   ]),
 ]),
 ("asia-e", "East & Central Asia", [
   ("ea-e", "East Asia", [
     ("tw","Taiwan",["taiwan","原住民族","傳統領域","amis","atayal","bunun"]),
     ("jp","Japan",["japan","ainu","hokkaido","okinawa","ryukyu","pmda","mhlw"]),
     ("cn","China",["china","tibet","tibetan","xinjiang","uyghur","inner mongolia","yunnan minority","nmpa","samr","guangdong"]),
     ("kr","Korea",["korea","korean","mfds"]),
     ("mn","Mongolia",["mongolia","mongolian","dukha","tsaatan"]),
   ]),
   ("ea-c", "Central Asia & Siberia", [
     ("ru-sib","Siberia & Russian North",["siberia","evenki","nenets","khanty","yamal","sakha","chukotka","коренные малочисленные"]),
     ("kz","Kazakhstan",["kazakhstan"]),
     ("kg","Kyrgyzstan",["kyrgyzstan"]),
     ("uz","Uzbekistan",["uzbekistan"]),
   ]),
 ]),
 ("mena", "Middle East & North Africa", [
   ("me-lev", "Levant & Gulf", [
     ("il","Israel & Palestine",["bedouin","negev","naqab","palestinian land","israel","west bank"]),
     ("jo","Jordan",["jordan","bedouin jordan"]),
     ("iq","Iraq",["iraq","marsh arabs","yazidi","kurdistan iraq"]),
     ("ir","Iran",["iran","qashqai","bakhtiari","ahwazi"]),
     ("sa","Gulf states",["saudi arabia","uae","oman","qatar","kuwait","sfda"]),
     ("tr","Turkey",["turkey","türkiye","kurdish","hasankeyf","alevi"]),
   ]),
 ]),
 ("europe", "Europe", [
   ("eu-n", "Nordic & Arctic Europe", [
     ("no","Norway",["norway","norwegian","sápmi","fosen","finnmark"]),
     ("se","Sweden",["sweden","swedish","girjas","gällivare","kiruna","samer"]),
     ("fi","Finland",["finland","finnish","inari","sámi parliament"]),
     ("gl","Greenland",["greenland","kalaallit","nuuk"]),
     ("ru-eu","Russian Karelia & Kola",["kola peninsula","karelia","murmansk sami"]),
   ]),
   ("eu-o", "Rest of Europe", [
     ("be","Belgium",["belgium", "belgian", "flanders", "wallonia", "antwerp"]),
     ("it","Italy",["italy", "italian", "rome", "milan", "sicily", "lombardy"]),
     ("ch","Switzerland",["switzerland", "swiss", "swissmedic", "geneva", "zurich", "bern"]),
     ("nl","Netherlands",["netherlands", "dutch", "nvwa", "amsterdam", "the hague"]),
     ("ua","Ukraine",["ukraine","crimean tatars","krym"]),
     ("ru","Russia (European)",["russia","russian federation"]),
     ("eu","European Union",["european union","european commission","brussels","efsa","ema","echa","european food safety authority","european medicines agency"]),
     ("uk","United Kingdom",["united kingdom","britain","scotland","wales","england","u.k.","uk","mhra","food standards agency","ofcom","ofgem","ofsted","northern ireland"]),
     ("es","Spain",["spain","spanish","catalonia","andalusia"]),
     ("fr","France",["france","french","anses"]),
     ("de","Germany",["germany","german","bfr","bavaria","saxony"]),
   ]),
 ]),
 ("oceania", "Oceania", [
   ("oc-au", "Australia", [
     ("au","Australia",["australia", "australian", "tga", "fsanz", "accc", "canberra", "sydney", "melbourne", "new south wales", "queensland", "western australia", "south australia", "tasmania", "northern territory"]),
     ("au-n","Northern Australia",["northern territory","arnhem land","kimberley","juukan gorge","tiwi","gulf country"]),
     ("au-w","Western Australia",["western australia","pilbara","noongar","yindjibarndi"]),
     ("au-e","Eastern Australia",["queensland","new south wales","victoria aboriginal","wiradjuri","gunditjmara","adani","carmichael"]),
     ("au-c","Central & South Australia",["south australia","adnyamathanha","arrernte","alice springs","olympic dam"]),
   ]),
   ("oc-nz", "Aotearoa New Zealand", [
     ("nz","Aotearoa",["new zealand","aotearoa","māori","maori","iwi","waitangi","ngāi tahu","tainui"]),
   ]),
   ("oc-pac", "Pacific Islands", [
     ("fj","Fiji",["fiji","fijian","itaukei"]),
     ("nc","Kanaky New Caledonia",["new caledonia","kanaky","kanak","nouméa"]),
     ("sb","Solomon Islands",["solomon islands"]),
     ("vu","Vanuatu",["vanuatu","ni-vanuatu"]),
     ("ws","Polynesia & Micronesia",["samoa","tonga","tuvalu","kiribati","marshall islands","palau","guam","chamorro","tahiti","rapa nui","easter island"]),
   ]),
 ]),
 ("polar", "Arctic & Antarctic", [
   ("pol-arc", "Circumpolar", [
     ("arctic","Arctic Council region",["arctic council","circumpolar","inuit circumpolar","arctic indigenous"]),
   ]),
 ]),
]

# --------------------------------------------------------------------------
# Subjects
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Subjects — after the Law Enforcement section of Suppression.
#
# Each subject is a list of (term, context) pairs. The term must appear AND at
# least one of its context words, which is what keeps a sector from swamping
# the wire: "copper output rises" has the sector but no control, so it fails;
# "copper concession auctioned" passes. Subjects with an empty context list
# are already control language on their own.
# --------------------------------------------------------------------------
# THE SUBJECTS
#
# Who takes the money, who carries the cost, and who is governing any of it.
# Fixing and betting lead, because that is where the section starts.
#
# This is a feed on the industry, not on the games. Results, fixtures,
# transfers and previews are refused, and every term carries the industry
# words it must appear beside.
# --------------------------------------------------------------------------
TOPICS = [
    ("matchfixing", "Fixing and throwing matches", [
        ("match-fixing", []), ("match fixing", []), ("spot-fixing", []), ("spot fixing", []),
        ("throwing", ["match", "game", "matches", "deliberately"]),
        ("fix", ["match", "game", "result", "outcome", "approached to"]),
        ("approached", ["fix", "throw", "player*", "athlete*", "official"]),
        ("integrity unit", []), ("sports integrity", []), ("betting integrity", []),
        ("suspicious betting", []), ("irregular betting", []), ("alert*", ["betting", "suspicious", "match"]),
        ("banned for", ["fixing", "betting", "corruption"]),
        ("court-siding", []), ("insider information", ["betting", "match", "team", "sold"]),
    ]),
    ("betting", "The money behind it", [
        ("sports betting", []), ("sportsbook", []), ("bookmaker", ["licence", "fined", "probe", "market"]),
        ("gambling operator", []), ("betting market", ["volume", "regulat*", "suspended", "billions"]),
        ("in-play betting", []), ("prop bet*", []), ("micro-bet*", []),
        ("betting sponsorship", []), ("shirt sponsor", ["betting", "gambling", "banned"]),
        ("gambling advertis*", []), ("betting advertis*", []),
        ("problem gambling", []), ("gambling harm", []), ("self-exclusion", []),
        ("affordability check", []), ("betting licence", ["revoked", "suspended", "granted", "review"]),
    ]),
    ("governance", "Who governs the sport", [
        ("governing body", ["corruption", "reform", "investigation", "resign*", "election", "audit"]),
        ("federation", ["corruption", "investigation", "suspended", "election", "reform", "banned"]),
        ("fifa", ["investigation", "corruption", "reform", "ban", "ethics", "ruling"]),
        ("ioc", ["decision", "reform", "investigation", "ruling", "governance"]),
        ("uefa", ["investigation", "ruling", "sanction", "reform", "licence"]),
        ("ethics committee", []), ("bribery", ["official", "federation", "vote", "hosting", "bid"]),
        ("vote-buying", ["host", "bid", "federation", "congress"]),
        ("bid", ["corruption", "investigation", "hosting", "vote", "rigged"]),
        ("suspended", ["federation", "national association", "member", "committee"]),
    ]),
    ("doping", "Doping and testing", [
        ("doping", []), ("anti-doping", []), ("wada", []), ("usada", []), ("nada", ["doping", "agency"]),
        ("failed test", ["athlete", "doping", "sample", "positive"]),
        ("banned substance", []), ("therapeutic use exemption", []), ("tue", ["doping", "exemption", "granted"]),
        ("whereabouts", ["missed", "failure", "rule", "athlete"]),
        ("sample", ["tampered", "swapped", "positive", "retested", "stored"]),
        ("state-sponsored doping", []), ("cover-up", ["doping", "test", "sample", "federation"]),
        ("ban", ["doping", "athlete", "years", "reduced", "upheld"]),
        ("cas", ["appeal", "ruling", "arbitration", "upheld", "overturned"]),
    ]),
    ("athletes", "Pay, contracts and rights", [
        ("athlete pay", []), ("prize money", ["gap", "equal", "raised", "dispute"]),
        ("equal pay", ["players", "team", "sport", "settlement", "claim"]),
        ("collective bargaining", ["players", "athletes", "league", "union"]),
        ("players union", []), ("player association", []), ("athlete representation", []),
        ("image rights", ["athlete", "player", "dispute", "contract"]),
        ("name image likeness", []), ("nil", ["deal", "athlete", "college", "rules"]),
        ("contract dispute", ["player", "athlete", "club", "federation"]),
        ("strike", ["players", "athletes", "league", "team", "threatened"]),
        ("transfer system", ["ruling", "challenge", "reform", "unlawful"]),
    ]),
    ("labour", "Who builds and services it", [
        ("migrant worker", ["stadium", "construction", "world cup", "olympic", "died", "conditions"]),
        ("construction worker", ["stadium", "died", "conditions", "wages", "unpaid"]),
        ("kafala", []), ("recruitment fee", ["worker", "stadium", "migrant"]),
        ("unpaid wages", ["worker", "stadium", "construction", "contractor"]),
        ("worker deaths", []), ("heat", ["worker", "stadium", "construction", "risk", "labour"]),
        ("compensation fund", ["worker", "migrant", "families"]),
        ("supply chain", ["kit", "merchandise", "sportswear", "labour", "audit"]),
        ("sportswear", ["factory", "labour", "wages", "conditions", "supply chain"]),
    ]),
    ("safeguarding", "Abuse and safeguarding", [
        ("safeguarding", []), ("abuse", ["athlete*", "gymnast*", "player*", "coach", "sport", "inquiry", "report"]),
        ("coach", ["abuse", "banned", "convicted", "charged", "misconduct", "struck off"]),
        ("inquiry", ["abuse", "safeguarding", "sport", "federation", "culture"]),
        ("whistleblower", ["athlete", "sport", "federation", "retaliation"]),
        ("duty of care", ["athlete", "sport", "review"]),
        ("bullying", ["culture", "athletes", "programme", "review", "coaching"]),
        ("reporting mechanism", ["athlete", "abuse", "safeguarding", "failed"]),
    ]),
    ("youth", "Young and student athletes", [
        ("student-athlete", []), ("student athlete", []), ("college athlete", []),
        ("youth academy", []), ("academy", ["player", "released", "welfare", "recruit*", "football"]),
        ("minors", ["transfer", "recruit*", "signed", "rules", "protection"]),
        ("scholarship", ["athlete", "withdrawn", "conditions", "college"]),
        ("amateur", ["rules", "status", "payment", "reform"]),
        ("scouting", ["minors", "children", "regulat*", "abroad"]),
        ("trafficking", ["young players", "football", "athletes", "academies"]),
    ]),
    ("megaevents", "Hosting and what it costs", [
        ("world cup", ["cost", "bid", "hosting", "workers", "displacement", "boycott", "budget"]),
        ("olympic", ["cost", "bid", "hosting", "budget", "displacement", "overrun", "legacy"]),
        ("hosting rights", []), ("bid process", []), ("host city contract", []),
        ("cost overrun", []), ("public funding", ["stadium", "games", "event", "bailout"]),
        ("displacement", ["residents", "games", "event", "stadium", "evict*"]),
        ("legacy", ["venue", "unused", "games", "promised", "unused"]),
        ("white elephant", []), ("security", ["games", "event", "surveillance", "powers", "temporary"]),
    ]),
    ("sportswashing", "Reputation and state money", [
        ("sportswashing", []), ("state-owned", ["club", "team", "league", "investment"]),
        ("sovereign wealth", ["club", "team", "league", "sport", "investment"]),
        ("takeover", ["club", "team", "ownership test", "approved", "blocked"]),
        ("human rights", ["hosting", "event", "sport", "criteria", "bid", "concerns"]),
        ("boycott", ["games", "event", "team", "athletes", "called"]),
        ("reputation", ["laundering", "state", "sport", "investment"]),
        ("soft power", ["sport", "investment", "hosting", "strategy"]),
    ]),
    ("ownership", "Clubs, leagues and money", [
        ("club ownership", []), ("owners test", []), ("fit and proper", []),
        ("private equity", ["club", "league", "sport", "stake", "investment"]),
        ("multi-club", ["ownership", "group", "model", "rules"]),
        ("financial fair play", []), ("profit and sustainability", []),
        ("points deduction", []), ("administration", ["club", "insolvenc*", "wound up"]),
        ("breakaway", ["league", "super league", "competition", "proposal"]),
        ("franchise", ["relocation", "moved", "expansion", "fee"]),
        ("fan ownership", []), ("supporters trust", []), ("golden share", []),
    ]),
    ("broadcast", "Who can watch it", [
        ("broadcast rights", []), ("media rights", ["sold", "auction", "value", "deal", "collective"]),
        ("streaming rights", []), ("paywall", ["sport", "match", "coverage", "fans"]),
        ("listed events", []), ("free-to-air", []), ("blackout", ["broadcast", "match", "rule"]),
        ("piracy", ["stream", "broadcast", "match", "enforcement", "blocking"]),
        ("subscription", ["sport", "fans", "cost", "fragmented", "rise"]),
        ("collective selling", []), ("rights value", ["fell", "rose", "record", "deal"]),
    ]),
    ("health", "Injury and long-term harm", [
        ("concussion", []), ("head injury", []), ("cte", []), ("brain injury", ["sport", "players", "study", "claim"]),
        ("dementia", ["footballers", "players", "rugby", "study", "risk"]),
        ("injury", ["workload", "schedule", "risk", "study", "rate", "burnout"]),
        ("player welfare", []), ("match schedule", ["congestion", "workload", "dispute", "expanded"]),
        ("heat", ["athletes", "players", "postponed", "risk", "guideline"]),
        ("mental health", ["athlete*", "player*", "programme", "support", "pressure"]),
        ("legal claim", ["concussion", "brain injury", "players", "former"]),
    ]),
    ("eligibility", "Rules on who may compete", [
        ("eligibility", ["rules", "policy", "ruling", "criteria", "changed", "challenge"]),
        ("classification", ["para", "paralympic", "athlete", "review", "system"]),
        ("gender eligibility", []), ("sex testing", []), ("dsd", ["regulations", "athletes", "ruling"]),
        ("transgender", ["policy", "eligibility", "rules", "ruling", "federation"]),
        ("testosterone", ["limit", "regulation", "policy", "ruling"]),
        ("nationality", ["switch", "eligibility", "rules", "naturalis*", "naturaliz*"]),
        ("court of arbitration", []), ("appeal", ["eligibility", "classification", "ruling", "ban"]),
    ]),
    ("stadiums", "Stadiums, land and public money", [
        ("stadium", ["public money", "subsid*", "funding", "cost", "taxpayer", "deal", "demolish"]),
        ("subsid*", ["stadium", "arena", "team", "franchise", "public"]),
        ("taxpayer", ["stadium", "arena", "funding", "cost", "bill"]),
        ("land", ["stadium", "development", "acquired", "compulsory", "sold"]),
        ("regeneration", ["stadium", "promised", "failed", "displacement"]),
        ("relocation", ["team", "franchise", "threat", "approved"]),
        ("naming rights", []), ("ticket price", ["rise", "cap", "protest", "fans"]),
        ("ticket resale", []), ("dynamic pricing", []),
    ]),
    ("environment", "What it costs the ground it is played on", [
        ("carbon footprint", ["sport", "event", "games", "club", "league"]),
        ("emissions", ["sport", "event", "travel", "games", "club"]),
        ("sponsorship", ["fossil fuel", "airline", "oil", "high-carbon"]),
        ("greenwash*", ["sport", "event", "club", "sponsorship", "claims"]),
        ("water use", ["golf", "course", "resort", "stadium", "snow"]),
        ("artificial snow", []), ("winter games", ["snow", "climate", "viable", "warming"]),
        ("climate", ["fixture", "postponed", "heat", "sport", "risk", "adaptation"]),
        ("travel", ["fixtures", "expanded", "emissions", "schedule", "flights"]),
    ]),
    ("animals", "Animal racing and animal sports", [
        ("horse racing", ["death", "injur*", "welfare", "ban", "inquiry", "drug", "regulat*"]),
        ("greyhound racing", []), ("dog racing", []), ("pigeon racing", ["welfare", "losses", "ban"]),
        ("rodeo", []), ("charreada", []), ("jaripeo", []), ("coleo", []), ("bull riding", []),
        ("bullfight*", []), ("racehorse", ["died", "killed", "injur*", "welfare", "retired", "slaughter"]),
        ("euthanis*", ["horse", "greyhound", "dog", "track"]),
        ("culled", ["horses", "greyhounds", "dogs", "uncompetitive"]),
        ("doping", ["horse", "greyhound", "racing", "trainer"]),
        ("animal welfare", ["racing", "sport", "rodeo", "ban", "law", "inquiry"]),
        ("track", ["deaths", "fatalit*", "closed", "welfare", "record"]),
    ]),
    ("regulation", "Rules, enforcement and integrity bodies", [
        ("sports regulator", []), ("independent regulator", []), ("football regulator", []),
        ("regulator", ["football", "sport", "racing", "gambling", "bill", "powers", "established"]),
        ("integrity commission", []), ("national platform", ["match-fixing", "integrity"]),
        ("macolin convention", []), ("criminalis*", ["match-fixing", "doping", "betting"]),
        ("criminaliz*", ["match-fixing", "doping", "betting"]),
        ("prosecution", ["fixing", "doping", "corruption", "betting", "sport"]),
        ("sanction", ["federation", "club", "athlete", "upheld", "reduced"]),
        ("governance code", ["sport", "federation", "compliance"]),
        ("audit", ["federation", "governing body", "accounts", "funding"]),
    ]),
    ("resistance", "What is set against it", [
        ("athlete activism", []), ("players protest", []), ("fan protest", []),
        ("supporters", ["protest", "campaign", "boycott", "ownership", "trust"]),
        ("campaign", ["gambling advert*", "ticket price", "safe standing", "concussion", "equal pay"]),
        ("reform", ["governance", "federation", "transfer system", "ownership", "regulator"]),
        ("ruling", ["athletes", "players", "equal pay", "transfer", "eligibility"]),
        ("class action", ["players", "athletes", "concussion", "former"]),
        ("union recognition", ["players", "athletes", "esports", "riders"]),
        ("safe standing", []), ("ticket cap", []),
    ]),
]

ANCHOR = [
    # Sports-industry language. A result or a transfer is not this subject; who
    # takes the money, who carries the cost, and who governs it, is.
    "match-fixing", "match fixing", "spot-fixing", "sports integrity",
    "betting integrity", "suspicious betting", "irregular betting",
    "sports betting", "gambling harm", "betting sponsorship", "gambling advertising",
    "governing body", "federation corruption", "ethics committee", "vote-buying",
    "doping", "anti-doping", "wada", "banned substance", "therapeutic use exemption",
    "court of arbitration for sport", "athlete pay", "prize money", "equal pay",
    "collective bargaining", "players union", "name image likeness",
    "transfer system", "migrant worker", "worker deaths", "kafala",
    "safeguarding", "duty of care", "student-athlete", "youth academy",
    "world cup hosting", "olympic bid", "cost overrun", "host city contract",
    "sportswashing", "sovereign wealth", "owners test", "fit and proper",
    "financial fair play", "multi-club ownership", "breakaway league",
    "broadcast rights", "media rights", "listed events", "free-to-air",
    "concussion", "cte", "player welfare", "match schedule congestion",
    "eligibility rules", "classification", "gender eligibility",
    "stadium subsidy", "taxpayer funding", "ticket resale", "dynamic pricing",
    "carbon footprint", "fossil fuel sponsorship",
    "horse racing welfare", "greyhound racing", "rodeo", "charreada", "bullfighting",
    "independent regulator", "macolin convention", "integrity commission",
]

BLOCK = [
    # results and the rest of the back pages, which are nearly all sports coverage
    "final score", "full-time", "half-time", "match report", "as it happened",
    "player ratings", "man of the match", "starting lineup", "team news",
    "fixtures", "results", "league table", "standings", "highlights",
    "preview", "predicted lineup", "injury update", "fitness test",
    "transfer rumour", "transfer news", "linked with", "medical scheduled",
    "signs for", "loan deal", "contract extension signed", "shirt number",
    "fantasy", "odds", "tips", "accumulator", "best bets", "betting preview",
    "goal of the month", "record broken", "hat-trick", "century", "grand slam",
    "wins gold", "medal table", "podium", "qualifies for", "knocked out",
    # unrelated filler
    "film review", "video game review", "recipe", "gift guide", "coupon",
    "black friday", "horoscope", "sponsored content", "partner content",
]

DECIDED = [
    "approved", "signed", "awarded", "granted", "ratified", "enacted", "passed",
    "ruling", "ruled", "struck down", "upheld", "overturned", "judgment", "judgement",
    "took effect", "came into force", "repealed", "revoked", "banned", "prohibited",
    "fined", "settled", "suspended", "expelled", "convicted", "charged",
    "ordered", "blocked", "points deducted", "licence revoked", "stripped of",
]
INSTITUTIONAL = [
    "court of arbitration for sport", "world anti-doping agency", "wada",
    "international olympic committee", "council of europe", "unesco",
    "european commission", "european parliament", "interpol", "europol",
    "gambling commission", "sports regulator", "integrity commission",
    "auditor general", "national audit office", "government accountability office",
    "parliamentary committee", "select committee", "official gazette",
    "court filing", "public inquiry", "ombudsman",
    "peer-reviewed", "published in", "study finds", "working paper", "dataset",
    "official data", "government figures", "national statistics",
]
MEASURED = [
    "per cent", "percent", "%", "one in", "1 in", "million", "billion",
    "figures show", "fell by", "rose by", "increase of", "decrease of",
    "estimated", "median", "average", "ranked", "index", "share of", "rate of",
    "deaths", "cases", "alerts", "cost", "budget", "attendance",
]
PENDING = [
    "proposed", "draft law", "bill", "consultation", "under review", "expected to",
    "due to decide", "hearing scheduled", "vote scheduled", "reading", "deadline",
    "next month", "next year", "review scheduled", "pending approval",
    "inquiry launched", "investigation opened", "green paper", "white paper",
    "appeal pending", "trial date",
]


ANCHOR_C = _compile_all(ANCHOR)
BLOCK_C = _compile_all(BLOCK)
DECIDED_C = _compile_all(DECIDED)
INSTITUTIONAL_C = _compile_all(INSTITUTIONAL)
MEASURED_C = _compile_all(MEASURED)
PENDING_C = _compile_all(PENDING)
TOPICS_C = [(tid, label, [(_compile(t), _compile_all(g) if g else None) for t, g in terms])
            for tid, label, terms in TOPICS]
GEO3_C = [(rid, rlabel, [(sid, slabel, [(pid, plabel, _compile_all(terms))
                                        for pid, plabel, terms in places])
                        for sid, slabel, places in subs])
          for rid, rlabel, subs in GEO3]


def relevant(text):
    """A subject has to claim the story.

    An anchor term alone used to be enough, with a fallback subject put on the
    result. That labels a story the wire never actually recognised, so a piece
    that merely mentions a market word arrives filed under a real subject. A
    story no subject will claim is refused and counted as refused instead."""
    if hit(text, BLOCK_C):
        return False
    return bool(topics_for(text))


def weight(text, standing, placed):
    """What the story contains, as a score and the reasons for it."""
    total, reasons = 0, []
    if hit(text, DECIDED_C):
        total += 2
        reasons.append("decided")
    if hit(text, INSTITUTIONAL_C):
        total += 2
        reasons.append("institutional")
    if hit(text, MEASURED_C):
        total += 1
        reasons.append("measured")
    if hit(text, PENDING_C):
        total += 1
        reasons.append("pending")
    if placed:
        total += 1
        reasons.append("located")
    if standing in ("official", "research"):
        total += 1
        reasons.append("primary source")
    return total, reasons


def topics_for(text):
    hits = []
    for tid, _label, terms in TOPICS_C:
        for term, guards in terms:
            if not hit(text, [term]):
                continue
            if guards and not hit(text, guards):
                continue
            hits.append(tid)
            break
    return hits


def places_for(text):
    """Returns (regions, subregions, places). Naming a place implies the
    subregion and region above it."""
    regions, subs, places = [], [], []
    for rid, _rl, sublist in GEO3_C:
        for sid, _sl, plist in sublist:
            for pid, _pl, terms in plist:
                if not hit(text, terms):
                    continue
                if pid not in places:
                    places.append(pid)
                if sid not in subs:
                    subs.append(sid)
                if rid not in regions:
                    regions.append(rid)
    return (regions or ["unlocated"], subs or ["unlocated"], places or ["unlocated"])


# ---------------------------------------------------------------- placement
# Coordinates for the gazetteer, a table of named places, and the routine that
# resolves a story to the most specific point it names.
#
# Nothing here decides what a story is ABOUT. That belongs to this feed's own
# relevant() and topics_for(), and must never be shadowed by anything arriving
# alongside the coordinates: a later definition wins in Python, and one
# careless slice once handed five feeds the conflict wire's vocabulary.

COORDS = {
 # --- regions ---
 "africa": [1.5, 20.0], "americas-n": [45.0, -100.0], "americas-s": [-12.0, -60.0],
 "asia-s": [22.0, 79.0], "asia-se": [2.0, 112.0], "asia-e": [40.0, 100.0],
 "mena": [28.0, 42.0], "europe": [52.0, 15.0], "oceania": [-25.0, 140.0], "polar": [78.0, 0.0],
 # --- subregions ---
 "africa-e": [1.0, 37.0], "africa-w": [10.0, -2.0], "africa-c": [0.0, 20.0],
 "africa-s": [-24.0, 24.0], "africa-n": [28.0, 12.0],
 "na-us": [39.0, -98.0], "na-ca": [58.0, -100.0], "na-mx": [23.0, -102.0],
 "la-amz": [-4.0, -62.0], "la-and": [-25.0, -68.0], "la-ca": [14.0, -87.0],
 "la-car": [8.0, -60.0], "la-br": [-13.0, -47.0],
 "sa-in": [22.0, 79.0], "sa-oth": [27.0, 85.0],
 "se-mar": [-2.0, 118.0], "se-main": [16.0, 101.0],
 "ea-e": [35.0, 118.0], "ea-c": [50.0, 80.0],
 "me-lev": [32.0, 40.0],
 "eu-n": [65.0, 20.0], "eu-o": [50.0, 15.0],
 "oc-au": [-25.0, 134.0], "oc-nz": [-41.0, 174.0], "oc-pac": [-15.0, 170.0],
 "pol-arc": [80.0, 0.0],
 # --- places: Africa ---
 "ke": [0.2, 37.9], "tz": [-6.4, 34.9], "ug": [1.4, 32.3], "et": [9.1, 40.5],
 "so": [5.2, 46.2], "rw": [-1.9, 29.9], "bi": [-3.4, 29.9], "sd": [15.6, 30.2],
 "ss": [7.9, 30.0], "mg": [-18.8, 46.9], "mz": [-18.7, 35.5], "zm": [-13.1, 27.8],
 "zw": [-19.0, 29.2], "mw": [-13.3, 34.3],
 "ng": [9.1, 8.7], "gh": [7.9, -1.0], "ci": [7.5, -5.5], "sn": [14.5, -14.5],
 "ml": [17.6, -4.0], "bf": [12.2, -1.6], "ne": [17.6, 8.1], "lr": [6.4, -9.4],
 "sl": [8.5, -11.8], "gn": [9.9, -9.7], "cm": [7.4, 12.4], "sahel": [15.0, 2.0], "horn": [8.0, 45.0],
 "cd": [-4.0, 21.8], "cg": [-0.2, 15.8], "ga": [-0.8, 11.6], "cf": [6.6, 20.9], "td": [15.5, 18.7],
 "za": [-30.6, 22.9], "bw": [-22.3, 24.7], "na": [-22.9, 18.5], "ao": [-11.2, 17.9], "ls": [-29.6, 28.2],
 "ma": [31.8, -7.1], "dz": [28.0, 1.7], "tn": [33.9, 9.5], "ly": [26.3, 17.2], "eg": [26.8, 30.8],
 # --- places: North America ---
 "us-ak": [64.0, -152.0], "us-sw": [34.5, -110.0], "us-pl": [44.0, -100.0],
 "us-pnw": [46.5, -121.0], "us-e": [35.5, -80.0], "us-hi": [20.8, -156.3],
 "ca-bc": [54.0, -125.0], "ca-pr": [52.0, -106.0], "ca-on": [49.0, -80.0],
 "ca-n": [64.0, -105.0], "ca-at": [46.5, -63.0],
 "mx-s": [17.0, -94.0], "mx-n": [28.5, -108.0],
 # --- places: Latin America ---
 "br-amz": [-4.5, -60.0], "pe-amz": [-6.0, -75.0], "co-amz": [-1.0, -72.0],
 "ec-amz": [-1.5, -76.5], "bo-amz": [-14.5, -65.0], "ve-amz": [5.0, -65.0],
 "cl": [-35.7, -71.5], "ar": [-38.4, -63.6], "pe": [-9.2, -75.0], "bo": [-16.3, -63.6],
 "py": [-23.4, -58.4], "uy": [-32.5, -55.8],
 "gt": [15.8, -90.2], "hn": [15.2, -86.2], "ni": [12.9, -85.2], "cr": [9.7, -83.8],
 "pa": [8.5, -80.8], "bz": [17.2, -88.5], "sv": [13.8, -88.9],
 "gy": [4.9, -58.9], "sr": [3.9, -56.0], "gf": [3.9, -53.1], "do": [18.7, -70.2],
 "br-ne": [-10.0, -45.0],
 # --- places: South Asia ---
 "in-c": [21.5, 82.0], "in-ne": [26.0, 93.0], "in-s": [13.0, 77.5], "in-h": [32.0, 78.0],
 "bd": [23.7, 90.4], "np": [28.4, 84.1], "pk": [30.4, 69.3], "lk": [7.9, 80.8], "bt": [27.5, 90.4],
 # --- places: Southeast Asia ---
 "id": [-2.5, 118.0], "ph": [12.9, 121.8], "my": [4.2, 109.5], "tl": [-8.9, 125.7], "pg-ind": [-6.3, 143.9],
 "th": [15.9, 100.99], "mm": [21.9, 95.96], "vn": [14.1, 108.3], "kh": [12.6, 104.99], "la": [19.9, 102.5],
 # --- places: East & Central Asia ---
 "tw": [23.7, 121.0], "jp": [36.2, 138.3], "cn": [35.9, 104.2], "kr": [36.5, 127.9], "mn": [46.9, 103.8],
 "ru-sib": [62.0, 105.0], "kz": [48.0, 66.9], "kg": [41.2, 74.8], "uz": [41.4, 64.6],
 # --- places: MENA ---
 "il": [31.5, 35.0], "jo": [30.6, 36.2], "iq": [33.2, 43.7], "ir": [32.4, 53.7],
 "sa": [24.0, 45.0], "tr": [39.0, 35.2],
 # --- places: Europe ---
 "no": [64.6, 12.0], "se": [62.0, 15.0], "fi": [64.0, 26.0], "gl": [71.7, -42.6], "ru-eu": [67.5, 35.0],
 "ua": [48.4, 31.2], "ru": [56.0, 40.0], "eu": [50.8, 4.4], "uk": [54.0, -2.5],
 "es": [40.2, -3.7], "fr": [46.6, 2.4], "de": [51.2, 10.4],
 # --- places: Oceania ---
 "au-n": [-15.0, 133.0], "au-w": [-25.0, 121.0], "au-e": [-30.0, 148.0], "au-c": [-29.0, 135.0],
 "nz": [-41.0, 174.0], "fj": [-17.7, 178.0], "nc": [-21.3, 165.5], "sb": [-9.6, 160.2],
 "vu": [-15.4, 166.9], "ws": [-13.8, -172.1],
 # --- polar ---
 "arctic": [80.0, 0.0],
}

PRECISE = {
 # --- Ukraine & Russia ---
 "kyiv": ("Kyiv", 50.45, 30.52), "kiev": ("Kyiv", 50.45, 30.52),
 "kharkiv": ("Kharkiv", 49.99, 36.23), "odesa": ("Odesa", 46.48, 30.73),
 "odessa": ("Odesa", 46.48, 30.73), "lviv": ("Lviv", 49.84, 24.03),
 "dnipro": ("Dnipro", 48.46, 35.05), "zaporizhzhia": ("Zaporizhzhia", 47.84, 35.14),
 "kherson": ("Kherson", 46.64, 32.61), "mykolaiv": ("Mykolaiv", 46.98, 31.99),
 "donetsk": ("Donetsk", 48.02, 37.80), "luhansk": ("Luhansk", 48.57, 39.31),
 "donbas": ("Donbas", 48.30, 38.20), "mariupol": ("Mariupol", 47.10, 37.55),
 "bakhmut": ("Bakhmut", 48.60, 38.00), "avdiivka": ("Avdiivka", 48.14, 37.75),
 "pokrovsk": ("Pokrovsk", 48.28, 37.18), "kupiansk": ("Kupiansk", 49.71, 37.62),
 "sumy": ("Sumy", 50.91, 34.80), "chernihiv": ("Chernihiv", 51.49, 31.29),
 "crimea": ("Crimea", 45.30, 34.40), "sevastopol": ("Sevastopol", 44.62, 33.53),
 "moscow": ("Moscow", 55.75, 37.62), "kremlin": ("Moscow", 55.75, 37.62),
 "belgorod": ("Belgorod", 50.60, 36.59), "kursk region": ("Kursk", 51.73, 36.19),
 "rostov": ("Rostov-on-Don", 47.24, 39.71), "novorossiysk": ("Novorossiysk", 44.72, 37.77),
 "st petersburg": ("St Petersburg", 59.94, 30.31), "vladivostok": ("Vladivostok", 43.12, 131.89),
 # --- Israel, Palestine, Lebanon, Syria ---
 "gaza city": ("Gaza City", 31.51, 34.45), "gaza strip": ("Gaza", 31.42, 34.35),
 "gaza": ("Gaza", 31.42, 34.35), "rafah": ("Rafah", 31.29, 34.25),
 "khan younis": ("Khan Younis", 31.34, 34.30), "deir al-balah": ("Deir al-Balah", 31.42, 34.35),
 "west bank": ("West Bank", 31.95, 35.30), "jenin": ("Jenin", 32.46, 35.30),
 "nablus": ("Nablus", 32.22, 35.26), "hebron": ("Hebron", 31.53, 35.10),
 "ramallah": ("Ramallah", 31.90, 35.21), "jerusalem": ("Jerusalem", 31.78, 35.22),
 "tel aviv": ("Tel Aviv", 32.09, 34.78), "haifa": ("Haifa", 32.79, 34.99),
 "golan": ("Golan Heights", 32.95, 35.75), "sderot": ("Sderot", 31.52, 34.60),
 "beirut": ("Beirut", 33.89, 35.50), "south lebanon": ("South Lebanon", 33.30, 35.40),
 "tyre": ("Tyre", 33.27, 35.20), "baalbek": ("Baalbek", 34.01, 36.21),
 "damascus": ("Damascus", 33.51, 36.29), "aleppo": ("Aleppo", 36.20, 37.13),
 "idlib": ("Idlib", 35.93, 36.63), "homs": ("Homs", 34.73, 36.71),
 "latakia": ("Latakia", 35.52, 35.79), "deir ez-zor": ("Deir ez-Zor", 35.34, 40.14),
 "hasakah": ("Hasakah", 36.50, 40.75), "rojava": ("North-east Syria", 36.40, 40.70),
 # --- Iraq, Iran, Gulf, Yemen ---
 "baghdad": ("Baghdad", 33.31, 44.36), "mosul": ("Mosul", 36.35, 43.13),
 "erbil": ("Erbil", 36.19, 44.01), "basra": ("Basra", 30.51, 47.78),
 "fallujah": ("Fallujah", 33.35, 43.78), "kirkuk": ("Kirkuk", 35.47, 44.39),
 "tehran": ("Tehran", 35.69, 51.39), "isfahan": ("Isfahan", 32.65, 51.67),
 "natanz": ("Natanz", 33.72, 51.73), "fordow": ("Fordow", 34.88, 50.99),
 "bandar abbas": ("Bandar Abbas", 27.19, 56.28), "strait of hormuz": ("Strait of Hormuz", 26.57, 56.25),
 "riyadh": ("Riyadh", 24.71, 46.68), "jeddah": ("Jeddah", 21.49, 39.19),
 "doha": ("Doha", 25.29, 51.53), "al udeid": ("Al Udeid air base", 25.12, 51.32),
 "abu dhabi": ("Abu Dhabi", 24.45, 54.38), "dubai": ("Dubai", 25.20, 55.27),
 "manama": ("Manama", 26.23, 50.59), "kuwait city": ("Kuwait City", 29.38, 47.99),
 "muscat": ("Muscat", 23.59, 58.41),
 "sanaa": ("Sanaa", 15.37, 44.19), "sana'a": ("Sanaa", 15.37, 44.19),
 "aden": ("Aden", 12.79, 45.02), "hodeidah": ("Hodeidah", 14.80, 42.95),
 "marib": ("Marib", 15.46, 45.32), "bab el-mandeb": ("Bab el-Mandeb", 12.58, 43.33),
 "red sea": ("Red Sea", 20.00, 38.00),
 # --- Turkey, Caucasus, Central Asia, Afghanistan ---
 "ankara": ("Ankara", 39.93, 32.86), "istanbul": ("Istanbul", 41.01, 28.98),
 "incirlik": ("Incirlik air base", 37.00, 35.43), "diyarbakir": ("Diyarbakır", 37.91, 40.24),
 "yerevan": ("Yerevan", 40.18, 44.51), "baku": ("Baku", 40.41, 49.87),
 "nagorno-karabakh": ("Nagorno-Karabakh", 39.82, 46.75), "karabakh": ("Nagorno-Karabakh", 39.82, 46.75),
 "tbilisi": ("Tbilisi", 41.72, 44.78), "abkhazia": ("Abkhazia", 43.00, 41.00),
 "south ossetia": ("South Ossetia", 42.35, 43.97),
 "kabul": ("Kabul", 34.53, 69.17), "kandahar": ("Kandahar", 31.61, 65.71),
 "herat": ("Herat", 34.35, 62.20), "jalalabad": ("Jalalabad", 34.43, 70.45),
 "dushanbe": ("Dushanbe", 38.56, 68.79), "tashkent": ("Tashkent", 41.30, 69.24),
 "almaty": ("Almaty", 43.24, 76.89), "astana": ("Astana", 51.17, 71.45),
 # --- South Asia ---
 "islamabad": ("Islamabad", 33.68, 73.05), "rawalpindi": ("Rawalpindi", 33.60, 73.04),
 "karachi": ("Karachi", 24.86, 67.01), "peshawar": ("Peshawar", 34.01, 71.58),
 "quetta": ("Quetta", 30.18, 66.98), "balochistan": ("Balochistan", 28.50, 65.50),
 "kashmir": ("Kashmir", 34.08, 74.80), "srinagar": ("Srinagar", 34.08, 74.80),
 "line of control": ("Line of Control", 34.20, 74.20),
 "new delhi": ("New Delhi", 28.61, 77.21), "mumbai": ("Mumbai", 19.08, 72.88),
 "manipur": ("Manipur", 24.66, 93.91), "assam": ("Assam", 26.20, 92.94),
 "dhaka": ("Dhaka", 23.81, 90.41), "chittagong hill tracts": ("Chittagong Hill Tracts", 22.60, 92.20),
 "colombo": ("Colombo", 6.93, 79.86), "kathmandu": ("Kathmandu", 27.72, 85.32),
 # --- East & Southeast Asia ---
 "beijing": ("Beijing", 39.90, 116.41), "shanghai": ("Shanghai", 31.23, 121.47),
 "taiwan strait": ("Taiwan Strait", 24.50, 119.50), "taipei": ("Taipei", 25.03, 121.57),
 "kinmen": ("Kinmen", 24.44, 118.32), "south china sea": ("South China Sea", 13.00, 114.00),
 "spratly": ("Spratly Islands", 9.50, 114.00), "paracel": ("Paracel Islands", 16.50, 112.00),
 "scarborough shoal": ("Scarborough Shoal", 15.15, 117.76),
 "senkaku": ("Senkaku Islands", 25.75, 123.48), "diaoyu": ("Senkaku Islands", 25.75, 123.48),
 "xinjiang": ("Xinjiang", 41.00, 85.00), "tibet": ("Tibet", 31.00, 88.00),
 "pyongyang": ("Pyongyang", 39.04, 125.76), "yongbyon": ("Yongbyon", 39.80, 125.75),
 "panmunjom": ("Panmunjom", 37.96, 126.68), "seoul": ("Seoul", 37.57, 126.98),
 "tokyo": ("Tokyo", 35.68, 139.69), "okinawa": ("Okinawa", 26.34, 127.80),
 "guam": ("Guam", 13.44, 144.79), "manila": ("Manila", 14.60, 120.98),
 "mindanao": ("Mindanao", 7.50, 124.50), "jakarta": ("Jakarta", -6.21, 106.85),
 "west papua": ("West Papua", -4.00, 138.00), "papua": ("Papua", -4.00, 138.00),
 "naypyidaw": ("Naypyidaw", 19.75, 96.10), "yangon": ("Yangon", 16.87, 96.20),
 "rakhine": ("Rakhine State", 20.10, 93.50), "kachin": ("Kachin State", 25.80, 97.40),
 "karen state": ("Karen State", 17.30, 97.70), "shan state": ("Shan State", 21.50, 98.00),
 "bangkok": ("Bangkok", 13.76, 100.50), "hanoi": ("Hanoi", 21.03, 105.85),
 "phnom penh": ("Phnom Penh", 11.56, 104.92),
 # --- Africa ---
 "khartoum": ("Khartoum", 15.50, 32.56), "omdurman": ("Omdurman", 15.65, 32.48),
 "port sudan": ("Port Sudan", 19.62, 37.22), "darfur": ("Darfur", 13.00, 24.00),
 "el fasher": ("El Fasher", 13.63, 25.35), "nyala": ("Nyala", 12.05, 24.88),
 "juba": ("Juba", 4.85, 31.58), "addis ababa": ("Addis Ababa", 9.03, 38.74),
 "tigray": ("Tigray", 14.00, 38.50), "amhara": ("Amhara", 11.50, 38.00),
 "mekelle": ("Mekelle", 13.50, 39.47), "asmara": ("Asmara", 15.34, 38.93),
 "mogadishu": ("Mogadishu", 2.05, 45.32), "kismayo": ("Kismayo", -0.36, 42.55),
 "puntland": ("Puntland", 8.50, 49.00), "nairobi": ("Nairobi", -1.29, 36.82),
 "kinshasa": ("Kinshasa", -4.44, 15.27), "goma": ("Goma", -1.68, 29.22),
 "north kivu": ("North Kivu", -0.80, 29.00), "south kivu": ("South Kivu", -3.00, 28.30),
 "bukavu": ("Bukavu", -2.51, 28.86), "ituri": ("Ituri", 1.80, 29.90),
 "bangui": ("Bangui", 4.39, 18.56), "n'djamena": ("N'Djamena", 12.13, 15.06),
 "bamako": ("Bamako", 12.64, -8.00), "gao": ("Gao", 16.27, -0.04),
 "timbuktu": ("Timbuktu", 16.77, -3.01), "ouagadougou": ("Ouagadougou", 12.37, -1.52),
 "niamey": ("Niamey", 13.51, 2.13), "lake chad": ("Lake Chad basin", 13.00, 14.00),
 "abuja": ("Abuja", 9.06, 7.49), "borno": ("Borno State", 11.80, 13.10),
 "maiduguri": ("Maiduguri", 11.83, 13.15), "lagos": ("Lagos", 6.52, 3.38),
 "tripoli libya": ("Tripoli", 32.89, 13.19), "benghazi": ("Benghazi", 32.12, 20.07),
 "cairo": ("Cairo", 30.04, 31.24), "sinai": ("Sinai", 29.50, 33.80),
 "cabo delgado": ("Cabo Delgado", -12.50, 39.50), "maputo": ("Maputo", -25.97, 32.57),
 "harare": ("Harare", -17.83, 31.05), "pretoria": ("Pretoria", -25.75, 28.19),
 "johannesburg": ("Johannesburg", -26.20, 28.05), "cape town": ("Cape Town", -33.92, 18.42),
 # --- Europe ---
 "brussels": ("Brussels", 50.85, 4.35), "the hague": ("The Hague", 52.08, 4.31),
 "geneva": ("Geneva", 46.20, 6.14), "vienna": ("Vienna", 48.21, 16.37),
 "london": ("London", 51.51, -0.13), "paris": ("Paris", 48.86, 2.35),
 "berlin": ("Berlin", 52.52, 13.40), "ramstein": ("Ramstein air base", 49.44, 7.60),
 "rome": ("Rome", 41.90, 12.50), "madrid": ("Madrid", 40.42, -3.70),
 "warsaw": ("Warsaw", 52.23, 21.01), "rzeszow": ("Rzeszów", 50.04, 22.00),
 "kaliningrad": ("Kaliningrad", 54.71, 20.51), "suwalki": ("Suwałki gap", 54.10, 23.00),
 "minsk": ("Minsk", 53.90, 27.57), "chisinau": ("Chișinău", 47.01, 28.86),
 "transnistria": ("Transnistria", 47.20, 29.20), "vilnius": ("Vilnius", 54.69, 25.28),
 "riga": ("Riga", 56.95, 24.11), "tallinn": ("Tallinn", 59.44, 24.75),
 "helsinki": ("Helsinki", 60.17, 24.94), "stockholm": ("Stockholm", 59.33, 18.07),
 "oslo": ("Oslo", 59.91, 10.75), "gotland": ("Gotland", 57.50, 18.50),
 "belgrade": ("Belgrade", 44.79, 20.45), "pristina": ("Pristina", 42.66, 21.16),
 "sarajevo": ("Sarajevo", 43.86, 18.41), "black sea": ("Black Sea", 43.40, 34.30),
 "baltic sea": ("Baltic Sea", 57.00, 19.00), "arctic circle": ("Arctic", 70.00, 20.00),
 # --- Americas ---
 "washington": ("Washington DC", 38.91, -77.04), "pentagon": ("The Pentagon", 38.87, -77.06),
 "white house": ("White House", 38.90, -77.04), "new york": ("New York", 40.71, -74.01),
 "guantanamo": ("Guantánamo Bay", 19.90, -75.15), "diego garcia": ("Diego Garcia", -7.31, 72.41),
 "ottawa": ("Ottawa", 45.42, -75.70), "mexico city": ("Mexico City", 19.43, -99.13),
 "bogota": ("Bogotá", 4.71, -74.07), "bogotá": ("Bogotá", 4.71, -74.07),
 "caracas": ("Caracas", 10.49, -66.88), "essequibo": ("Essequibo", 6.00, -59.00),
 "port-au-prince": ("Port-au-Prince", 18.59, -72.31), "havana": ("Havana", 23.11, -82.37),
 "brasilia": ("Brasília", -15.79, -47.88), "brasília": ("Brasília", -15.79, -47.88),
 "buenos aires": ("Buenos Aires", -34.60, -58.38), "santiago": ("Santiago", -33.45, -70.67),
 "lima": ("Lima", -12.05, -77.04), "quito": ("Quito", -0.18, -78.47),
 "guayaquil": ("Guayaquil", -2.19, -79.89), "tegucigalpa": ("Tegucigalpa", 14.07, -87.19),
 "san salvador": ("San Salvador", 13.69, -89.19), "guatemala city": ("Guatemala City", 14.63, -90.51),
 # --- Oceania ---
 "canberra": ("Canberra", -35.28, 149.13), "darwin": ("Darwin", -12.46, 130.85),
 "wellington": ("Wellington", -41.29, 174.78), "noumea": ("Nouméa", -22.28, 166.46),
 "nouméa": ("Nouméa", -22.28, 166.46), "bougainville": ("Bougainville", -6.20, 155.20),
 "port moresby": ("Port Moresby", -9.44, 147.18), "honiara": ("Honiara", -9.43, 159.95),
}

_AREA_WORDS = ("state", "region", "sea", "strait", "basin", "islands", "gap", "arctic",
               "heights", "tracts", "province", "peninsula", "shoal", "circle")
_AREA_NAMES = {"Darfur", "Tigray", "Amhara", "Donbas", "Crimea", "Kashmir", "Balochistan",
               "Xinjiang", "Tibet", "Sinai", "Papua", "West Papua", "North-east Syria",
               "Puntland", "Nagorno-Karabakh", "Abkhazia", "South Ossetia", "West Bank",
               "Gaza", "Transnistria", "Ituri", "Cabo Delgado", "Mindanao",
               "North Kivu", "South Kivu", "Golan Heights", "Senkaku Islands",
               "South Lebanon", "Line of Control", "Manipur", "Assam", "Gotland", "Okinawa",
               "Guam", "Bougainville", "Kinmen", "Essequibo"}

_AREA_NAMES = {"Darfur", "Tigray", "Amhara", "Donbas", "Crimea", "Kashmir", "Balochistan",
               "Xinjiang", "Tibet", "Sinai", "Papua", "West Papua", "North-east Syria",
               "Puntland", "Nagorno-Karabakh", "Abkhazia", "South Ossetia", "West Bank",
               "Gaza", "Transnistria", "Ituri", "Cabo Delgado", "Mindanao",
               "North Kivu", "South Kivu", "Golan Heights", "Senkaku Islands",
               "South Lebanon", "Line of Control", "Manipur", "Assam", "Gotland", "Okinawa",
               "Guam", "Bougainville", "Kinmen", "Essequibo"}


def _rank(label):
    low = label.lower()
    if label in _AREA_NAMES or any(w in low for w in _AREA_WORDS):
        return 0          # an area
    return 1              # a point: city, base, facility


# Cities where policing is reported. The inherited table is a war gazetteer
# and resolved almost nothing here: Rochdale, Chicago and São Paulo are the
# place names this subject actually produces.
PRECISE.update({
    'abidjan': ('Abidjan', 5.36, -4.01),
    'abuja': ('Abuja', 9.06, 7.5),
    'accra': ('Accra', 5.6, -0.19),
    'addis ababa': ('Addis Ababa', 9.03, 38.74),
    'alexandria': ('Alexandria', 31.2, 29.92),
    'algiers': ('Algiers', 36.75, 3.06),
    'almaty': ('Almaty', 43.24, 76.89),
    'amman': ('Amman', 31.95, 35.93),
    'amsterdam': ('Amsterdam', 52.37, 4.9),
    'ankara': ('Ankara', 39.93, 32.87),
    'atlanta': ('Atlanta', 33.75, -84.39),
    'auckland': ('Auckland', -36.85, 174.76),
    'baghdad': ('Baghdad', 33.31, 44.36),
    'baku': ('Baku', 40.41, 49.87),
    'baltimore': ('Baltimore', 39.29, -76.61),
    'bamako': ('Bamako', 12.64, -8.0),
    'bangkok': ('Bangkok', 13.76, 100.5),
    'barcelona': ('Barcelona', 41.39, 2.17),
    'beijing': ('Beijing', 39.9, 116.41),
    'beirut': ('Beirut', 33.89, 35.5),
    'belfast': ('Belfast', 54.6, -5.93),
    'belgrade': ('Belgrade', 44.79, 20.45),
    'bengaluru': ('Bengaluru', 12.97, 77.59),
    'berlin': ('Berlin', 52.52, 13.4),
    'birmingham': ('Birmingham', 52.49, -1.89),
    'bogota': ('Bogotá', 4.71, -74.07),
    'boston': ('Boston', 42.36, -71.06),
    'brasilia': ('Brasília', -15.79, -47.88),
    'brisbane': ('Brisbane', -27.47, 153.03),
    'bristol': ('Bristol', 51.45, -2.59),
    'brussels': ('Brussels', 50.85, 4.35),
    'bucharest': ('Bucharest', 44.43, 26.1),
    'budapest': ('Budapest', 47.5, 19.04),
    'buenos aires': ('Buenos Aires', -34.6, -58.38),
    'cairo': ('Cairo', 30.04, 31.24),
    'calais': ('Calais', 50.95, 1.86),
    'calgary': ('Calgary', 51.05, -114.07),
    'cali': ('Cali', 3.45, -76.53),
    'california': ('California', 36.78, -119.42),
    'cape town': ('Cape Town', -33.92, 18.42),
    'caracas': ('Caracas', 10.49, -66.88),
    'cardiff': ('Cardiff', 51.48, -3.18),
    'casablanca': ('Casablanca', 33.57, -7.59),
    'ceuta': ('Ceuta', 35.89, -5.32),
    'chennai': ('Chennai', 13.08, 80.27),
    'chicago': ('Chicago', 41.88, -87.63),
    'ciudad juarez': ('Ciudad Juárez', 31.74, -106.49),
    'cleveland': ('Cleveland', 41.5, -81.69),
    'colombo': ('Colombo', 6.93, 79.86),
    'connecticut': ('Connecticut', 41.6, -72.7),
    'copenhagen': ('Copenhagen', 55.68, 12.57),
    'dakar': ('Dakar', 14.72, -17.47),
    'dallas': ('Dallas', 32.78, -96.8),
    'dar es salaam': ('Dar es Salaam', -6.79, 39.21),
    'davao': ('Davao', 7.19, 125.46),
    'delhi': ('Delhi', 28.61, 77.21),
    'denver': ('Denver', 39.74, -104.99),
    'detroit': ('Detroit', 42.33, -83.05),
    'dhaka': ('Dhaka', 23.81, 90.41),
    'doha': ('Doha', 25.29, 51.53),
    'dubai': ('Dubai', 25.2, 55.27),
    'dublin': ('Dublin', 53.35, -6.26),
    'durban': ('Durban', -29.86, 31.02),
    'edinburgh': ('Edinburgh', 55.95, -3.19),
    'ferguson': ('Ferguson, MO', 38.74, -90.31),
    'florida': ('Florida', 27.66, -81.52),
    'frankfurt': ('Frankfurt', 50.11, 8.68),
    'glasgow': ('Glasgow', 55.86, -4.25),
    'guangzhou': ('Guangzhou', 23.13, 113.26),
    'guatemala city': ('Guatemala City', 14.63, -90.51),
    'guerrero': ('Guerrero', 17.55, -99.5),
    'hamburg': ('Hamburg', 53.55, 9.99),
    'hanoi': ('Hanoi', 21.03, 105.85),
    'harare': ('Harare', -17.83, 31.05),
    'helsinki': ('Helsinki', 60.17, 24.94),
    'ho chi minh city': ('Ho Chi Minh City', 10.82, 106.63),
    'hong kong': ('Hong Kong', 22.32, 114.17),
    'houston': ('Houston', 29.76, -95.37),
    'hyderabad': ('Hyderabad', 17.39, 78.49),
    'islamabad': ('Islamabad', 33.68, 73.05),
    'istanbul': ('Istanbul', 41.01, 28.98),
    'jakarta': ('Jakarta', -6.21, 106.85),
    'jerusalem': ('Jerusalem', 31.77, 35.21),
    'johannesburg': ('Johannesburg', -26.2, 28.05),
    'kabul': ('Kabul', 34.53, 69.17),
    'kampala': ('Kampala', 0.35, 32.58),
    'kano': ('Kano', 12.0, 8.52),
    'karachi': ('Karachi', 24.86, 67.01),
    'kathmandu': ('Kathmandu', 27.72, 85.32),
    'kentucky': ('Kentucky', 37.84, -84.27),
    'khartoum': ('Khartoum', 15.5, 32.56),
    'kigali': ('Kigali', -1.94, 30.06),
    'kingston': ('Kingston', 17.98, -76.79),
    'kinshasa': ('Kinshasa', -4.44, 15.27),
    'kolkata': ('Kolkata', 22.57, 88.36),
    'kuala lumpur': ('Kuala Lumpur', 3.14, 101.69),
    'kuwait city': ('Kuwait City', 29.38, 47.99),
    'lagos': ('Lagos', 6.52, 3.38),
    'lahore': ('Lahore', 31.55, 74.34),
    'lampedusa': ('Lampedusa', 35.5, 12.6),
    'leeds': ('Leeds', 53.8, -1.55),
    'lesvos': ('Lesvos', 39.1, 26.55),
    'lima': ('Lima', -12.05, -77.04),
    'lisbon': ('Lisbon', 38.72, -9.14),
    'liverpool': ('Liverpool', 53.41, -2.98),
    'los angeles': ('Los Angeles', 34.05, -118.24),
    'luanda': ('Luanda', -8.84, 13.23),
    'lusaka': ('Lusaka', -15.39, 28.32),
    'lyon': ('Lyon', 45.76, 4.83),
    'madrid': ('Madrid', 40.42, -3.7),
    'manchester': ('Manchester', 53.48, -2.24),
    'manila': ('Manila', 14.6, 120.98),
    'marseille': ('Marseille', 43.3, 5.37),
    'medellin': ('Medellín', 6.24, -75.58),
    'melbourne': ('Melbourne', -37.81, 144.96),
    'melilla': ('Melilla', 35.29, -2.94),
    'memphis': ('Memphis', 35.15, -90.05),
    'mexico city': ('Mexico City', 19.43, -99.13),
    'miami': ('Miami', 25.76, -80.19),
    'michoacan': ('Michoacán', 19.57, -101.71),
    'milan': ('Milan', 45.46, 9.19),
    'minneapolis': ('Minneapolis', 44.98, -93.27),
    'minsk': ('Minsk', 53.9, 27.57),
    'montevideo': ('Montevideo', -34.9, -56.16),
    'montreal': ('Montreal', 45.5, -73.57),
    'moscow': ('Moscow', 55.76, 37.62),
    'mumbai': ('Mumbai', 19.08, 72.88),
    'munich': ('Munich', 48.14, 11.58),
    'naples': ('Naples', 40.85, 14.27),
    'new delhi': ('New Delhi', 28.61, 77.21),
    'new jersey': ('New Jersey', 40.06, -74.41),
    'new orleans': ('New Orleans', 29.95, -90.07),
    'newcastle': ('Newcastle', 54.98, -1.61),
    'nottingham': ('Nottingham', 52.95, -1.15),
    'oakland': ('Oakland', 37.8, -122.27),
    'osaka': ('Osaka', 34.69, 135.5),
    'oslo': ('Oslo', 59.91, 10.75),
    'ottawa': ('Ottawa', 45.42, -75.7),
    'paris': ('Paris', 48.86, 2.35),
    'perth': ('Perth', -31.95, 115.86),
    'philadelphia': ('Philadelphia', 39.95, -75.17),
    'phnom penh': ('Phnom Penh', 11.56, 104.92),
    'phoenix': ('Phoenix', 33.45, -112.07),
    'port-au-prince': ('Port-au-Prince', 18.59, -72.31),
    'portland': ('Portland, OR', 45.52, -122.68),
    'prague': ('Prague', 50.08, 14.44),
    'pretoria': ('Pretoria', -25.75, 28.19),
    'quezon city': ('Quezon City', 14.68, 121.04),
    'quito': ('Quito', -0.18, -78.47),
    'rabat': ('Rabat', 34.02, -6.84),
    'rio de janeiro': ('Rio de Janeiro', -22.91, -43.17),
    'riyadh': ('Riyadh', 24.71, 46.68),
    'rochdale': ('Rochdale', 53.61, -2.16),
    'rome': ('Rome', 41.9, 12.5),
    'rotterdam': ('Rotterdam', 51.92, 4.48),
    'salvador': ('Salvador', -12.97, -38.5),
    'san antonio': ('San Antonio', 29.42, -98.49),
    'san diego': ('San Diego', 32.72, -117.16),
    'san francisco': ('San Francisco', 37.77, -122.42),
    'san salvador': ('San Salvador', 13.69, -89.22),
    'santiago': ('Santiago', -33.45, -70.67),
    'sao paulo': ('São Paulo', -23.55, -46.63),
    'seattle': ('Seattle', 47.61, -122.33),
    'seoul': ('Seoul', 37.57, 126.98),
    'shanghai': ('Shanghai', 31.23, 121.47),
    'sheffield': ('Sheffield', 53.38, -1.47),
    'shenzhen': ('Shenzhen', 22.54, 114.06),
    'singapore': ('Singapore', 1.35, 103.82),
    'sofia': ('Sofia', 42.7, 23.32),
    'st louis': ('St. Louis', 38.63, -90.2),
    'st petersburg': ('St Petersburg', 59.93, 30.34),
    'stockholm': ('Stockholm', 59.33, 18.07),
    'surabaya': ('Surabaya', -7.26, 112.75),
    'sydney': ('Sydney', -33.87, 151.21),
    'taipei': ('Taipei', 25.03, 121.57),
    'tashkent': ('Tashkent', 41.3, 69.24),
    'tbilisi': ('Tbilisi', 41.72, 44.79),
    'tegucigalpa': ('Tegucigalpa', 14.07, -87.19),
    'tehran': ('Tehran', 35.69, 51.39),
    'tel aviv': ('Tel Aviv', 32.09, 34.78),
    'texas': ('Texas', 31.0, -99.0),
    'tokyo': ('Tokyo', 35.68, 139.69),
    'toronto': ('Toronto', 43.65, -79.38),
    'tunis': ('Tunis', 36.81, 10.18),
    'urumqi': ('Ürümqi', 43.83, 87.62),
    'uttar pradesh': ('Uttar Pradesh', 26.85, 80.95),
    'uvalde': ('Uvalde, TX', 29.21, -99.79),
    'vancouver': ('Vancouver', 49.28, -123.12),
    'vienna': ('Vienna', 48.21, 16.37),
    'warsaw': ('Warsaw', 52.23, 21.01),
    'washington dc': ('Washington, DC', 38.91, -77.04),
    'wellington': ('Wellington', -41.29, 174.78),
    'winnipeg': ('Winnipeg', 49.9, -97.14),
    'xinjiang': ('Xinjiang', 41.75, 86.15),
    'yangon': ('Yangon', 16.87, 96.2),
    'yaounde': ('Yaoundé', 3.85, 11.5),
    'yerevan': ('Yerevan', 40.18, 44.51),
    'zagreb': ('Zagreb', 45.81, 15.98),
})


# --------------------------------------------------------------------------
# Subnational units and agencies.
#
# The city list runs out above city level and below country level, so a great
# deal of policy and enforcement reporting placed nowhere: "South Dakota State
# Fair", "FSSAI proposes warning labels", "Health Canada recalls". States,
# provinces and the agencies that stand in for their jurisdiction are added
# here. Existing entries are never overwritten — setdefault only.
# --------------------------------------------------------------------------
_AREA_ADDITIONS = {
    'accc':                    ('Australia', -35.31, 149.13),
    'alabama':                 ('Alabama', 32.81, -86.79),
    'alaska':                  ('Alaska', 61.37, -152.4),
    'alberta':                 ('Alberta', 53.93, -116.58),
    'andalusia':               ('Andalusia', 37.54, -4.73),
    'andhra pradesh':          ('Andhra Pradesh', 15.91, 79.74),
    'anses':                   ('France', 48.86, 2.35),
    'anvisa':                  ('Brazil', -15.79, -47.88),
    'arizona':                 ('Arizona', 33.73, -111.43),
    'arkansas':                ('Arkansas', 34.97, -92.37),
    'baden-wurttemberg':       ('Baden-Württemberg', 48.66, 9.35),
    'bahia':                   ('Bahia', -12.58, -41.7),
    'bavaria':                 ('Bavaria', 48.79, 11.5),
    'bavaria region':          ('Bavaria', 48.79, 11.5),
    'bfr':                     ('Germany', 52.52, 13.4),
    'bihar':                   ('Bihar', 25.1, 85.31),
    'british columbia':        ('British Columbia', 53.73, -127.65),
    'california':              ('California', 36.12, -119.68),
    'catalonia':               ('Catalonia', 41.59, 1.52),
    'cdc':                     ('United States', 33.8, -84.33),
    'cdsco':                   ('India', 28.61, 77.21),
    'cfia':                    ('Canada', 45.42, -75.7),
    'cma':                     ('United Kingdom', 51.5, -0.12),
    'cms':                     ('United States', 38.89, -77.03),
    'cofepris':                ('Mexico', 19.43, -99.13),
    'colorado':                ('Colorado', 39.06, -105.31),
    'connecticut':             ('Connecticut', 41.6, -72.76),
    'delaware':                ('Delaware', 39.32, -75.51),
    'echa':                    ('European Union', 60.17, 24.94),
    'efsa':                    ('European Union', 44.49, 11.34),
    'eiopa':                   ('European Union', 50.11, 8.68),
    'ema':                     ('European Union', 52.34, 4.91),
    'england':                 ('England', 52.36, -1.17),
    'epa':                     ('United States', 38.89, -77.03),
    'european food safety authority': ('European Union', 44.49, 11.34),
    'european medicines agency': ('European Union', 52.34, 4.91),
    'fda':                     ('United States', 38.91, -77.04),
    'flanders':                ('Flanders', 51.03, 4.1),
    'florida':                 ('Florida', 27.77, -81.69),
    'food standards agency':   ('United Kingdom', 51.5, -0.12),
    'fsanz':                   ('Australia', -35.31, 149.13),
    'fsis':                    ('United States', 38.91, -77.04),
    'fssai':                   ('India', 28.61, 77.21),
    'ftc':                     ('United States', 38.9, -77.03),
    'georgia':                 ('Georgia', 33.04, -83.64),
    'guangdong':               ('Guangdong', 23.38, 113.77),
    'gujarat':                 ('Gujarat', 22.26, 71.19),
    'hawaii':                  ('Hawaii', 21.09, -157.5),
    'health canada':           ('Canada', 45.42, -75.7),
    'hokkaido':                ('Hokkaido', 43.22, 142.86),
    'idaho':                   ('Idaho', 44.24, -114.48),
    'illinois':                ('Illinois', 40.35, -88.99),
    'indiana':                 ('Indiana', 39.85, -86.26),
    'iowa':                    ('Iowa', 42.01, -93.21),
    'kansas':                  ('Kansas', 38.53, -96.73),
    'karnataka':               ('Karnataka', 15.32, 75.71),
    'kentucky':                ('Kentucky', 37.67, -84.67),
    'kerala':                  ('Kerala', 10.85, 76.27),
    'lombardy':                ('Lombardy', 45.48, 9.85),
    'louisiana':               ('Louisiana', 31.17, -91.87),
    'maharashtra':             ('Maharashtra', 19.75, 75.71),
    'maine':                   ('Maine', 44.69, -69.38),
    'manitoba':                ('Manitoba', 53.76, -98.81),
    'maryland':                ('Maryland', 39.06, -76.8),
    'massachusetts':           ('Massachusetts', 42.23, -71.53),
    'mato grosso':             ('Mato Grosso', -12.68, -56.92),
    'mfds':                    ('South Korea', 36.48, 127.29),
    'mhlw':                    ('Japan', 35.68, 139.69),
    'mhra':                    ('United Kingdom', 51.5, -0.12),
    'michigan':                ('Michigan', 43.33, -84.54),
    'minas gerais':            ('Minas Gerais', -18.51, -44.55),
    'minnesota':               ('Minnesota', 45.69, -93.9),
    'mississippi':             ('Mississippi', 32.74, -89.68),
    'missouri':                ('Missouri', 38.46, -92.29),
    'montana':                 ('Montana', 46.92, -110.45),
    'nafdac':                  ('Nigeria', 9.06, 7.5),
    'nebraska':                ('Nebraska', 41.13, -98.27),
    'nevada':                  ('Nevada', 38.31, -117.06),
    'new brunswick':           ('New Brunswick', 46.57, -66.46),
    'new hampshire':           ('New Hampshire', 43.45, -71.56),
    'new jersey':              ('New Jersey', 40.3, -74.52),
    'new mexico':              ('New Mexico', 34.84, -106.25),
    'new south wales':         ('New South Wales', -31.25, 146.92),
    'new york state':          ('New York State', 42.17, -74.95),
    'newfoundland':            ('Newfoundland', 53.14, -57.66),
    'nice':                    ('United Kingdom', 53.48, -2.24),
    'nih':                     ('United States', 39.0, -77.1),
    'nmpa':                    ('China', 39.9, 116.4),
    'north carolina':          ('North Carolina', 35.63, -79.81),
    'north dakota':            ('North Dakota', 47.53, -99.78),
    'north rhine-westphalia':  ('North Rhine-Westphalia', 51.43, 7.66),
    'northern ireland':        ('Northern Ireland', 54.79, -6.49),
    'northern territory':      ('Northern Territory', -19.49, 132.55),
    'nova scotia':             ('Nova Scotia', 44.68, -63.74),
    'nvwa':                    ('Netherlands', 52.09, 5.12),
    'ofcom':                   ('United Kingdom', 51.5, -0.12),
    'ofgem':                   ('United Kingdom', 51.5, -0.12),
    'ofsted':                  ('United Kingdom', 51.5, -0.12),
    'ohio':                    ('Ohio', 40.39, -82.76),
    'okinawa':                 ('Okinawa', 26.34, 127.8),
    'oklahoma':                ('Oklahoma', 35.57, -96.93),
    'ontario':                 ('Ontario', 51.25, -85.32),
    'oregon':                  ('Oregon', 44.57, -122.07),
    'osha':                    ('United States', 38.89, -77.03),
    'para':                    ('Pará', -3.79, -52.48),
    'pennsylvania':            ('Pennsylvania', 40.59, -77.21),
    'pmda':                    ('Japan', 35.68, 139.69),
    'punjab':                  ('Punjab', 31.15, 75.34),
    'quebec province':         ('Quebec', 52.94, -73.55),
    'queensland':              ('Queensland', -20.92, 142.7),
    'rajasthan':               ('Rajasthan', 27.02, 74.22),
    'rbi':                     ('India', 19.06, 72.87),
    'rhode island':            ('Rhode Island', 41.68, -71.51),
    'sahpra':                  ('South Africa', -25.75, 28.19),
    'samr':                    ('China', 39.9, 116.4),
    'sao paulo state':         ('São Paulo State', -22.19, -48.79),
    'saskatchewan':            ('Saskatchewan', 52.94, -106.45),
    'saxony':                  ('Saxony', 51.1, 13.2),
    'scotland':                ('Scotland', 56.49, -4.2),
    'sebi':                    ('India', 19.06, 72.87),
    'sec':                     ('United States', 38.89, -77.03),
    'sfda':                    ('Saudi Arabia', 24.71, 46.68),
    'sicily':                  ('Sicily', 37.6, 14.02),
    'south australia':         ('South Australia', -30.0, 136.21),
    'south carolina':          ('South Carolina', 33.86, -80.95),
    'south dakota':            ('South Dakota', 44.3, -99.44),
    'swissmedic':              ('Switzerland', 46.95, 7.45),
    'tamil nadu':              ('Tamil Nadu', 11.13, 78.66),
    'tasmania':                ('Tasmania', -41.64, 146.32),
    'telangana':               ('Telangana', 18.11, 79.02),
    'tennessee':               ('Tennessee', 35.75, -86.69),
    'texas':                   ('Texas', 31.05, -97.56),
    'tga':                     ('Australia', -35.31, 149.13),
    'tibet':                   ('Tibet', 31.15, 88.78),
    'usda':                    ('United States', 38.91, -77.04),
    'utah':                    ('Utah', 40.15, -111.86),
    'uttar pradesh':           ('Uttar Pradesh', 26.85, 80.95),
    'vermont':                 ('Vermont', 44.05, -72.71),
    'victoria state':          ('Victoria', -36.86, 144.28),
    'virginia':                ('Virginia', 37.77, -78.17),
    'wales':                   ('Wales', 52.13, -3.78),
    'wallonia':                ('Wallonia', 50.44, 4.87),
    'washington state':        ('Washington State', 47.4, -121.49),
    'west bengal':             ('West Bengal', 22.99, 87.86),
    'west virginia':           ('West Virginia', 38.49, -80.95),
    'western australia':       ('Western Australia', -25.04, 122.29),
    'wisconsin':               ('Wisconsin', 44.27, -89.62),
    'wyoming':                 ('Wyoming', 42.76, -107.3),
    'xinjiang':                ('Xinjiang', 41.75, 84.9),
}
for _k, _v in _AREA_ADDITIONS.items():
    PRECISE.setdefault(_k, _v)


# --------------------------------------------------------------------------
# Cities missing from the table above. Names that are ordinary English words or
# common surnames are deliberately left out — derby, stoke, mobile, male, van,
# hue, natal, santos, cork, hull, reading — because pinning "the derby" to
# Derby is the same fault as reading a publisher name as geography. Places that
# are genuinely ambiguous between two major cities (cordoba, santa cruz,
# newport, toledo, victoria, springfield, hamilton, newcastle) are left out for
# the same reason: a wrong pin is worse than no pin.
# --------------------------------------------------------------------------
PRECISE.update({
    "zurich":              ("Zurich", 47.38, 8.54),
    "bern":                ("Bern", 46.95, 7.45),
    "basel":               ("Basel", 47.56, 7.59),
    "lausanne":            ("Lausanne", 46.52, 6.63),
    "nyon":                ("Nyon", 46.38, 6.24),
    "monaco":              ("Monaco", 43.74, 7.42),
    "westminster":         ("Westminster", 51.5, -0.13),
    "strasbourg":          ("Strasbourg", 48.57, 7.75),
    "luxembourg":          ("Luxembourg", 49.61, 6.13),
    "hague":               ("The Hague", 52.08, 4.31),
    "antwerp":             ("Antwerp", 51.22, 4.4),
    "utrecht":             ("Utrecht", 52.09, 5.12),
    "porto":               ("Porto", 41.15, -8.61),
    "seville":             ("Seville", 37.39, -5.98),
    "valencia":            ("Valencia", 39.47, -0.38),
    "bilbao":              ("Bilbao", 43.26, -2.93),
    "malaga":              ("Malaga", 36.72, -4.42),
    "turin":               ("Turin", 45.07, 7.69),
    "florence":            ("Florence", 43.77, 11.26),
    "bologna":             ("Bologna", 44.49, 11.34),
    "venice":              ("Venice", 45.44, 12.32),
    "palermo":             ("Palermo", 38.12, 13.36),
    "genoa":               ("Genoa", 44.41, 8.93),
    "toulouse":            ("Toulouse", 43.6, 1.44),
    "bordeaux":            ("Bordeaux", 44.84, -0.58),
    "lille":               ("Lille", 50.63, 3.06),
    "nantes":              ("Nantes", 47.22, -1.55),
    "cologne":             ("Cologne", 50.94, 6.96),
    "stuttgart":           ("Stuttgart", 48.78, 9.18),
    "dusseldorf":          ("Dusseldorf", 51.23, 6.78),
    "dortmund":            ("Dortmund", 51.51, 7.47),
    "leipzig":             ("Leipzig", 51.34, 12.37),
    "bremen":              ("Bremen", 53.08, 8.81),
    "hanover":             ("Hanover", 52.38, 9.73),
    "nuremberg":           ("Nuremberg", 49.45, 11.08),
    "graz":                ("Graz", 47.07, 15.44),
    "salzburg":            ("Salzburg", 47.81, 13.06),
    "innsbruck":           ("Innsbruck", 47.27, 11.39),
    "ljubljana":           ("Ljubljana", 46.06, 14.51),
    "bratislava":          ("Bratislava", 48.15, 17.11),
    "brno":                ("Brno", 49.2, 16.61),
    "krakow":              ("Krakow", 50.06, 19.94),
    "gdansk":              ("Gdansk", 54.35, 18.65),
    "poznan":              ("Poznan", 52.41, 16.93),
    "wroclaw":             ("Wroclaw", 51.11, 17.04),
    "lodz":                ("Lodz", 51.76, 19.46),
    "reykjavik":           ("Reykjavik", 64.15, -21.94),
    "bergen":              ("Bergen", 60.39, 5.32),
    "trondheim":           ("Trondheim", 63.43, 10.39),
    "gothenburg":          ("Gothenburg", 57.71, 11.97),
    "malmo":               ("Malmo", 55.6, 13.0),
    "aarhus":              ("Aarhus", 56.16, 10.2),
    "tampere":             ("Tampere", 61.5, 23.79),
    "turku":               ("Turku", 60.45, 22.27),
    "galway":              ("Galway", 53.27, -9.05),
    "aberdeen":            ("Aberdeen", 57.15, -2.09),
    "dundee":              ("Dundee", 56.46, -2.97),
    "swansea":             ("Swansea", 51.62, -3.94),
    "plymouth":            ("Plymouth", 50.38, -4.14),
    "brighton":            ("Brighton", 50.82, -0.14),
    "southampton":         ("Southampton", 50.91, -1.4),
    "coventry":            ("Coventry", 52.41, -1.51),
    "luton":               ("Luton", 51.88, -0.42),
    "tijuana":             ("Tijuana", 32.51, -117.04),
    "cancun":              ("Cancun", 21.16, -86.85),
    "merida":              ("Merida", 20.97, -89.62),
    "toluca":              ("Toluca", 19.29, -99.66),
    "queretaro":           ("Queretaro", 20.59, -100.39),
    "rosario":             ("Rosario", -32.95, -60.66),
    "mendoza":             ("Mendoza", -32.89, -68.84),
    "salta":               ("Salta", -24.79, -65.41),
    "valparaiso":          ("Valparaiso", -33.05, -71.61),
    "concepcion":          ("Concepcion", -36.83, -73.05),
    "cartagena":           ("Cartagena", 10.39, -75.51),
    "barranquilla":        ("Barranquilla", 10.97, -74.8),
    "cuenca":              ("Cuenca", -2.9, -79.0),
    "arequipa":            ("Arequipa", -16.41, -71.54),
    "cusco":               ("Cusco", -13.53, -71.97),
    "cochabamba":          ("Cochabamba", -17.39, -66.16),
    "asuncion":            ("Asuncion", -25.26, -57.58),
    "ciudad del este":     ("Ciudad del Este", -25.51, -54.61),
    "fortaleza":           ("Fortaleza", -3.73, -38.53),
    "curitiba":            ("Curitiba", -25.43, -49.27),
    "manaus":              ("Manaus", -3.12, -60.02),
    "belem":               ("Belem", -1.46, -48.5),
    "goiania":             ("Goiania", -16.69, -49.26),
    "maceio":              ("Maceio", -9.65, -35.73),
    "campinas":            ("Campinas", -22.91, -47.06),
    "niteroi":             ("Niteroi", -22.88, -43.1),
    "ibadan":              ("Ibadan", 7.38, 3.9),
    "port harcourt":       ("Port Harcourt", 4.82, 7.03),
    "kumasi":              ("Kumasi", 6.69, -1.62),
    "tamale":              ("Tamale", 9.4, -0.84),
    "bouake":              ("Bouake", 7.69, -5.03),
    "thies":               ("Thies", 14.79, -16.93),
    "conakry":             ("Conakry", 9.64, -13.58),
    "freetown":            ("Freetown", 8.48, -13.23),
    "monrovia":            ("Monrovia", 6.3, -10.8),
    "lome":                ("Lome", 6.13, 1.22),
    "cotonou":             ("Cotonou", 6.37, 2.42),
    "douala":              ("Douala", 4.05, 9.77),
    "libreville":          ("Libreville", 0.42, 9.47),
    "brazzaville":         ("Brazzaville", -4.26, 15.28),
    "kisangani":           ("Kisangani", 0.52, 25.2),
    "lubumbashi":          ("Lubumbashi", -11.67, 27.48),
    "gulu":                ("Gulu", 2.77, 32.3),
    "mombasa":             ("Mombasa", -4.04, 39.67),
    "kisumu":              ("Kisumu", -0.09, 34.77),
    "arusha":              ("Arusha", -3.37, 36.68),
    "mwanza":              ("Mwanza", -2.52, 32.9),
    "dodoma":              ("Dodoma", -6.16, 35.75),
    "zanzibar":            ("Zanzibar", -6.16, 39.2),
    "bujumbura":           ("Bujumbura", -3.38, 29.36),
    "hargeisa":            ("Hargeisa", 9.56, 44.07),
    "djibouti":            ("Djibouti", 11.59, 43.15),
    "bahir dar":           ("Bahir Dar", 11.59, 37.39),
    "misrata":             ("Misrata", 32.38, 15.09),
    "sfax":                ("Sfax", 34.74, 10.76),
    "sousse":              ("Sousse", 35.83, 10.64),
    "oran":                ("Oran", 35.7, -0.63),
    "constantine":         ("Constantine", 36.36, 6.61),
    "annaba":              ("Annaba", 36.9, 7.77),
    "tangier":             ("Tangier", 35.76, -5.83),
    "fez":                 ("Fez", 34.02, -5.0),
    "marrakech":           ("Marrakech", 31.63, -8.01),
    "agadir":              ("Agadir", 30.42, -9.6),
    "oujda":               ("Oujda", 34.68, -1.91),
    "bloemfontein":        ("Bloemfontein", -29.09, 26.16),
    "gqeberha":            ("Gqeberha", -33.96, 25.6),
    "port elizabeth":      ("Gqeberha", -33.96, 25.6),
    "polokwane":           ("Polokwane", -23.9, 29.45),
    "windhoek":            ("Windhoek", -22.56, 17.08),
    "gaborone":            ("Gaborone", -24.65, 25.91),
    "bulawayo":            ("Bulawayo", -20.15, 28.58),
    "ndola":               ("Ndola", -12.97, 28.64),
    "blantyre":            ("Blantyre", -15.79, 35.01),
    "beira":               ("Beira", -19.84, 34.84),
    "nampula":             ("Nampula", -15.12, 39.27),
    "antananarivo":        ("Antananarivo", -18.88, 47.51),
    "port louis":          ("Port Louis", -20.16, 57.5),
    "mecca":               ("Mecca", 21.39, 39.86),
    "medina":              ("Medina", 24.47, 39.61),
    "dammam":              ("Dammam", 26.43, 50.1),
    "sharjah":             ("Sharjah", 25.35, 55.39),
    "najaf":               ("Najaf", 32.03, 44.34),
    "karbala":             ("Karbala", 32.61, 44.02),
    "sidon":               ("Sidon", 33.56, 35.37),
    "irbid":               ("Irbid", 32.55, 35.85),
    "beersheba":           ("Beersheba", 31.25, 34.79),
    "izmir":               ("Izmir", 38.42, 27.14),
    "bursa":               ("Bursa", 40.19, 29.06),
    "adana":               ("Adana", 37.0, 35.32),
    "gaziantep":           ("Gaziantep", 37.07, 37.38),
    "konya":               ("Konya", 37.87, 32.48),
    "antalya":             ("Antalya", 36.9, 30.7),
    "trabzon":             ("Trabzon", 41.0, 39.72),
    "tabriz":              ("Tabriz", 38.08, 46.29),
    "shiraz":              ("Shiraz", 29.59, 52.58),
    "mashhad":             ("Mashhad", 36.3, 59.61),
    "ahvaz":               ("Ahvaz", 31.32, 48.67),
    "qom":                 ("Qom", 34.64, 50.88),
    "karaj":               ("Karaj", 35.84, 50.94),
    "mazar-i-sharif":      ("Mazar-i-Sharif", 36.71, 67.11),
    "multan":              ("Multan", 30.16, 71.52),
    "faisalabad":          ("Faisalabad", 31.42, 73.08),
    "sialkot":             ("Sialkot", 32.49, 74.53),
    "chittagong":          ("Chittagong", 22.36, 91.78),
    "sylhet":              ("Sylhet", 24.9, 91.87),
    "khulna":              ("Khulna", 22.82, 89.55),
    "rajshahi":            ("Rajshahi", 24.37, 88.6),
    "pokhara":             ("Pokhara", 28.21, 83.99),
    "biratnagar":          ("Biratnagar", 26.45, 87.28),
    "galle":               ("Galle", 6.03, 80.22),
    "jaffna":              ("Jaffna", 9.66, 80.02),
    "kandy":               ("Kandy", 7.29, 80.64),
    "thimphu":             ("Thimphu", 27.47, 89.64),
    "ulaanbaatar":         ("Ulaanbaatar", 47.89, 106.91),
    "bishkek":             ("Bishkek", 42.87, 74.59),
    "khujand":             ("Khujand", 40.28, 69.62),
    "samarkand":           ("Samarkand", 39.65, 66.98),
    "bukhara":             ("Bukhara", 39.77, 64.42),
    "nukus":               ("Nukus", 42.46, 59.6),
    "aktobe":              ("Aktobe", 50.28, 57.17),
    "shymkent":            ("Shymkent", 42.32, 69.59),
    "karaganda":           ("Karaganda", 49.81, 73.09),
    "atyrau":              ("Atyrau", 47.09, 51.92),
    "ganja":               ("Ganja", 40.68, 46.36),
    "gyumri":              ("Gyumri", 40.79, 43.85),
    "batumi":              ("Batumi", 41.64, 41.64),
    "chengdu":             ("Chengdu", 30.57, 104.07),
    "chongqing":           ("Chongqing", 29.56, 106.55),
    "xian":                ("Xian", 34.34, 108.94),
    "wuhan":               ("Wuhan", 30.59, 114.31),
    "nanjing":             ("Nanjing", 32.06, 118.8),
    "hangzhou":            ("Hangzhou", 30.27, 120.15),
    "suzhou":              ("Suzhou", 31.3, 120.62),
    "qingdao":             ("Qingdao", 36.07, 120.38),
    "dalian":              ("Dalian", 38.91, 121.61),
    "shenyang":            ("Shenyang", 41.81, 123.43),
    "harbin":              ("Harbin", 45.8, 126.53),
    "kunming":             ("Kunming", 25.04, 102.71),
    "lhasa":               ("Lhasa", 29.65, 91.11),
    "xiamen":              ("Xiamen", 24.48, 118.09),
    "fuzhou":              ("Fuzhou", 26.07, 119.3),
    "changsha":            ("Changsha", 28.23, 112.94),
    "zhengzhou":           ("Zhengzhou", 34.75, 113.63),
    "jinan":               ("Jinan", 36.65, 117.12),
    "tianjin":             ("Tianjin", 39.13, 117.2),
    "kaohsiung":           ("Kaohsiung", 22.63, 120.3),
    "tainan":              ("Tainan", 22.99, 120.21),
    "taichung":            ("Taichung", 24.15, 120.68),
    "busan":               ("Busan", 35.18, 129.08),
    "incheon":             ("Incheon", 37.46, 126.71),
    "daegu":               ("Daegu", 35.87, 128.6),
    "gwangju":             ("Gwangju", 35.16, 126.85),
    "daejeon":             ("Daejeon", 36.35, 127.38),
    "kyoto":               ("Kyoto", 35.01, 135.77),
    "nagoya":              ("Nagoya", 35.18, 136.91),
    "fukuoka":             ("Fukuoka", 33.59, 130.4),
    "sapporo":             ("Sapporo", 43.06, 141.35),
    "sendai":              ("Sendai", 38.27, 140.87),
    "hiroshima":           ("Hiroshima", 34.39, 132.46),
    "kobe":                ("Kobe", 34.69, 135.2),
    "yokohama":            ("Yokohama", 35.44, 139.64),
    "cebu":                ("Cebu", 10.32, 123.89),
    "zamboanga":           ("Zamboanga", 6.92, 122.08),
    "iloilo":              ("Iloilo", 10.72, 122.56),
    "bandung":             ("Bandung", -6.92, 107.61),
    "medan":               ("Medan", 3.6, 98.68),
    "makassar":            ("Makassar", -5.15, 119.43),
    "semarang":            ("Semarang", -6.97, 110.42),
    "palembang":           ("Palembang", -2.98, 104.76),
    "denpasar":            ("Denpasar", -8.65, 115.22),
    "yogyakarta":          ("Yogyakarta", -7.8, 110.36),
    "penang":              ("Penang", 5.41, 100.34),
    "johor bahru":         ("Johor Bahru", 1.49, 103.74),
    "kuching":             ("Kuching", 1.55, 110.35),
    "kota kinabalu":       ("Kota Kinabalu", 5.98, 116.07),
    "ipoh":                ("Ipoh", 4.6, 101.09),
    "chiang mai":          ("Chiang Mai", 18.79, 98.99),
    "phuket":              ("Phuket", 7.88, 98.39),
    "pattaya":             ("Pattaya", 12.93, 100.88),
    "hat yai":             ("Hat Yai", 7.01, 100.47),
    "da nang":             ("Da Nang", 16.05, 108.2),
    "hai phong":           ("Hai Phong", 20.86, 106.68),
    "can tho":             ("Can Tho", 10.05, 105.75),
    "mandalay":            ("Mandalay", 21.98, 96.08),
    "siem reap":           ("Siem Reap", 13.36, 103.86),
    "vientiane":           ("Vientiane", 17.97, 102.63),
    "luang prabang":       ("Luang Prabang", 19.89, 102.14),
    "dili":                ("Dili", -8.56, 125.56),
    "adelaide":            ("Adelaide", -34.93, 138.6),
    "hobart":              ("Hobart", -42.88, 147.33),
    "wollongong":          ("Wollongong", -34.42, 150.89),
    "cairns":              ("Cairns", -16.92, 145.77),
    "townsville":          ("Townsville", -19.26, 146.82),
    "christchurch":        ("Christchurch", -43.53, 172.64),
    "dunedin":             ("Dunedin", -45.87, 170.5),
    "suva":                ("Suva", -18.14, 178.44),
    "apia":                ("Apia", -13.83, -171.77),
    "honolulu":            ("Honolulu", 21.31, -157.86),
    "anchorage":           ("Anchorage", 61.22, -149.9),
    "fairbanks":           ("Fairbanks", 64.84, -147.72),
    "juneau":              ("Juneau", 58.3, -134.42),
    "reno":                ("Reno", 39.53, -119.81),
    "boise":               ("Boise", 43.62, -116.21),
    "spokane":             ("Spokane", 47.66, -117.43),
    "tucson":              ("Tucson", 32.22, -110.97),
    "albuquerque":         ("Albuquerque", 35.08, -106.65),
    "omaha":               ("Omaha", 41.26, -95.93),
    "wichita":             ("Wichita", 37.69, -97.34),
    "tulsa":               ("Tulsa", 36.15, -95.99),
    "little rock":         ("Little Rock", 34.75, -92.29),
    "shreveport":          ("Shreveport", 32.53, -93.75),
    "chattanooga":         ("Chattanooga", 35.05, -85.31),
    "knoxville":           ("Knoxville", 35.96, -83.92),
    "lexington":           ("Lexington", 38.04, -84.5),
    "dayton":              ("Dayton", 39.76, -84.19),
    "akron":               ("Akron", 41.08, -81.52),
    "syracuse":            ("Syracuse", 43.05, -76.15),
    "albany":              ("Albany", 42.65, -73.76),
    "hartford":            ("Hartford", 41.76, -72.69),
    "providence":          ("Providence", 41.82, -71.41),
    "worcester":           ("Worcester", 42.26, -71.8),
    "halifax":             ("Halifax", 44.65, -63.58),
    "saskatoon":           ("Saskatoon", 52.13, -106.67),
    "regina":              ("Regina", 50.45, -104.62),
    "whitehorse":          ("Whitehorse", 60.72, -135.05),
    "yellowknife":         ("Yellowknife", 62.45, -114.37),
    "iqaluit":             ("Iqaluit", 63.75, -68.52),
})

PRECISE_C = sorted(
    ((term, label, lat, lon, _compile(term), _rank(label))
     for term, (label, lat, lon) in PRECISE.items()),
    key=lambda row: (-row[5], -len(row[0])))   # points before areas, longest term first

LOCATIVE = [
 " in ", " im ", " en ", " au ", " aux ", " a ", " à ", " al ", " nel ", " nella ",
 " on ", " over ", " near ", " into ", " inside ", " across ", " throughout ",
 " sur ", " dans ", " van ", " naar ", " uit ", " w ", " na ", " do ", " em ", " no ",
 " v ", " в ", " на ", " у ", " до ", " στη", " στο", " την ", " στην ",
 "في ", "ب", "ל", "ב", "ที่", "ใน", "在", "で", "へ", "에서", "로",
]
_LOC_MAX = 12          # how far back to look for the marker


def _first_pos(text, compiled):
    """Where a place's terms first appear in the text, or None."""
    best = None
    for c in compiled:
        if isinstance(c, str):
            i = text.find(c)
        else:
            mo = c.search(text)
            i = mo.start() if mo else -1
        if i >= 0 and (best is None or i < best):
            best = i
    return best

def _is_scene(text, pos):
    """True when the name at this position is preceded by a locative marker."""
    if pos is None:
        return False
    window = text[max(0, pos - _LOC_MAX):pos]
    return any(mark in window for mark in LOCATIVE)

def precise_for(text):
    """A city, province, base or waterway named in the story. Checked before the
    country layer so a headline about Kharkiv is pinned on Kharkiv rather than
    the middle of Ukraine. Longest term wins."""
    for term, label, lat, lon, rx, _rk in PRECISE_C:
        if hit(text, [rx]):
            return label, [lat, lon]
    return None, None

def scene_first(text, places):
    """Reorder matched places so any marked as the scene of the story lead."""
    if len(places) < 2:
        return places
    terms = {}
    for _rid, _rl, sublist in GEO3_C:
        for _sid, _sl, plist in sublist:
            for pid, _pl, compiled in plist:
                if pid in places:
                    terms[pid] = compiled
    scene, rest = [], []
    for pid in places:
        (scene if _is_scene(text, _first_pos(text, terms.get(pid, []))) else rest).append(pid)
    return scene + rest

def point_for(text, places, subs, regions):
    """The most specific point a story resolved to: a named sub-national place
    if there is one, otherwise the country, otherwise the subregion or region.
    Returns (label_or_None, point_or_None)."""
    label, point = precise_for(text)
    if point:
        return label, point
    places = scene_first(text, places)
    for level in (places, subs, regions):
        for pid in level:
            if pid in COORDS:
                return None, COORDS[pid]
    return None, None


def load_sources():
    with open(SOURCES_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    srcs = []
    for s in cfg.get("direct", []):
        srcs.append({"name": s["name"], "lang": s["lang"], "standing": s["standing"],
                     "region": s["standing"], "kind": s.get("kind", "news"), "url": s["url"]})
    for block, prefix in (("gnews", "Google News · "), ("events", "Events · ")):
        for loc in cfg.get(block, []):
            srcs.append({"name": prefix + loc["label"], "lang": loc["lang"],
                         "standing": loc["standing"], "region": loc["standing"],
                         "kind": "news", "url": build_gnews_url(loc),
                         "query": loc.get("query", "")})
    return srcs, cfg


def run(dry_run=False, fixtures=None):
    sources, cfg = load_sources()
    print("Reading %d wires…" % len(sources))

    def read(src):
        if fixtures:
            path = os.path.join(fixtures, re.sub(r"[^\w.-]", "_", src["name"]) + ".xml")
            if not os.path.exists(path):
                return src, None
            with open(path, "rb") as fh:
                return src, fh.read()
        return src, fetch(src["url"])

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for src, raw in pool.map(read, sources):
            results.append((src, raw))

    previous = []
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as fh:
                previous = json.load(fh).get("items", [])
        except Exception:  # noqa: BLE001
            previous = []

    seen_fp, seen_url, items = set(), set(), []

    def absorb(row):
        fp = fingerprint(row["t"])
        cu = canon_url(row["u"])
        if fp in seen_fp or cu in seen_url:
            return False
        seen_fp.add(fp)
        seen_url.add(cu)
        items.append(row)
        return True

    stats, ok_count, refused = [], 0, 0
    for src, raw in results:
        stat = {"name": src["name"], "lang": src["lang"], "standing": src["standing"],
                "region": src["standing"], "kept": 0, "refused": 0, "ok": False}
        if raw:
            stat["ok"] = True
            ok_count += 1
            for row in parse_feed(raw, src):
                text = (row["t"] + " " + row["s"]).lower()
                if hit(text, BLOCK_C):
                    stat["refused"] += 1
                    refused += 1
                    continue
                if not relevant(text):
                    stat["refused"] += 1
                    refused += 1
                    continue
                regions, subs, places = places_for(text)
                total, reasons = weight(text, src["standing"], regions != ["unlocated"])
                row["x"] = topics_for(text)
                row["w"] = regions
                row["sr"] = subs
                row["pl"] = places
                row["pn"], row["ll"] = point_for(text, places, subs, regions)
                row["p"] = total
                row["y"] = reasons
                row["st"] = src["standing"]
                if absorb(row):
                    stat["kept"] += 1
        stats.append(stat)
        print("  %-36s %s" % (src["name"][:36],
                              "unreachable" if not raw
                              else "%d kept, %d refused" % (stat["kept"], stat["refused"])))

    fresh_urls = {canon_url(i["u"]) for i in items}
    for row in previous:
        if "x" in row:
            absorb(row)

    cutoff = int(time.time() * 1000) - RETAIN_DAYS * 86400000
    items = [i for i in items if (i.get("d") or cutoff + 1) >= cutoff]
    items.sort(key=lambda i: i.get("d") or 0, reverse=True)
    items = items[:MAX_ITEMS]
    fresh = sum(1 for i in items if canon_url(i["u"]) in fresh_urls)

    languages = {}
    for loc in cfg.get("gnews", []):
        languages.setdefault(loc["lang"], re.sub(r"\s*·.*$|\s*\(.*$|\s+\d+$", "", loc["label"]).strip())
    languages.setdefault("en", "English")

    by_standing = {}
    for i in items:
        by_standing[i["st"]] = by_standing.get(i["st"], 0) + 1

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {"stories": len(items), "new_this_run": fresh,
                   "languages": len({i["g"] for i in items}),
                   "notable": sum(1 for i in items if i.get("p", 0) >= NOTABLE_SCORE),
                   "refused": refused,
                   "by_standing": by_standing,
                   "wires_ok": ok_count, "wires_total": len(sources)},
        "notable_score": NOTABLE_SCORE,
        "languages": languages,
        "standings": [
            {"id": "official", "label": "Regulators, courts & governing bodies"},
            {"id": "research", "label": "Research & integrity monitoring"},
            {"id": "press", "label": "Press"},
            {"id": "rights", "label": "Athlete, fan & welfare organisations"},
        ],
        "topics": [{"id": tid, "label": label} for tid, label, _ in TOPICS],
        "coords": COORDS,
        "geo": ([{"id": rid, "label": rlabel,
                  "subs": [{"id": sid, "label": slabel,
                            "places": [{"id": pid, "label": plabel} for pid, plabel, _t in places]}
                           for sid, slabel, places in subs]}
                 for rid, rlabel, subs in GEO3] +
                [{"id": "unlocated", "label": "No single region", "subs": []}]),
        "sources": stats,
        "items": items,
    }

    print("\n%d stories (%d new, %d consequential) · %d refused · %d languages · %d/%d wires answered"
          % (len(items), fresh, payload["counts"]["notable"], refused,
             payload["counts"]["languages"], ok_count, len(sources)))
    if by_standing:
        print("By standing: " + ", ".join("%s %d" % (k, v) for k, v in sorted(by_standing.items())))

    if dry_run:
        print("\n--dry-run: wire_sports.json not written")
        return payload

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print("Wrote %s (%.0f KB)" % (OUT_PATH, os.path.getsize(OUT_PATH) / 1024))
    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixtures")
    args = ap.parse_args()
    run(dry_run=args.dry_run, fixtures=args.fixtures)
