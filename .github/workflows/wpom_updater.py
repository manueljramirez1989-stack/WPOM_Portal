"""
wpom_updater.py
Reads portal_updates.json from the scraper and patches index.html.
Updates: commitment status, photo URLs, ticker entries.
Writes a git commit message summarizing changes.
"""

import json
import re
import os
from datetime import datetime

# ── LOAD DATA ─────────────────────────────────────────────────────────────────

def load_updates(path='portal_updates.json'):
    if not os.path.exists(path):
        print(f"No {path} found — nothing to update.")
        return None
    with open(path) as f:
        return json.load(f)


def load_html(path='index.html'):
    with open(path) as f:
        return f.read()


def save_html(content, path='index.html'):
    with open(path, 'w') as f:
        f.write(content)


# ── SCHOOL NAME NORMALIZATION ─────────────────────────────────────────────────

SCHOOL_ALIASES = {
    # Common short names → full names as used in WPOM data
    'iowa':            'Iowa',
    'ohio state':      'Ohio State',
    'ohio st':         'Ohio State',
    'penn state':      'Penn State',
    'oklahoma state':  'Oklahoma State',
    'ok state':        'Oklahoma State',
    'ok st':           'Oklahoma State',
    'nc state':        'NC State',
    'north carolina state': 'NC State',
    'michigan':        'Michigan',
    'minnesota':       'Minnesota',
    'nebraska':        'Nebraska',
    'illinois':        'Illinois',
    'purdue':          'Purdue',
    'iowa state':      'Iowa State',
    'missouri':        'Missouri',
    'stanford':        'Stanford',
    'cornell':         'Cornell',
    'duke':            'Duke',
    'virginia tech':   'Virginia Tech',
    'vt':              'Virginia Tech',
    'north carolina':  'UNC',
    'unc':             'UNC',
    'maryland':        'Maryland',
    'pittsburgh':      'Pitt',
    'pitt':            'Pitt',
    'lehigh':          'Lehigh',
    'rutgers':         'Rutgers',
    'wisconsin':       'Wisconsin',
    'arizona state':   'Arizona State',
    'asu':             'Arizona State',
    'west virginia':   'West Virginia',
    'wvu':             'West Virginia',
    'princeton':       'Princeton',
    'columbia':        'Columbia',
    'harvard':         'Harvard',
    'little rock':     'Little Rock',
}

def normalize_school(raw):
    if not raw:
        return raw
    key = raw.strip().lower()
    return SCHOOL_ALIASES.get(key, raw.strip())


# ── COMMITMENT UPDATER ────────────────────────────────────────────────────────

def update_commitments(html, commitments):
    """
    For each confirmed commitment, find the athlete in the DATA block
    and update their committed field.
    Returns (updated_html, list_of_changes)
    """
    changes = []

    for c in commitments:
        wpom_name = c.get('wpom_match', '')
        school_raw = c.get('committed_to', '')
        if not wpom_name or not school_raw:
            continue

        school = normalize_school(school_raw)

        # Find the athlete entry in the DATA block
        # Entries look like: name:'V. Robinson', ... committed:''
        # We need to update committed:'' → committed:'Iowa'

        # Pattern: name:'NAME', ... committed:''
        # Build a pattern that matches this specific athlete's committed field
        name_pattern = re.escape(wpom_name)

        # Find the entry block for this athlete
        entry_start = html.find(f"name:'{wpom_name}'")
        if entry_start == -1:
            print(f"  ✗ Could not find entry for: {wpom_name}")
            continue

        # Find the end of this entry (next { or end of entries array)
        entry_end = html.find('},', entry_start)
        if entry_end == -1:
            entry_end = html.find('}', entry_start)

        entry_block = html[entry_start:entry_end+2]

        # Check if already committed
        current_committed = re.search(r"committed:'([^']*)'", entry_block)
        if current_committed and current_committed.group(1) == school:
            print(f"  ✓ Already committed: {wpom_name} → {school}")
            continue

        # Update the committed field
        if current_committed:
            old_val = current_committed.group(0)
            new_val = f"committed:'{school}'"
            new_entry = entry_block.replace(old_val, new_val)
            html = html[:entry_start] + new_entry + html[entry_start+len(entry_block):]
            changes.append(f"{wpom_name} → {school}")
            print(f"  ✓ Updated: {wpom_name} → {school}")
        else:
            print(f"  ✗ No committed field found for: {wpom_name}")

    return html, changes


# ── PHOTO UPDATER ─────────────────────────────────────────────────────────────

