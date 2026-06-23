#!/usr/bin/env python3
"""
Bolashak scholarship-recipients monitor.

Watches https://bolashak.gov.kz/ru/o-stipendii/obladateli-stipendii and sends a
Telegram message when a new recipients list appears -- in particular anything
mentioning the target year (default 2026).

When a new list appears it also downloads the PDF and searches it for your
name (surname + first name as adjacent whole words). If found, you get a
"Congratulations, you won!" message.

Configuration is read from environment variables:
    TELEGRAM_BOT_TOKEN   (required)  bot token from @BotFather
    TELEGRAM_CHAT_ID     (required)  your numeric chat id
    TARGET_YEAR          (optional)  year to highlight, default "2026"
    WATCH_SURNAME        (optional)  surname to search for, default "Аманбай"
    WATCH_FIRSTNAME      (optional)  first name to search for, default "Алмас"
    STATE_FILE           (optional)  path to state json, default "state.json"
    NOTIFY_ANY_NEW       (optional)  "1"/"0", also notify about non-target-year
                                     new lists. Default "1".

Usage:
    python monitor.py            # run one check
    python monitor.py --test     # just send a test Telegram message and exit
"""

import io
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

try:
    import pypdf
except ImportError:  # the workflow installs it; locally it may be missing
    pypdf = None

URL = "https://bolashak.gov.kz/ru/o-stipendii/obladateli-stipendii"
BASE = "https://bolashak.gov.kz"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TARGET_YEAR = os.environ.get("TARGET_YEAR", "2026").strip()
WATCH_SURNAME = os.environ.get("WATCH_SURNAME", "Аманбай").strip()
WATCH_FIRSTNAME = os.environ.get("WATCH_FIRSTNAME", "Алмас").strip()
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
NOTIFY_ANY_NEW = os.environ.get("NOTIFY_ANY_NEW", "1").strip() != "0"
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Recognises the recipient links, e.g. "Обладатели стипендии от 24 декабря 2025 года"
ENTRY_TEXT_MARKER = "Обладатели стипендии"


def log(msg):
    print("[%s] %s" % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"), msg),
          flush=True)


def fetch(url, retries=3):
    """Fetch a URL as text, retrying a few times on transient errors."""
    # Test hook: read from a local file instead of the network.
    local = os.environ.get("BOLASHAK_HTML_FILE")
    if local:
        with open(local, encoding="utf-8") as f:
            return f.read()
    ctx = ssl.create_default_context()
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - we want to retry on anything
            last_err = e
            log("fetch attempt %d/%d failed: %r" % (attempt, retries, e))
    raise last_err


def clean_text(html_fragment):
    text = re.sub(r"<[^>]+>", "", html_fragment)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_entries(html):
    """Return {key: {"text": ..., "href": ...}} for every recipient-list link."""
    entries = {}
    for href, inner in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        text = clean_text(inner)
        if ENTRY_TEXT_MARKER.lower() not in text.lower():
            continue
        abs_href = href if href.startswith("http") else BASE + href
        key = abs_href  # the PDF URL is the stable identity of a list
        entries[key] = {"text": text, "href": abs_href}
    return entries


def is_target(entry):
    # Primary, reliable signal: the visible link text, e.g. "... 2026 года".
    # Use digit boundaries so "2026" isn't matched inside a longer number.
    if re.search(r"(?<!\d)" + re.escape(TARGET_YEAR) + r"(?!\d)", entry["text"]):
        return True
    # Secondary: a year folder/date in the (decoded) PDF url, e.g. ".../2026/..."
    # Decode first so URL-encoded spaces (%20 -> " ") can't fabricate the year.
    href_dec = urllib.parse.unquote(entry["href"])
    if re.search(r"[/_\-]" + re.escape(TARGET_YEAR) + r"[/_\-.]", href_dec):
        return True
    return False


def download_bytes(url, retries=3):
    """Download a URL as raw bytes (used for PDFs)."""
    # Test hook: map a url to a local file via BOLASHAK_PDF_FILE.
    local = os.environ.get("BOLASHAK_PDF_FILE")
    if local:
        with open(local, "rb") as f:
            return f.read()
    ctx = ssl.create_default_context()
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log("pdf download attempt %d/%d failed: %r" % (attempt, retries, e))
    raise last_err


def extract_pdf_text(data):
    """Return the text of a PDF, or None if it can't be read as text."""
    if pypdf is None:
        log("pypdf not installed -- cannot read PDF")
        return None
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:  # noqa: BLE001
        log("PDF parse error: %r" % e)
        return None
    return text


def _line_of(text, regex):
    """Return the source line containing a regex match (for context)."""
    for line in text.splitlines():
        if regex.search(line):
            return re.sub(r"\s+", " ", line).strip()
    m = regex.search(text)
    if m:
        s, e = max(0, m.start() - 25), min(len(text), m.end() + 35)
        return re.sub(r"\s+", " ", text[s:e]).strip()
    return ""


