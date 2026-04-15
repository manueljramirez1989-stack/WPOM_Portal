"""
wpom_scraper.py
Scrapes wrestling transfer portal data from public sources.
Looks for: new commitments, new portal entries, photo URLs from Sidearm schools.
Outputs: portal_updates.json
"""

import json
import re
import time
import urllib.request
import urllib.error
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# Sidearm CDN schools — direct photo URL extraction works on these
SIDEARM_SCHOOLS = {
    'NC State':   'https://gopack.com/sports/wrestling/roster',
    'Duke':       'https://goduke.com/sports/wrestling/roster',
    'Cornell':    'https://cornellbigred.com/sports/wrestling/roster',
    'Maryland':   'https://umterps.com/sports/wrestling/roster',
    'Virginia':   'https://virginiasports.com/sports/wrestling/roster',
    'Pitt':       'https://pittsburghpanthers.com/sports/wrestling/roster',
    'Lehigh':     'https://lehighsports.com/sports/wrestling/roster',
    'Penn':       'https://pennathletics.com/sports/wrestling/roster',
}

# Known athlete->school mappings for targeted photo pulls
ATHLETE_SCHOOLS = {
    'V. Robinson':    ('NC State',  'vincent-robinson'),
    'Luca Felix':     ('NC State',  'luca-felix'),
    'Daniel Zepeda':  ('NC State',  'daniel-zepeda'),
    'Connor Barket':  ('Duke',      'connor-barket'),
    'Aidan Wallace':  ('Duke',      'aidan-wallace'),
    'Meyer Shapiro':  ('Cornell',   'meyer-shapiro'),
    'Branson John':   ('Maryland',  'branson-john'),
}

