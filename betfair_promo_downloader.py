#!/usr/bin/env python3
"""
Betfair Promo SP Greyhound Downloader & Processor
=================================================
Downloads daily greyhound WIN market CSVs from:
  https://promo.betfair.com/betfairsp/prices

Filters to Australian races, parses useful fields, and writes a clean CSV
that can be used as a starting point for a race-results dataset.

Requirements:
  pip install pandas requests tqdm

Usage examples:
  # Last 7 days
  python betfair_promo_downloader.py --days 7

  # Specific date range
  python betfair_promo_downloader.py --start 2026-04-01 --end 2026-10-01

  # Just process already-downloaded files in ./downloads
  python betfair_promo_downloader.py --skip-download
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://promo.betfair.com/betfairsp/prices"
DOWNLOAD_DIR = Path("downloads")
OUTPUT_CSV = Path("aus_greyhound_betfair_promo.csv")

# Official column names (lower-case for convenience)
COLUMNS = [
    "event_id",
    "menu_hint",
    "event_name",
    "event_dt",
    "selection_id",
    "selection_name",
    "win_lose",
    "bsp",
    "ppwap",
    "morningwap",
    "ppmax",
    "ppmin",
    "ipmax",
    "ipmin",
    "morningtradedvol",
    "pptradedvol",
    "iptradedvol",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def date_range(start: datetime, end: datetime):
    """Yield every date from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_filename(dt: datetime) -> str:
    """Return the expected win-market filename for a given date."""
    return f"dwbfgreyhoundwin{dt.strftime('%d%m%Y')}.csv"


def download_file(url: str, dest: Path, session: requests.Session, retries: int = 3) -> bool:
    """Download a single file with simple retry logic. Returns True on success."""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 100:
                dest.write_bytes(resp.content)
                return True
            if resp.status_code == 404:
                log.debug("Not found: %s", url)
                return False
            log.warning("HTTP %s for %s (attempt %d)", resp.status_code, url, attempt)
        except requests.RequestException as exc:
            log.warning("Request error for %s (attempt %d): %s", url, attempt, exc)
        time.sleep(1.5 * attempt)
    return False


def download_range(start: datetime, end: datetime, force: bool = False) -> list[Path]:
    """
    Download all greyhound win CSVs in the date range.
    Returns list of successfully downloaded (or already present) file paths.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; BetfairPromoDownloader/1.0)"})

    downloaded: list[Path] = []
    dates = list(date_range(start, end))

    for dt in tqdm(dates, desc="Downloading", unit="day"):
        fname = build_filename(dt)
        dest = DOWNLOAD_DIR / fname
        url = f"{BASE_URL}/{fname}"

        if dest.exists() and not force and dest.stat().st_size > 100:
            downloaded.append(dest)
            continue

        if download_file(url, dest, session):
            downloaded.append(dest)
        else:
            # Some days may genuinely have no greyhound racing
            if dest.exists():
                dest.unlink(missing_ok=True)

    log.info("Downloaded / found %d files", len(downloaded))
    return downloaded


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# Patterns for EVENT_NAME on Australian win markets, e.g.:
#   "R3 400m Gr5"
#   "R11 545m Gr5"
#   "R1 460m Maiden"
#   "R8 498m Final"
#   "R2 366m Mdn"
RACE_NAME_RE = re.compile(
    r"""
    ^R?(?P<race_number>\d+)\s+          # optional R + race number
    (?P<distance>\d+)m\s*               # distance in metres
    (?P<grade>.*)?                      # remainder = grade / race type
    $""",
    re.VERBOSE | re.IGNORECASE,
)

# SELECTION_NAME is almost always "N. Dog Name"
SELECTION_RE = re.compile(r"^(?P<box>\d+)\.\s*(?P<dog_name>.+)$")


def parse_menu_hint(hint: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract track name and country from MENU_HINT.
    Examples:
      "Warragul (AUS) 21st Oct"  -> ("Warragul", "AUS")
      "Rockhampton (AUS) 27th Jan"
      "Central Park 27th Jan"    -> ("Central Park", None)
    """
    if not isinstance(hint, str):
        return None, None
    m = re.search(r"^(.+?)\s*\((AUS|NZL|IRL|GBR?)\)", hint, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).upper()
    # Fallback – just take everything before the date-like part
    parts = hint.rsplit(" ", 2)
    track = " ".join(parts[:-2]) if len(parts) >= 3 else hint
    return track.strip() or None, None


def parse_event_name(name: str) -> dict:
    """Parse race number / distance / grade from EVENT_NAME."""
    result = {"race_number": None, "distance": None, "grade": None}
    if not isinstance(name, str):
        return result
    m = RACE_NAME_RE.match(name.strip())
    if m:
        result["race_number"] = int(m.group("race_number"))
        result["distance"] = int(m.group("distance"))
        grade = (m.group("grade") or "").strip()
        result["grade"] = grade or None
    return result


def parse_selection_name(name: str) -> tuple[Optional[int], Optional[str]]:
    """Extract box number and dog name from SELECTION_NAME."""
    if not isinstance(name, str):
        return None, None
    m = SELECTION_RE.match(name.strip())
    if m:
        return int(m.group("box")), m.group("dog_name").strip()
    # Fallback – treat whole string as dog name
    return None, name.strip()