def search_name(text):
    """Search PDF text for the watched person, and ONLY that person.

    Names in these lists are "Surname FirstName Patronymic". We match the
    surname immediately followed by the first name as whole words, so it matches
    exactly "Аманбай Алмас" and nobody else -- not a different person who shares
    the surname (e.g. "Аманбай Нурлан"), not someone whose first name is "Алмас"
    (e.g. "Жумабеков Алмас"), and not a patronymic like "Алмасовна".
    Returns ("win", line) on a match, ("no", None) otherwise.
    """
    if not text:
        return ("unreadable", None)
    norm = text.replace("\xa0", " ")
    full = re.compile(
        r"\b" + re.escape(WATCH_SURNAME) + r"\s+" + re.escape(WATCH_FIRSTNAME) + r"\b",
        re.IGNORECASE,
    )
    if full.search(norm):
        return ("win", _line_of(norm, full))
    return ("no", None)


def analyze_pdf(entry):
    """Download an entry's PDF and search it. Returns (status, line)."""
    try:
        data = download_bytes(entry["href"])
    except Exception as e:  # noqa: BLE001
        log("could not download %s: %r" % (entry["href"], e))
        return ("unreadable", None)
    status, line = search_name(extract_pdf_text(data))
    log("PDF '%s' -> %s" % (entry["text"], status))
    return (status, line)


def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        log("could not read state (%r); treating as first run" % e)
        return None


def save_state(entries):
    state = {
        "last_checked_date": date.today().isoformat(),
        "last_checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_year": TARGET_YEAR,
        "keys": sorted(entries.keys()),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    log("state saved (%d entries)" % len(entries))


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        log("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set -- printing instead:\n" + text)
        return False
    api = "https://api.telegram.org/bot%s/sendMessage" % BOT_TOKEN
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(api, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    if not resp.get("ok"):
        raise RuntimeError("Telegram API error: %s" % resp)
    log("Telegram message sent")
    return True


def main():
    if "--test" in sys.argv:
        send_telegram("✅ <b>Bolashak monitor</b> is connected. "
                      "I'll message you here when a <b>%s</b> recipients list appears."
                      % esc(TARGET_YEAR))
        return 0

    html = fetch(URL)
    entries = parse_entries(html)
    log("found %d recipient entries on page" % len(entries))
    if not entries:
        log("WARNING: parsed 0 entries -- page layout may have changed. Not updating state.")
        return 0

    prev = load_state()

    if prev is None:
        # First run: remember what's there now, send a one-time confirmation,
        # and do NOT report every existing list as "new".
        save_state(entries)
        target_now = [e for e in entries.values() if is_target(e)]
        if target_now:
            lines = ["🎓 <b>%s recipients list already present:</b>" % esc(TARGET_YEAR)]
            for e in target_now:
                lines.append('• <a href="%s">%s</a>' % (esc(e["href"]), esc(e["text"])))
            send_telegram("\n".join(lines))
        else:
            send_telegram("✅ <b>Bolashak monitor started.</b>\nWatching %s\nNo <b>%s</b> "
                          "list yet — I'll ping you the moment one appears."
                          % (esc(URL), esc(TARGET_YEAR)))
        return 0

    prev_keys = set(prev.get("keys", []))
    new_keys = [k for k in entries if k not in prev_keys]

    if not new_keys:
        log("no changes")
        save_state(entries)  # refresh last_checked_date (keeps repo active)
        return 0

    log("new entries: %d" % len(new_keys))
    full_name = "%s %s" % (WATCH_SURNAME, WATCH_FIRSTNAME)

    for k in sorted(new_keys):
        e = entries[k]
        target = is_target(e)
        if not target and not NOTIFY_ANY_NEW:
            continue  # ignore non-target lists if the user only wants TARGET_YEAR
        status, line = analyze_pdf(e)
        msg = build_message(e, target, status, line, full_name)
        if msg:
            send_telegram(msg)

    save_state(entries)
    return 0


def build_message(entry, target, status, line, full_name):
    title = esc(entry["text"])
    href = esc(entry["href"])
    link = '<a href="%s">Open the PDF</a>' % href

    if status == "win":
        return ("🎉🎉🎉 <b>CONGRATULATIONS, YOU WON!</b> 🎉🎉🎉\n\n"
                "Your name «%s» is in the new list:\n<b>%s</b>\n\n"
                "Found entry:\n<code>%s</code>\n\n%s"
                % (esc(full_name), title, esc(line), link))

    year_tag = ("🎓 <b>NEW %s list published!</b>" % esc(TARGET_YEAR)) if target \
        else "📄 <b>New recipients list added</b>"

    if status == "unreadable":
        return ("%s\n<b>%s</b>\n\nI couldn't read the PDF automatically — "
                "please check it yourself.\n%s" % (year_tag, title, link))

    # status == "no"
    return ("%s\n<b>%s</b>\n\nYour name «%s» was <b>not</b> found in this list.\n%s"
            % (year_tag, title, esc(full_name), link))


if __name__ == "__main__":
    sys.exit(main())
