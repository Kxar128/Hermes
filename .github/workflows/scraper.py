# scraper.py
import feedparser
import requests
import json
import os
import time
import tempfile
import re
from urllib.parse import urlparse

STATE_FILE = "state.json"

USERS = {
    "YourTwitterUsername": os.environ.get("HERMESSOLARIS"),
}

FEED_BASE = "https://nitter.net"  # RSS provider
TEST_MODE = os.environ.get("TEST_MODE", "").lower() in ("1", "true", "yes")
TEST_PREFIX = os.environ.get("TEST_PREFIX", "[TEST]")
HEADERS = {"User-Agent": "Mozilla/5.0"}
ID_RE = re.compile(r"/status/(\d+)")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def atomic_write_state(state):
    tfd, tmp = tempfile.mkstemp(dir=".", prefix="state.", text=True)
    with os.fdopen(tfd, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def canonicalize_to_x(url):
    """Return canonical x.com status URL (strip fragment/query)."""
    if not url:
        return url
    p = urlparse(url)
    path = p.path.rstrip("/")
    if "nitter." in p.netloc or "nitter" in p.netloc:
        return "https://x.com" + path
    if "twitter.com" in p.netloc or "x.com" in p.netloc:
        return p.scheme + "://" + p.netloc + path
    return p.scheme + "://" + p.netloc + path


def extract_id_from_url(url):
    """Extract numeric status id from a URL. Return int or None."""
    if not url:
        return None
    m = ID_RE.search(url)
    if m:
        try:
            return int(m.group(1))
        except:
            return None
    digits = re.search(r"(\d{5,})", url)
    if digits:
        try:
            return int(digits.group(1))
        except:
            return None
    return None


def fetch_feed_entries(username):
    rss_url = f"{FEED_BASE}/{username}/rss"
    feed = feedparser.parse(rss_url)
    entries = []
    if not feed or not getattr(feed, "entries", None):
        return entries
    for e in feed.entries:
        link = e.get("link") or e.get("id") or ""
        cid = extract_id_from_url(link)
        canonical = canonicalize_to_x(link)
        if cid is None:
            links = e.get("links", [])
            for L in links:
                href = L.get("href")
                c2 = extract_id_from_url(href) if href else None
                if c2:
                    cid = c2
                    canonical = canonicalize_to_x(href)
                    break
        if cid:
            entries.append({"id": cid, "link": canonical})
    seen = set()
    uniq = []
    for it in entries:
        if it["id"] not in seen:
            uniq.append(it)
            seen.add(it["id"])
    return uniq


def post_to_discord(webhook, link, is_test=False):
    if not webhook:
        print("[ERROR] missing webhook")
        return False
    post_link = link
    post_link = post_link.replace("https://nitter.net/", "https://x.com/").replace(
        "https://nitter.cz/", "https://x.com/"
    )
    content = f"{TEST_PREFIX} {post_link}" if is_test else post_link
    try:
        r = requests.post(webhook, json={"content": content}, timeout=15)
        print(f"[DISCORD] status {r.status_code}")
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"[ERROR] posting to discord failed: {e}")
        return False


def process_user(username, webhook, state, posted_this_run):
    entries = fetch_feed_entries(username)
    if not entries:
        print(f"[INFO] no RSS entries for {username}")
        return False
    entries_sorted = sorted(entries, key=lambda x: x["id"])
    last_seen_id = None
    if state.get(username):
        try:
            last_seen_id = int(state.get(username))
        except:
            last_seen_id = None
    if last_seen_id:
        new_entries = [e for e in entries_sorted if e["id"] > last_seen_id]
    else:
        new_entries = entries_sorted[-1:] if entries_sorted else []
    if not new_entries:
        print(f"[OK] no new post for {username}")
        return False
    any_posted = False
    for entry in new_entries:
        if entry["id"] in posted_this_run:
            print(f"[SKIP] already posted this run: {entry['id']}")
            continue
        success = post_to_discord(webhook, entry["link"], is_test=TEST_MODE)
        if success:
            posted_this_run.add(entry["id"])
            any_posted = True
        else:
            print(f"[WARN] failed to post {entry['link']}; stopping further posts for {username}")
            break
    if any_posted and not TEST_MODE:
        newest_posted_id = max(e["id"] for e in new_entries) if new_entries else None
        if newest_posted_id:
            state[username] = str(newest_posted_id)
    return any_posted


def main():
    print("=== START SCRAPER ===")
    state = load_state()
    posted_this_run = set()
    updated = False
    for user, webhook in USERS.items():
        print(f"[CHECK] {user}")
        changed = process_user(user, webhook, state, posted_this_run)
        if changed:
            updated = True
        time.sleep(1)
    if updated and not TEST_MODE:
        atomic_write_state(state)
        print("[STATE] state.json updated")
    else:
        if TEST_MODE:
            print("[TEST] TEST_MODE active — state not changed")
        else:
            print("[STATE] no changes to state")
    print("=== FINISHED ===")


if __name__ == "__main__":
    main()