def process_file(path: Path) -> pd.DataFrame:
    """Read one CSV, filter to Australia, parse fields, return tidy DataFrame."""
    try:
        # Some older files lack a header or have extra commas; be defensive
        df = pd.read_csv(
            path,
            header=0,
            names=COLUMNS,
            on_bad_lines="warn",
            encoding="utf-8",
            low_memory=False,
        )
    except Exception as exc:
        log.warning("Failed to read %s: %s", path.name, exc)
        return pd.DataFrame()

    # Normalise column names just in case
    df.columns = [c.lower().strip() for c in df.columns]

    # Keep only rows that look Australian
    mask = df["menu_hint"].astype(str).str.contains(r"\(AUS\)", case=False, na=False)
    # Also catch older-style "AUS / Track" prefixes if present
    mask |= df["menu_hint"].astype(str).str.contains(r"^AUS\s*/", case=False, na=False)
    df = df.loc[mask].copy()
    if df.empty:
        return df

    # Parse date/time
    df["event_dt"] = pd.to_datetime(df["event_dt"], dayfirst=True, errors="coerce")
    df["meeting_date"] = df["event_dt"].dt.date

    # Track & country
    parsed = df["menu_hint"].apply(parse_menu_hint)
    df["track_name"] = parsed.apply(lambda x: x[0])
    df["country"] = parsed.apply(lambda x: x[1])

    # Race details from EVENT_NAME
    race_info = df["event_name"].apply(parse_event_name)
    df["race_number"] = race_info.apply(lambda x: x["race_number"])
    df["distance"] = race_info.apply(lambda x: x["distance"])
    df["grade"] = race_info.apply(lambda x: x["grade"])

    # Box & dog
    sel = df["selection_name"].apply(parse_selection_name)
    df["box_number"] = sel.apply(lambda x: x[0])
    df["dog_name"] = sel.apply(lambda x: x[1])

    # Result & price
    df["finish_position"] = df["win_lose"].map({1: 1, 0: None})  # only know the winner
    df["sp_odds"] = pd.to_numeric(df["bsp"], errors="coerce")
    # Replace the sentinel 1001 with NaN
    df.loc[df["sp_odds"] >= 1000, "sp_odds"] = pd.NA

    # Keep a useful subset of columns
    keep = [
        "meeting_date",
        "track_name",
        "country",
        "race_number",
        "event_name",          # original race description
        "distance",
        "grade",
        "box_number",
        "dog_name",
        "finish_position",    # 1 = winner, else null (we only know winners from win market)
        "sp_odds",
        "event_id",
        "selection_id",
        "event_dt",
        "ppwap",
        "pptradedvol",
    ]
    # Only keep columns that actually exist
    keep = [c for c in keep if c in df.columns]
    return df[keep]


def process_all(files: list[Path]) -> pd.DataFrame:
    """Process every downloaded file and concatenate."""
    frames = []
    for path in tqdm(files, desc="Processing", unit="file"):
        part = process_file(path)
        if not part.empty:
            frames.append(part)

    if not frames:
        log.warning("No Australian rows found in any file")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)

    # Basic clean-up
    combined = combined.drop_duplicates(
        subset=["event_id", "selection_id"], keep="first"
    )
    combined = combined.sort_values(
        ["meeting_date", "track_name", "race_number", "box_number"]
    ).reset_index(drop=True)

    log.info("Final dataset: %d rows, %d unique races",
             len(combined),
             combined["event_id"].nunique())
    return combined


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Download & process Betfair Promo greyhound SP files (Australian focus)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Download the last N days (default if no --start/--end given: 30)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Start date YYYY-MM-DD",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date YYYY-MM-DD (default: yesterday)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Do not download; only process files already in ./downloads",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the file already exists",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(OUTPUT_CSV),
        help=f"Output CSV path (default: {OUTPUT_CSV})",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Resolve date range
    today = datetime.utcnow().date()
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    elif args.days:
        start = today - timedelta(days=args.days)
    else:
        start = today - timedelta(days=30)

    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
    else:
        end = today - timedelta(days=1)  # yesterday – today’s file may be incomplete

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.min.time())

    log.info("Date range: %s → %s", start, end)

    if args.skip_download:
        files = sorted(DOWNLOAD_DIR.glob("dwbfgreyhoundwin*.csv"))
        log.info("Using %d existing files in %s", len(files), DOWNLOAD_DIR)
    else:
        files = download_range(start_dt, end_dt, force=args.force)

    if not files:
        log.error("No files available to process")
        return

    df = process_all(files)
    if df.empty:
        log.error("Nothing to write")
        return

    out_path = Path(args.output)
    df.to_csv(out_path, index=False)
    log.info("Wrote %s  (%d rows)", out_path, len(df))

    # Quick preview
    print("\n--- Preview (first 8 rows) ---")
    print(df.head(8).to_string(index=False))
    print("\n--- Column summary ---")
    print(df.dtypes)
    print(f"\nAustralian tracks found: {sorted(df['track_name'].dropna().unique())[:15]} ...")


if __name__ == "__main__":
    main()