SIDEARM_ROSTER_URLS = {
    'NC State':  'https://gopack.com/sports/wrestling/roster/{slug}',
    'Duke':      'https://goduke.com/sports/wrestling/roster/{slug}',
    'Cornell':   'https://cornellbigred.com/sports/wrestling/roster/{slug}',
    'Maryland':  'https://umterps.com/sports/wrestling/roster/{slug}',
}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def fetch(url, timeout=15):
    """Fetch a URL, return text or None."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"  FETCH ERROR {url[:60]}: {e}")
        return None


def fetch_json(url, timeout=15):
    """Fetch URL, parse as JSON."""
    text = fetch(url, timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


# ── PHOTO SCRAPING ────────────────────────────────────────────────────────────

CDN_BASE = 'https://dxbhsrqyrr690.cloudfront.net/sidearm.nextgen.sites/'

SCHOOL_CDN = {
    'NC State': 'gopack.com',
    'Duke':     'goduke.com',
    'Cornell':  'cornellbigred.com',
    'Maryland': 'umterps.com',
    'Virginia': 'virginiasports.com',
    'Pitt':     'pittsburghpanthers.com',
    'Lehigh':   'lehighsports.com',
}

def get_player_photo(school, slug):
    """
    Fetch a player's roster page and extract the best action shot URL.
    Works for Sidearm-platform schools.
    """
    base_urls = {
        'NC State': f'https://gopack.com/sports/wrestling/roster/{slug}',
        'Duke':     f'https://goduke.com/sports/wrestling/roster/{slug}',
        'Cornell':  f'https://cornellbigred.com/sports/wrestling/roster/{slug}',
        'Maryland': f'https://umterps.com/sports/wrestling/roster/{slug}',
    }

    url = base_urls.get(school)
    if not url:
        return None

    cdn_domain = SCHOOL_CDN.get(school)
    if not cdn_domain:
        return None

    html = fetch(url)
    if not html:
        return None

    # Find all CDN image URLs — prefer action shots (16x9 ratio) over headshots
    pattern = rf'https://dxbhsrqyrr690\.cloudfront\.net/sidearm\.nextgen\.sites/{re.escape(cdn_domain)}/images/[^\s"\'\\]+'
    matches = re.findall(pattern, html)

    if not matches:
        # Try the images.sidearmdev.com resize format
        resize_pattern = r'https://images\.sidearmdev\.com/(?:resize|crop)\?url=([^&"\'\\]+)'
        raw_matches = re.findall(resize_pattern, html)
        for raw in raw_matches:
            decoded = urllib.parse.unquote(raw)
            if cdn_domain in decoded:
                matches.append(decoded)

    if not matches:
        return None

    # Prefer 16x9 action shots (recent year) over headshots
    action_shots = [m for m in matches if '16x9' in m or 'action' in m.lower()]
    headshots = [m for m in matches if 'HS' in m or 'head' in m.lower() or 'crop' in m]

    # Sort by recency (higher year = more recent)
    def year_score(url):
        m = re.search(r'/(\d{4})/', url)
        return int(m.group(1)) if m else 0

    if action_shots:
        return sorted(action_shots, key=year_score, reverse=True)[0]
    elif matches:
        return sorted(matches, key=year_score, reverse=True)[0]
    return None


def scrape_known_photos():
    """Pull photos for all athletes with known school/slug mappings."""
    photos = {}
    print("\n── Scraping photos from Sidearm schools ──")
    for name, (school, slug) in ATHLETE_SCHOOLS.items():
        print(f"  {name} ({school})...")
        url = get_player_photo(school, slug)
        if url:
            photos[name] = url
            print(f"    ✓ {url[-50:]}")
        else:
            print(f"    ✗ not found")
        time.sleep(1.5)  # be polite
    return photos


# ── PORTAL COMMITMENT SCRAPING ────────────────────────────────────────────────

# On3 transfer portal API (public)
ON3_PORTAL_URL = 'https://www.on3.com/transfer-portal/sport/wrestling/'

# InterMat portal page
INTERMAT_PORTAL_URL = 'https://intermat.com/transfer-portal/'

# FloWrestling portal (requires subscription for full data, but headlines are public)
FLO_SEARCH_URL = 'https://www.flowrestling.org/articles?q=transfer+portal+committed'


def scrape_on3_commitments():
    """
    Scrape On3 transfer portal for wrestling commitments.
    On3 embeds JSON data in their page's __NEXT_DATA__ script tag.
    """
    print("\n── Scraping On3 portal ──")
    html = fetch(ON3_PORTAL_URL)
    if not html:
        return []

    # Extract __NEXT_DATA__ JSON
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        print("  No __NEXT_DATA__ found")
        return []

    try:
        data = json.loads(m.group(1))
        # Navigate to portal entries
        props = data.get('props', {}).get('pageProps', {})
        entries = props.get('transferPortalAthletes', props.get('athletes', []))
        if not entries:
            # Try nested
            for key in props:
                if isinstance(props[key], list) and len(props[key]) > 0:
                    if isinstance(props[key][0], dict) and 'name' in str(props[key][0]):
                        entries = props[key]
                        break

        commitments = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            name = e.get('name') or e.get('athleteName', '')
            school = e.get('commitSchool') or e.get('destination', '')
            sport = str(e.get('sport', '')).lower()
            if 'wrestl' not in sport and sport:
                continue
            if name and school:
                commitments.append({
                    'name': name,
                    'committed_to': school,
                    'source': 'on3',
                    'scraped_at': datetime.utcnow().isoformat()
                })

        print(f"  Found {len(commitments)} commitments on On3")
        return commitments

    except Exception as e:
        print(f"  On3 parse error: {e}")
        return []


def scrape_intermat_portal():
    """
    Scrape InterMat transfer portal page for wrestling entries.
    """
    print("\n── Scraping InterMat portal ──")
    html = fetch(INTERMAT_PORTAL_URL)
    if not html:
        return []

    commitments = []

    # InterMat lists entries in a table — look for wrestler names + destination
    # Pattern: name, weight, from school, to school
    rows = re.findall(
        r'<tr[^>]*>(.*?)</tr>',
        html, re.DOTALL
    )

    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if len(cells) >= 4:
            name = cells[0]
            # Basic validation — names have at least first + last
            if len(name.split()) >= 2 and any(c.isalpha() for c in name):
                entry = {
                    'name': name,
                    'weight': cells[1] if len(cells) > 1 else '',
                    'from_school': cells[2] if len(cells) > 2 else '',
                    'committed_to': cells[3] if len(cells) > 3 else '',
                    'source': 'intermat',
                    'scraped_at': datetime.utcnow().isoformat()
                }
                commitments.append(entry)

    print(f"  Found {len(commitments)} entries on InterMat")
    return commitments


def search_flowrestling_news():
    """
    Search FloWrestling headlines for commitment announcements.
    Returns list of {name, committed_to, headline} dicts.
    """
    print("\n── Searching FloWrestling headlines ──")
    html = fetch(FLO_SEARCH_URL)
    if not html:
        return []

    commitments = []

    # Look for commitment patterns in headlines
    # Common: "John Smith commits to Iowa" / "Smith transfers to Penn State"
    headlines = re.findall(r'(?:commits? to|transfers? to|announces? commitment to|will transfer to)[^<"]{5,60}', html, re.IGNORECASE)

    for h in headlines[:20]:
        school_match = re.search(r'(?:commits? to|transfers? to|will transfer to)\s+(.{5,40}?)(?:\s*[<".,]|$)', h, re.IGNORECASE)
        if school_match:
            commitments.append({
                'school_mentioned': school_match.group(1).strip(),
                'context': h.strip(),
                'source': 'flowrestling',
                'scraped_at': datetime.utcnow().isoformat()
            })

    print(f"  Found {len(commitments)} commitment mentions on FloWrestling")
    return commitments


# ── KNOWN ATHLETES COMMITMENT CHECK ──────────────────────────────────────────

# Current portal athletes tracked in WPOM — check these by name
TRACKED_ATHLETES = [
    # 125
    "V. Robinson", "Nico Provo", "Luke Lilledahl", "Marc-Anthony McGowan",
    "Jore Volk", "Tyler Klinsky",
    # 133
    "Tyler Knox", "Jax Forrest", "Ben Davino", "Aaron Seidel",
    "Marcus Blaze", "Drake Ayala", "Jacob Van Dee",
    # 141
    "Jack Consiglio", "Sergio Vega", "Jesse Mendez", "Carter Bailey",
    "Nasir Bailey", "Elijah Griffin",
    # 149
    "Aden Valencia", "Daniel Zepeda", "Shayne Van Ness", "Jaxon Joy",
    "Ryan Crookham", "Zan Fugitt", "Jayden Scott",
    # 157
    "Meyer Shapiro", "Landon Robideau", "Antrell Taylor", "Ty Watters",
    "Kannon Webster", "Cameron Catrabone",
    # 165
    "Bryce Hepner", "Mitchell Mesenbrink", "Mikey Caliendo",
    "G. Arnold", "Jordan Williams",
    # 174
    "Levi Haines", "Christopher Minto", "Dom Solis", "Aidan Wallace",
    "Aurelius Dunbar", "TJ Matrisciano",
    # 184
    "TJ Stewart", "B. Berge", "Jaxon Smith", "Dylan Ross",
    "Jake Dailey", "B. McCrone",
    # 197
    "Massoma Endene", "Branson John", "Connor Barket", "A. Posada",
    "Brett Ungar", "Luca Felix",
    # 285
    "Connor Barket", "Bennett Tabor", "Tyler Hicks", "Isaac Trumble",
]


def check_athlete_commitments(commitments_list):
    """
    Cross-reference scraped commitments against our tracked athletes.
    Returns list of confirmed matches.
    """
    confirmed = []
    for commitment in commitments_list:
        name = commitment.get('name', '')
        for tracked in TRACKED_ATHLETES:
            # Fuzzy match — last name + first initial
            tracked_parts = tracked.split()
            name_parts = name.split()
            if len(tracked_parts) >= 2 and len(name_parts) >= 2:
                # Check last name match
                if tracked_parts[-1].lower() == name_parts[-1].lower():
                    commitment['wpom_match'] = tracked
                    confirmed.append(commitment)
                    break
    return confirmed


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run_scraper():
    print(f"\n{'='*60}")
    print(f"WPOM Scraper — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    results = {
        'scraped_at': datetime.utcnow().isoformat(),
        'commitments': [],
        'photos': {},
        'errors': []
    }

    # 1. Scrape commitments from all sources
    all_commitments = []

    try:
        all_commitments += scrape_on3_commitments()
    except Exception as e:
        results['errors'].append(f'on3: {e}')
        print(f"  On3 error: {e}")

    try:
        all_commitments += scrape_intermat_portal()
    except Exception as e:
        results['errors'].append(f'intermat: {e}')
        print(f"  InterMat error: {e}")

    try:
        all_commitments += search_flowrestling_news()
    except Exception as e:
        results['errors'].append(f'flowrestling: {e}')
        print(f"  FloWrestling error: {e}")

    # 2. Cross-reference against tracked athletes
    confirmed = check_athlete_commitments(all_commitments)
    results['commitments'] = confirmed
    results['all_raw'] = all_commitments

    print(f"\n── Summary ──")
    print(f"  Total scraped: {len(all_commitments)}")
    print(f"  WPOM matches:  {len(confirmed)}")

    # 3. Scrape photos for known athletes
    try:
        photos = scrape_known_photos()
        results['photos'] = photos
    except Exception as e:
        results['errors'].append(f'photos: {e}')
        print(f"  Photo scrape error: {e}")

    # 4. Write output
    with open('portal_updates.json', 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n✓ Written to portal_updates.json")
    print(f"  Commitments: {len(confirmed)}")
    print(f"  Photos:      {len(results['photos'])}")
    print(f"  Errors:      {len(results['errors'])}")

    return results


if __name__ == '__main__':
    import urllib.parse
    run_scraper()