def update_photos(html, photos):
    """
    Update photo URLs in athlete entries.
    Only updates entries that currently have photo:''
    """
    changes = []

    for name, url in photos.items():
        if not url:
            continue

        entry_start = html.find(f"name:'{name}'")
        if entry_start == -1:
            continue

        entry_end = html.find('},', entry_start)
        entry_block = html[entry_start:entry_end+2]

        # Only update if photo is currently empty
        if "photo:''" in entry_block:
            new_entry = entry_block.replace("photo:''", f"photo:'{url}'")
            html = html[:entry_start] + new_entry + html[entry_start+len(entry_block):]
            changes.append(f"{name}: photo updated")
            print(f"  ✓ Photo updated: {name}")
        elif f"photo:'{url}'" in entry_block:
            pass  # already correct
        else:
            # Update to new URL regardless
            new_entry = re.sub(r"photo:'[^']*'", f"photo:'{url}'", entry_block)
            html = html[:entry_start] + new_entry + html[entry_start+len(entry_block):]
            changes.append(f"{name}: photo refreshed")
            print(f"  ✓ Photo refreshed: {name}")

    return html, changes


# ── DATE STAMP UPDATER ────────────────────────────────────────────────────────

def update_datestamp(html):
    """Update the 'Live Portal Data · Month Year' label in the scorecard."""
    now = datetime.utcnow()
    month_year = now.strftime('%B %Y')
    # Pattern: Live Portal Data · April 2026
    new_label = f'Live Portal Data &middot; {month_year}'
    html = re.sub(
        r'Live Portal Data &middot; \w+ \d{4}',
        new_label,
        html
    )
    return html


# ── TICKER UPDATER ────────────────────────────────────────────────────────────

def update_ticker(html, new_commitments):
    """
    Add new commitment announcements to the ticker.
    Inserts new tick-items near the top of the ticker.
    """
    if not new_commitments:
        return html

    ticker_inner = html.find('class="ticker-inner"')
    if ticker_inner == -1:
        return html

    # Build new ticker items for each new commitment
    new_items = ''
    for c in new_commitments:
        name = c.get('wpom_match') or c.get('name', '')
        school = normalize_school(c.get('committed_to', ''))
        if name and school:
            new_items += f'\n    <span class="tick-item"><strong>{name}</strong> → {school} (COMMITTED)</span><span class="tick-sep">|</span>'

    if new_items:
        # Insert after ticker-inner opening
        insert_pos = html.find('>', ticker_inner) + 1
        html = html[:insert_pos] + new_items + html[insert_pos:]

    return html


# ── GIT COMMIT MESSAGE ────────────────────────────────────────────────────────

def build_commit_message(commitment_changes, photo_changes):
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    lines = [f'Portal update {now}']

    if commitment_changes:
        lines.append('')
        lines.append('Commitments:')
        for c in commitment_changes:
            lines.append(f'  - {c}')

    if photo_changes:
        lines.append('')
        lines.append('Photos:')
        for p in photo_changes:
            lines.append(f'  - {p}')

    if not commitment_changes and not photo_changes:
        lines.append('No changes — routine check')

    return '\n'.join(lines)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run_updater(html_path='index.html', updates_path='portal_updates.json'):
    print(f"\n{'='*60}")
    print(f"WPOM Updater — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # Load
    updates = load_updates(updates_path)
    if not updates:
        return False

    html = load_html(html_path)
    original_html = html

    all_commitment_changes = []
    all_photo_changes = []

    # 1. Update commitments
    confirmed_commitments = updates.get('commitments', [])
    if confirmed_commitments:
        print(f"\n── Processing {len(confirmed_commitments)} confirmed commitments ──")
        html, changes = update_commitments(html, confirmed_commitments)
        all_commitment_changes += changes
    else:
        print("\n── No new commitments found ──")

    # 2. Update photos
    photos = updates.get('photos', {})
    if photos:
        print(f"\n── Processing {len(photos)} photos ──")
        html, changes = update_photos(html, photos)
        all_photo_changes += changes
    else:
        print("\n── No new photos ──")

    # 3. Update ticker with new commitments
    if all_commitment_changes:
        html = update_ticker(html, confirmed_commitments)

    # 4. Update date stamp
    html = update_datestamp(html)

    # 5. Write if changed
    if html != original_html:
        save_html(html, html_path)
        print(f"\n✓ index.html updated")
    else:
        print(f"\n— No changes to index.html")

    # 6. Write commit message
    commit_msg = build_commit_message(all_commitment_changes, all_photo_changes)
    with open('commit_message.txt', 'w') as f:
        f.write(commit_msg)
    print(f"\nCommit message:\n{commit_msg}")

    return bool(html != original_html)


if __name__ == '__main__':
    changed = run_updater()
    # Exit code 0 = changes made, 1 = no changes
    # (GitHub Actions uses this to decide whether to commit)
    import sys
    sys.exit(0 if changed else 1)
