#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
WORKER_VERSION = "1.0.0"
PIPELINE = "website_scraper"
MAX_RETRIES = 3
TOTAL_ATTEMPTS = 4
BACKOFF_SECONDS = [60, 300, 900]
DEFAULT_MAX_PAGES = 20
HARD_MAX_PAGES = 100
DEFAULT_MAX_DEPTH = 2
HARD_MAX_DEPTH = 5
DEFAULT_DELAY_SECONDS = 2.0
STATE_RETENTION_DAYS = 183

AI = Path(os.environ.get("SECOND_BRAIN_ROOT", "/AI"))
STATE_DIR = AI / "State/Second Brain/Website Scraper"
STATE_PATH = STATE_DIR / "current.json"
LOCK_PATH = STATE_DIR / "worker.lock"
LOG_DIR = AI / "Logs/Pipelines/Websites"
EVENT_LOG = LOG_DIR / "website-scraper-events.jsonl"
MANIFEST_ROOT = AI / "Archive/Websites/Manifests"
FAILED_ROOT = AI / "Archive/Websites/Failed"
NOTE_DIR = AI / "Knowledge/Second Brain/00 Inbox/Websites"
TEMP_ROOT = AI / "Temp/Website Scraper"
WEBPAGE_WORKER = AI / "Scripts/Second Brain Webpage Import/webpage_import.py"
FAILURE_CONTROL = AI / "Scripts/Second Brain Failure Control/failure_control.py"
BROWSER_IMAGE = "local/second-brain-web-renderer:1.0.0"
BROWSER_NETWORK = "second-brain-scraper-net"
SECCOMP_PROFILE = AI / "Apps/Website Scraper Browser/seccomp_profile.json"

TRACKER_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
    "igshid", "vero_id", "oly_anon_id", "oly_enc_id", "ref_src",
}
SKIP_EXTENSIONS = {
    ".7z", ".avi", ".bmp", ".csv", ".doc", ".docx", ".epub", ".exe", ".gif", ".gz",
    ".ico", ".iso", ".jpeg", ".jpg", ".json", ".m4a", ".mkv", ".mov", ".mp3", ".mp4",
    ".mpeg", ".ods", ".odt", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".rss", ".svg",
    ".tar", ".tif", ".tiff", ".tsv", ".wav", ".webp", ".xls", ".xlsx", ".xml", ".zip",
}

class RetryableError(RuntimeError):
    pass

class PermanentError(RuntimeError):
    pass

def now_dt() -> datetime:
    return datetime.now(timezone.utc)

def now_iso() -> str:
    return now_dt().isoformat().replace("+00:00", "Z")

def parse_time(value):
    if not value:
        return None
    try:
        s = str(value)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def safe_text(value, limit=1000) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value or ""))[:limit].strip()

def yaml_string(value) -> str:
    return json.dumps(str(value), ensure_ascii=False)

def atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass

def atomic_text(path: Path, text: str, mode=0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass

def append_event(obj: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def load_webpage_module():
    spec = importlib.util.spec_from_file_location("second_brain_webpage_import", WEBPAGE_WORKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load canonical webpage-import module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "publish_rendered_capture", None)):
        raise RuntimeError("Webpage importer does not expose the browser-rendered publish API required by Website Scraper.")
    return module

def base_state():
    return {
        "schema_version": SCHEMA_VERSION,
        "worker_version": WORKER_VERSION,
        "updated_at": now_iso(),
        "policy": {
            "default_max_pages": DEFAULT_MAX_PAGES,
            "hard_max_pages": HARD_MAX_PAGES,
            "default_max_depth": DEFAULT_MAX_DEPTH,
            "hard_max_depth": HARD_MAX_DEPTH,
            "minimum_delay_seconds": DEFAULT_DELAY_SECONDS,
            "robots_txt_honored": True,
            "nofollow_honored": True,
            "same_host_default": True,
            "login_and_access_control_bypass": False,
            "browser_image": BROWSER_IMAGE,
            "browser_network": BROWSER_NETWORK,
        },
        "crawls": {},
        "summary": {},
    }

def recompute_summary(state):
    keys = ["queued", "processing", "completed", "completed_with_warning", "failed_permanent"]
    counts = {k: 0 for k in keys}
    for rec in state.get("crawls", {}).values():
        status = rec.get("status")
        if status in counts:
            counts[status] += 1
    state["summary"] = counts
    state["updated_at"] = now_iso()

def prune_state(state):
    cutoff = now_dt() - timedelta(days=STATE_RETENTION_DAYS)
    remove = []
    for crawl_id, rec in state.get("crawls", {}).items():
        if rec.get("status") not in {"completed", "completed_with_warning", "failed_permanent"}:
            continue
        when = parse_time(rec.get("completed_at") or rec.get("updated_at"))
        if when and when < cutoff:
            remove.append(crawl_id)
    for crawl_id in remove:
        state["crawls"].pop(crawl_id, None)

class StateLock:
    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.f = (STATE_DIR / "state.lock").open("a+")
        fcntl.flock(self.f.fileno(), fcntl.LOCK_EX)
        return self
    def __exit__(self, exc_type, exc, tb):
        fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        self.f.close()

class WorkerLock:
    def __init__(self, nonblocking=False):
        self.nonblocking = nonblocking
        self.acquired = False
    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.f = LOCK_PATH.open("a+")
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if self.nonblocking else 0)
        try:
            fcntl.flock(self.f.fileno(), flags)
            self.acquired = True
        except BlockingIOError:
            self.acquired = False
        return self
    def __exit__(self, exc_type, exc, tb):
        if self.acquired:
            fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        self.f.close()

def load_state():
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = base_state()
    except Exception as exc:
        raise RuntimeError(f"Website Scraper state is unreadable: {exc}")
    if not isinstance(state, dict) or not isinstance(state.get("crawls", {}), dict):
        raise RuntimeError("Website Scraper state schema is invalid.")
    state.setdefault("crawls", {})
    state.setdefault("policy", base_state()["policy"])
    state["schema_version"] = SCHEMA_VERSION
    state["worker_version"] = WORKER_VERSION
    return state

def save_state(state):
    prune_state(state)
    recompute_summary(state)
    atomic_json(STATE_PATH, state)
    try:
        os.chmod(STATE_PATH, 0o600)
    except Exception:
        pass

def mutate_state(fn):
    with StateLock():
        state = load_state()
        result = fn(state)
        save_state(state)
        return result

def browser_available() -> bool:
    try:
        image = subprocess.run(["docker", "image", "inspect", BROWSER_IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        network = subprocess.run(["docker", "network", "inspect", BROWSER_NETWORK], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        return image and network and SECCOMP_PROFILE.is_file()
    except OSError:
        return False

def failure_control(args: list[str]) -> dict:
    p = subprocess.run(
        ["python3", str(FAILURE_CONTROL), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if p.returncode != 0:
        raise RuntimeError(f"Failure Control command failed: {(p.stdout or '').strip()}")
    lines = [x for x in (p.stdout or "").splitlines() if x.strip()]
    if not lines:
        return {}
    try:
        return json.loads(lines[-1])
    except Exception:
        return {"raw": lines[-1]}

def strip_tracking(url: str, wp) -> str:
    normalized = wp.normalize_url(url)
    p = urllib.parse.urlsplit(normalized)
    query = []
    for key, value in urllib.parse.parse_qsl(p.query, keep_blank_values=True):
        lk = key.lower()
        if lk.startswith("utm_") or lk in TRACKER_KEYS:
            continue
        query.append((key, value))
    cleaned = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", urllib.parse.urlencode(query, doseq=True), ""))
    return wp.normalize_url(cleaned)

def host_allowed(host: str, crawl: dict) -> bool:
    host = (host or "").lower().rstrip(".")
    allowed_hosts = {str(x).lower().rstrip(".") for x in crawl.get("allowed_hosts", [])}
    root_host = str(crawl.get("root_host") or "").lower().rstrip(".")
    if host in allowed_hosts or host == root_host:
        return True
    return bool(crawl.get("include_subdomains")) and any(
        host.endswith("." + base) for base in (allowed_hosts | {root_host}) if base
    )

def should_skip_path(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    return any(path.endswith(ext) for ext in SKIP_EXTENSIONS)

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.meta_nofollow = False
        self.base_href = None
    def handle_starttag(self, tag, attrs):
        attr = {str(k).lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "base" and attr.get("href") and not self.base_href:
            self.base_href = attr["href"]
        elif tag == "a" and attr.get("href"):
            rel = {x.strip().lower() for x in attr.get("rel", "").replace(",", " ").split()}
            self.links.append((attr["href"], "nofollow" in rel))
        elif tag == "meta":
            name = (attr.get("name") or "").lower()
            if name == "robots":
                tokens = {x.strip().lower() for x in attr.get("content", "").replace(",", " ").split()}
                if "nofollow" in tokens or "none" in tokens:
                    self.meta_nofollow = True

def extract_links(html_text: str, base_url: str, crawl: dict, wp) -> list[str]:
    parser = LinkParser()
    try:
        parser.feed(html_text)
    except Exception:
        return []
    if parser.meta_nofollow:
        return []
    base = urllib.parse.urljoin(base_url, parser.base_href) if parser.base_href else base_url
    out = []
    seen = set()
    for href, nofollow in parser.links:
        if nofollow:
            continue
        absolute = urllib.parse.urljoin(base, href)
        try:
            candidate = strip_tracking(absolute, wp)
            p = urllib.parse.urlsplit(candidate)
            if not host_allowed(p.hostname or "", crawl):
                continue
            if should_skip_path(candidate):
                continue
            wp.validate_public_resolution(candidate)
        except Exception:
            continue
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out

def robots_policy(crawl: dict, target_url: str, wp) -> tuple[bool, float]:
    p = urllib.parse.urlsplit(target_url)
    origin = f"{p.scheme}://{p.netloc}"
    cache = crawl.setdefault("robots", {})
    cached = cache.get(origin)
    if isinstance(cached, dict):
        if cached.get("mode") == "allow_all":
            return True, float(cached.get("delay_seconds") or 0)
        if cached.get("mode") == "deny_all":
            return False, float(cached.get("delay_seconds") or 0)
        lines = cached.get("lines", [])
    else:
        robots_url = origin + "/robots.txt"
        try:
            fetched = wp.fetch_url(robots_url)
            lines = fetched["decoded"].splitlines()
            cache[origin] = {"mode": "parsed", "lines": lines[:10000], "fetched_at": now_iso()}
        except wp.PermanentError as exc:
            msg = str(exc)
            if "HTTP 404" in msg or "HTTP 410" in msg:
                cache[origin] = {"mode": "allow_all", "lines": [], "fetched_at": now_iso()}
                return True, 0.0
            if "HTTP 401" in msg or "HTTP 403" in msg:
                cache[origin] = {"mode": "deny_all", "lines": [], "fetched_at": now_iso()}
                return False, 0.0
            # Conservative on malformed/unsupported robots responses.
            cache[origin] = {"mode": "deny_all", "lines": [], "error": safe_text(exc), "fetched_at": now_iso()}
            return False, 0.0
        except wp.RetryableError as exc:
            raise RetryableError(f"robots.txt could not be checked safely yet: {exc}")

    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(origin + "/robots.txt")
    rp.parse(lines)
    agent = "SecondBrainWebsiteScraper"
    allowed = bool(rp.can_fetch(agent, target_url))
    delay = rp.crawl_delay(agent)
    if delay is None:
        delay = rp.crawl_delay("*")
    try:
        delay = float(delay or 0)
    except Exception:
        delay = 0.0
    return allowed, max(0.0, delay)

def browser_render(url: str) -> dict:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    token = sha256_text(url + now_iso())[:16]
    name = f"sb-scraper-{token}"
    scratch = TEMP_ROOT / f"render-{token}"
    output = scratch / "second-brain-render.html"
    scratch.mkdir(parents=True, exist_ok=False)
    # The browser only receives a newly-created empty scratch directory. It contains
    # no Second Brain data and is deleted immediately after the DOM is consumed.
    # Mode 0777 avoids coupling the host uid to the image-owned non-root pwuser uid.
    os.chmod(scratch, 0o777)
    create = [
        "docker", "create", "--name", name, "--init",
        "--network", BROWSER_NETWORK,
        "--user", "pwuser",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--security-opt", f"seccomp={SECCOMP_PROFILE}",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=536870912",
        "--tmpfs", "/home/pwuser:rw,nosuid,nodev,size=67108864",
        "--mount", f"type=bind,src={scratch},dst=/output",
        "--pids-limit", "256",
        "--memory", "1024m",
        "--cpus", "2",
        "--shm-size", "512m",
        "-e", f"SB_RENDER_URL={url}",
        BROWSER_IMAGE,
    ]
    try:
        p = subprocess.run(create, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if p.returncode != 0:
            raise RetryableError(f"Browser container could not be created: {safe_text(p.stdout, 2000)}")
        p = subprocess.run(["docker", "start", "-a", name], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        inspect = subprocess.run(["docker", "inspect", "-f", "{{.State.ExitCode}}", name], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        exit_code = int((inspect.stdout or "1").strip()) if inspect.returncode == 0 else 1
        lines = [x for x in (p.stdout or "").splitlines() if x.strip()]
        meta = {}
        if lines:
            try:
                meta = json.loads(lines[-1])
            except Exception:
                meta = {}
        if exit_code != 0:
            message = meta.get("error") or safe_text(p.stdout, 3000) or f"renderer exit {exit_code}"
            if meta.get("retryable") or exit_code == 75:
                raise RetryableError(message)
            raise PermanentError(message)
        if not output.is_file():
            raise RetryableError("Rendered DOM was not written to the isolated scratch output directory.")
        text = output.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            raise RetryableError("Isolated browser returned empty DOM.")
        return {
            "html": text,
            "final_url": meta.get("final_url") or url,
            "http_status": int(meta.get("http_status") or 200),
            "renderer": meta.get("renderer") or "playwright-chromium-1.61.0",
        }
    finally:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(scratch, ignore_errors=True)

def crawl_id_for(normalized_root: str) -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + sha256_text(normalized_root)[:10]

def queue_crawl(url: str, max_pages: int, max_depth: int, render_mode: str, include_subdomains: bool, delay_seconds: float) -> dict:
    wp = load_webpage_module()
    normalized = strip_tracking(url, wp)
    wp.validate_public_resolution(normalized)
    max_pages = max(1, min(int(max_pages), HARD_MAX_PAGES))
    max_depth = max(0, min(int(max_depth), HARD_MAX_DEPTH))
    delay_seconds = max(DEFAULT_DELAY_SECONDS, float(delay_seconds))
    if render_mode not in {"auto", "static", "browser"}:
        raise PermanentError("render mode must be auto, static, or browser")
    if render_mode in {"auto", "browser"} and not browser_available():
        raise PermanentError("Hardened Website Scraper browser runtime is unavailable.")
    root_host = urllib.parse.urlsplit(normalized).hostname or ""
    crawl_id = crawl_id_for(normalized)
    rec = {
        "crawl_id": crawl_id,
        "root_url": safe_text(url, 4096),
        "normalized_root_url": normalized,
        "root_host": root_host.lower(),
        "allowed_hosts": [root_host.lower()],
        "include_subdomains": bool(include_subdomains),
        "max_pages": max_pages,
        "max_depth": max_depth,
        "render_mode": render_mode,
        "delay_seconds": delay_seconds,
        "status": "queued",
        "attempt": 0,
        "submitted_at": now_iso(),
        "updated_at": now_iso(),
        "completed_at": None,
        "retry_at": None,
        "last_error": None,
        "last_fetch_at": None,
        "robots": {},
        "pages": {
            normalized: {
                "url": normalized,
                "depth": 0,
                "discovered_from": None,
                "status": "pending",
                "webpage_request_id": None,
                "browser_attempt": 0,
                "retry_at": None,
                "last_error": None,
                "capture_result": None,
                "final_note_path": None,
            }
        },
        "manifest_json_path": None,
        "manifest_note_path": None,
    }
    def mutate(state):
        state["crawls"][crawl_id] = rec
    mutate_state(mutate)
    append_event({"schema_version": SCHEMA_VERSION, "event": "crawl_queued", "at": now_iso(), "crawl_id": crawl_id, "root_url": normalized})
    return {"status": "completed", "result": "queued", "crawl_id": crawl_id, "normalized_root_url": normalized, "max_pages": max_pages, "max_depth": max_depth, "render_mode": render_mode}

def webpage_record(request_id: str, wp):
    state = wp.load_state()
    return (state.get("requests") or {}).get(request_id)

def raw_for_webpage_record(rec: dict, wp) -> tuple[str, str]:
    raw_path = rec.get("raw_archive_path")
    final_url = rec.get("final_url") or rec.get("normalized_url")
    if not raw_path and rec.get("url_id"):
        url_rec = (wp.load_state().get("urls") or {}).get(rec["url_id"], {})
        raw_path = url_rec.get("latest_raw_archive_path")
        final_url = url_rec.get("latest_final_url") or final_url
    if not raw_path:
        raise RuntimeError("Completed webpage record has no raw archive path.")
    path = Path(raw_path)
    if not path.is_file():
        raise RuntimeError(f"Completed webpage raw archive is missing: {path}")
    return path.read_text(encoding="utf-8", errors="replace"), final_url

def add_discovered_pages(crawl: dict, from_url: str, depth: int, html_text: str, final_url: str, wp):
    if depth >= int(crawl["max_depth"]):
        return
    links = extract_links(html_text, final_url, crawl, wp)
    pages = crawl["pages"]
    for link in links:
        if len(pages) >= int(crawl["max_pages"]):
            break
        if link in pages:
            continue
        pages[link] = {
            "url": link,
            "depth": depth + 1,
            "discovered_from": from_url,
            "status": "pending",
            "webpage_request_id": None,
            "browser_attempt": 0,
            "retry_at": None,
            "last_error": None,
            "capture_result": None,
            "final_note_path": None,
        }

def complete_from_webpage(crawl: dict, page: dict, rec: dict, wp):
    html_text, final_url = raw_for_webpage_record(rec, wp)
    page["status"] = "completed" if rec.get("status") == "completed" else "duplicate"
    page["capture_result"] = rec.get("status")
    page["final_note_path"] = rec.get("final_note_path") or rec.get("duplicate_of_note")
    page["final_url"] = final_url
    page["completed_at"] = now_iso()
    page["last_error"] = None
    if int(page.get("depth", 0)) == 0:
        final_host = (urllib.parse.urlsplit(final_url).hostname or "").lower()
        if final_host and final_host not in crawl.setdefault("allowed_hosts", []):
            crawl["allowed_hosts"].append(final_host)
    add_discovered_pages(crawl, page["url"], int(page["depth"]), html_text, final_url, wp)

def browser_failure_source(crawl_id: str, url: str) -> str:
    return f"{crawl_id}:{sha256_text(url)[:16]}"

def mark_browser_failure(crawl: dict, page: dict, exc: Exception, retryable: bool):
    attempt = int(page.get("browser_attempt", 0) or 0) + 1
    page["browser_attempt"] = attempt
    source_id = browser_failure_source(crawl["crawl_id"], page["url"])
    if retryable and attempt < TOTAL_ATTEMPTS:
        delay = BACKOFF_SECONDS[attempt - 1]
        retry_at = now_dt() + timedelta(seconds=delay)
        page["status"] = "browser_retry_wait"
        page["retry_at"] = retry_at.isoformat().replace("+00:00", "Z")
        page["last_error"] = safe_text(exc, 1500)
        failure_control([
            "register", "--pipeline", PIPELINE, "--source-id", source_id,
            "--source-name", page["url"], "--disposition", "failed_retryable",
            "--attempt", str(attempt), "--retry-at", page["retry_at"],
            "--error", page["last_error"],
        ])
    else:
        page["status"] = "failed_permanent"
        page["retry_at"] = None
        page["last_error"] = safe_text(exc, 1500)
        failure_control([
            "register", "--pipeline", PIPELINE, "--source-id", source_id,
            "--source-name", page["url"], "--disposition", "failed_permanent",
            "--attempt", str(attempt), "--error", page["last_error"],
        ])

def resolve_browser_failure(crawl: dict, page: dict):
    failure_control([
        "resolve", "--pipeline", PIPELINE,
        "--source-id", browser_failure_source(crawl["crawl_id"], page["url"]),
        "--message", "Website Scraper browser-rendered page completed successfully.",
    ])

def render_and_publish(crawl: dict, page: dict, wp):
    try:
        rendered = browser_render(page["url"])
        result = wp.publish_rendered_capture(
            page["url"], rendered["html"], rendered["final_url"], rendered["http_status"], rendered["renderer"]
        )
        processing = result.get("processing") or {}
        r = processing.get("result")
        request_id = processing.get("request_id") or (result.get("queue") or {}).get("request_id")
        if request_id:
            page["webpage_request_id"] = request_id
        if r in {"completed", "duplicate_content", "reconciled_published_capture"}:
            rec = webpage_record(request_id, wp) if request_id else None
            if not rec:
                raise RetryableError("Rendered webpage published but canonical request state was not readable yet.")
            complete_from_webpage(crawl, page, rec, wp)
            page["capture_mode"] = "browser_rendered_dom"
            resolve_browser_failure(crawl, page)
            return
        if r in {"retry_wait", "queued", "queued_worker_busy", "not_due_or_not_processable", "duplicate_pending"}:
            page["status"] = "awaiting_static"
            return
        if r == "failed_permanent":
            raise PermanentError(processing.get("last_error") or "Rendered webpage publication failed permanently.")
        raise RetryableError(f"Unexpected rendered webpage publication result: {r}")
    except PermanentError as exc:
        mark_browser_failure(crawl, page, exc, False)
    except RetryableError as exc:
        mark_browser_failure(crawl, page, exc, True)
    except Exception as exc:
        mark_browser_failure(crawl, page, RuntimeError(f"{type(exc).__name__}: {exc}"), True)

def static_failure_should_use_browser(rec: dict) -> bool:
    text = str(rec.get("last_error") or "").lower()
    hints = [
        "not contain enough readable static text",
        "javascript",
        "browser-based scraper",
        "browser based scraper",
    ]
    return any(h in text for h in hints)

def start_static(crawl: dict, page: dict, wp):
    result = wp.capture(page["url"])
    request_id = result.get("request_id") or (result.get("queue") or {}).get("request_id")
    if request_id:
        page["webpage_request_id"] = request_id
    processing = result.get("processing") or {}
    r = processing.get("result")
    if not r:
        r = result.get("result")
    if r in {"completed", "duplicate_content", "reconciled_published_capture"}:
        rec = webpage_record(request_id, wp)
        if not rec:
            raise RetryableError("Webpage capture completed but canonical request state was not readable yet.")
        complete_from_webpage(crawl, page, rec, wp)
        page["capture_mode"] = rec.get("capture_mode") or "static_http"
        return
    if r in {"queued", "queued_worker_busy", "retry_wait", "duplicate_pending", "not_due_or_not_processable"}:
        page["status"] = "awaiting_static"
        return
    if r == "failed_permanent":
        rec = webpage_record(request_id, wp) or {}
        if crawl["render_mode"] == "auto" and static_failure_should_use_browser(rec):
            page["status"] = "pending_browser"
            render_and_publish(crawl, page, wp)
        else:
            page["status"] = "failed_permanent"
            page["last_error"] = rec.get("last_error") or "Static webpage import failed permanently."
        return
    raise RetryableError(f"Unexpected canonical webpage capture result: {r}")

def revisit_static(crawl: dict, page: dict, wp):
    request_id = page.get("webpage_request_id")
    if not request_id:
        page["status"] = "pending"
        return
    rec = webpage_record(request_id, wp)
    if not rec:
        page["status"] = "pending"
        page["webpage_request_id"] = None
        return
    status = rec.get("status")
    if status in {"queued", "processing", "retry_wait"}:
        return
    if status in {"completed", "duplicate"}:
        complete_from_webpage(crawl, page, rec, wp)
        page["capture_mode"] = rec.get("capture_mode") or "static_http"
        return
    if status == "failed_permanent":
        if crawl["render_mode"] == "auto" and static_failure_should_use_browser(rec):
            page["status"] = "pending_browser"
            render_and_publish(crawl, page, wp)
        else:
            page["status"] = "failed_permanent"
            page["last_error"] = rec.get("last_error") or "Static webpage import failed permanently."
        return

def enforce_delay(crawl: dict, robots_delay: float):
    delay = max(float(crawl["delay_seconds"]), float(robots_delay or 0))
    last = parse_time(crawl.get("last_fetch_at"))
    if last:
        remain = delay - (now_dt() - last).total_seconds()
        if remain > 0:
            time.sleep(min(remain, 30))
    crawl["last_fetch_at"] = now_iso()

def process_page(crawl: dict, page: dict, wp):
    if page["status"] == "awaiting_static":
        revisit_static(crawl, page, wp)
        return
    if page["status"] == "browser_retry_wait":
        retry_at = parse_time(page.get("retry_at"))
        if retry_at and retry_at > now_dt():
            return
        page["status"] = "pending_browser"
    if page["status"] not in {"pending", "pending_browser"}:
        return

    allowed, robots_delay = robots_policy(crawl, page["url"], wp)
    if not allowed:
        page["status"] = "skipped_robots"
        page["last_error"] = "robots.txt disallows this URL for SecondBrainWebsiteScraper."
        return
    enforce_delay(crawl, robots_delay)
    if crawl["render_mode"] == "browser" or page["status"] == "pending_browser":
        render_and_publish(crawl, page, wp)
    else:
        start_static(crawl, page, wp)

def terminal_page(status: str) -> bool:
    return status in {"completed", "duplicate", "failed_permanent", "skipped_robots"}

def finalize_crawl(crawl: dict):
    pages = list(crawl["pages"].values())
    if not pages or not all(terminal_page(p.get("status")) for p in pages):
        crawl["status"] = "queued"
        crawl["updated_at"] = now_iso()
        return False
    failures = [p for p in pages if p.get("status") in {"failed_permanent", "skipped_robots"}]
    crawl["status"] = "completed_with_warning" if failures else "completed"
    crawl["completed_at"] = now_iso()
    crawl["updated_at"] = crawl["completed_at"]
    write_manifest(crawl)
    failure_control(["resolve", "--pipeline", PIPELINE, "--source-id", crawl["crawl_id"], "--message", "Website crawl reached a terminal isolated result."])
    append_event({
        "schema_version": SCHEMA_VERSION,
        "event": crawl["status"],
        "at": now_iso(),
        "crawl_id": crawl["crawl_id"],
        "root_url": crawl["normalized_root_url"],
        "pages_total": len(pages),
        "pages_completed": sum(1 for p in pages if p.get("status") in {"completed", "duplicate"}),
        "pages_failed_or_blocked": len(failures),
    })
    return True

def write_manifest(crawl: dict):
    MANIFEST_ROOT.mkdir(parents=True, exist_ok=True)
    NOTE_DIR.mkdir(parents=True, exist_ok=True)
    cid = crawl["crawl_id"]
    json_path = MANIFEST_ROOT / f"{cid}.json"
    note_path = NOTE_DIR / f"{cid}_website-scrape.md"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "crawl_id": cid,
        "root_url": crawl["normalized_root_url"],
        "submitted_at": crawl["submitted_at"],
        "completed_at": crawl["completed_at"],
        "status": crawl["status"],
        "policy": {
            "max_pages": crawl["max_pages"],
            "max_depth": crawl["max_depth"],
            "render_mode": crawl["render_mode"],
            "include_subdomains": crawl["include_subdomains"],
            "delay_seconds": crawl["delay_seconds"],
            "robots_txt_honored": True,
            "nofollow_honored": True,
            "login_and_access_control_bypass": False,
        },
        "pages": list(crawl["pages"].values()),
    }
    atomic_json(json_path, manifest)

    lines = [
        "---",
        f"title: {yaml_string('Website Scrape — ' + crawl['root_host'])}",
        'type: "website-scrape-manifest"',
        'content_origin: "system_generated"',
        'status: "review"',
        f"crawl_id: {yaml_string(cid)}",
        f"root_url: {yaml_string(crawl['normalized_root_url'])}",
        f"submitted_at: {yaml_string(crawl['submitted_at'])}",
        f"completed_at: {yaml_string(crawl['completed_at'])}",
        f"crawl_status: {yaml_string(crawl['status'])}",
        f"max_pages: {int(crawl['max_pages'])}",
        f"max_depth: {int(crawl['max_depth'])}",
        f"render_mode: {yaml_string(crawl['render_mode'])}",
        f"include_subdomains: {str(bool(crawl['include_subdomains'])).lower()}",
        "robots_txt_honored: true",
        "nofollow_honored: true",
        "login_bypass_enabled: false",
        f"manifest_json_path: {yaml_string(json_path)}",
        "---",
        "",
        f"# Website Scrape — {safe_text(crawl['root_host'], 200)}",
        "",
        f"- Root: <{crawl['normalized_root_url']}>",
        f"- Result: **{crawl['status']}**",
        f"- Pages represented: **{len(crawl['pages'])}**",
        f"- Render mode: **{crawl['render_mode']}**",
        "",
        "## Page Results",
        "",
        "| Depth | Result | URL | Canonical note |",
        "|---:|---|---|---|",
    ]
    for page in sorted(crawl["pages"].values(), key=lambda x: (int(x.get("depth", 0)), x.get("url", ""))):
        url = safe_text(page.get("url"), 4096).replace("|", "%7C")
        note = safe_text(page.get("final_note_path"), 4096).replace("|", "\\|") or "—"
        lines.append(f"| {int(page.get('depth',0))} | {page.get('status')} | <{url}> | `{note}` |")
    lines += [
        "",
        "## Crawl Contract",
        "",
        "- This manifest is system-generated; captured webpage notes remain separate canonical page records.",
        "- robots.txt and link-level/meta nofollow were honored.",
        "- Same-host crawling is the default; subdomains require explicit opt-in.",
        "- Browser rendering is isolated and used only for public pages under the selected render policy.",
        "- Login, CAPTCHA, paywall, and access-control bypass are disabled.",
        "",
    ]
    atomic_text(note_path, "\n".join(lines), 0o644)
    crawl["manifest_json_path"] = str(json_path)
    crawl["manifest_note_path"] = str(note_path)

def process_crawl(crawl: dict, page_budget: int, wp):
    crawl["status"] = "processing"
    crawl["updated_at"] = now_iso()
    try:
        processed = 0
        for url in list(crawl["pages"].keys()):
            if processed >= page_budget:
                break
            page = crawl["pages"][url]
            if terminal_page(page.get("status")):
                continue
            before = page.get("status")
            process_page(crawl, page, wp)
            if page.get("status") != before or before in {"pending", "pending_browser", "browser_retry_wait"}:
                processed += 1
        finalize_crawl(crawl)
        crawl["attempt"] = 0
        crawl["retry_at"] = None
        crawl["last_error"] = None
        return processed
    except Exception as exc:
        attempt = int(crawl.get("attempt", 0) or 0) + 1
        crawl["attempt"] = attempt
        crawl["last_error"] = safe_text(f"{type(exc).__name__}: {exc}", 2000)
        if attempt < TOTAL_ATTEMPTS:
            crawl["status"] = "queued"
            retry_at = now_dt() + timedelta(seconds=BACKOFF_SECONDS[attempt - 1])
            crawl["retry_at"] = retry_at.isoformat().replace("+00:00", "Z")
            failure_control([
                "register", "--pipeline", PIPELINE, "--source-id", crawl["crawl_id"],
                "--source-name", crawl["normalized_root_url"], "--disposition", "failed_retryable",
                "--attempt", str(attempt), "--retry-at", crawl["retry_at"], "--error", crawl["last_error"],
            ])
        else:
            crawl["status"] = "failed_permanent"
            crawl["retry_at"] = None
            crawl["completed_at"] = now_iso()
            failure_control([
                "register", "--pipeline", PIPELINE, "--source-id", crawl["crawl_id"],
                "--source-name", crawl["normalized_root_url"], "--disposition", "failed_permanent",
                "--attempt", str(attempt), "--error", crawl["last_error"],
            ])
            write_manifest(crawl)
        append_event({
            "schema_version": SCHEMA_VERSION,
            "event": "crawl_worker_failure",
            "at": now_iso(),
            "crawl_id": crawl["crawl_id"],
            "attempt": attempt,
            "retry_at": crawl.get("retry_at"),
            "status": crawl["status"],
            "error": crawl["last_error"],
        })
        return 0

def process_due(max_crawls: int, page_budget: int) -> dict:
    with WorkerLock(nonblocking=True) as lock:
        if not lock.acquired:
            return {"status": "completed", "result": "worker_busy", "processed_crawls": 0}
        wp = load_webpage_module()
        with StateLock():
            state = load_state()
            candidates = []
            now = now_dt()
            for crawl in state["crawls"].values():
                if crawl.get("status") not in {"queued", "processing"}:
                    continue
                retry_at = parse_time(crawl.get("retry_at"))
                if retry_at and retry_at > now:
                    continue
                candidates.append(crawl)
            candidates.sort(key=lambda x: x.get("submitted_at") or "")
            results = []
            for crawl in candidates[:max_crawls]:
                count = process_crawl(crawl, page_budget, wp)
                results.append({"crawl_id": crawl["crawl_id"], "status": crawl["status"], "page_actions": count})
            save_state(state)
    return {"status": "completed", "result": "processed", "processed_crawls": len(results), "crawls": results}

def show_status():
    state = load_state()
    recompute_summary(state)
    unresolved = []
    for crawl in state["crawls"].values():
        if crawl.get("status") in {"queued", "processing", "failed_permanent"}:
            unresolved.append({
                "crawl_id": crawl["crawl_id"],
                "status": crawl["status"],
                "root_url": crawl["normalized_root_url"],
                "pages": len(crawl.get("pages", {})),
                "retry_at": crawl.get("retry_at"),
                "last_error": crawl.get("last_error"),
            })
    return {"status": "completed", "state_path": str(STATE_PATH), "summary": state["summary"], "unresolved": unresolved}

def get_crawl(crawl_id: str):
    rec = load_state()["crawls"].get(crawl_id)
    if not rec:
        raise PermanentError(f"Unknown crawl_id: {crawl_id}")
    return rec

def scrape(url: str, max_pages: int, max_depth: int, render_mode: str, include_subdomains: bool, delay_seconds: float, timeout: int) -> dict:
    queued = queue_crawl(url, max_pages, max_depth, render_mode, include_subdomains, delay_seconds)
    crawl_id = queued["crawl_id"]
    deadline = time.time() + max(1, timeout)
    while time.time() < deadline:
        process_due(1, 5)
        rec = get_crawl(crawl_id)
        if rec.get("status") in {"completed", "completed_with_warning", "failed_permanent"}:
            return {
                "status": "completed" if rec["status"] != "failed_permanent" else "failed",
                "queue": queued,
                "processing": {
                    "result": rec["status"],
                    "crawl_id": crawl_id,
                    "pages_total": len(rec.get("pages", {})),
                    "pages_completed_or_duplicate": sum(1 for p in rec.get("pages", {}).values() if p.get("status") in {"completed", "duplicate"}),
                    "pages_failed_or_blocked": sum(1 for p in rec.get("pages", {}).values() if p.get("status") in {"failed_permanent", "skipped_robots"}),
                    "manifest_json_path": rec.get("manifest_json_path"),
                    "manifest_note_path": rec.get("manifest_note_path"),
                    "last_error": rec.get("last_error"),
                },
            }
        time.sleep(5)
    rec = get_crawl(crawl_id)
    return {
        "status": "completed",
        "queue": queued,
        "processing": {
            "result": "queued_wait_timeout",
            "crawl_id": crawl_id,
            "crawl_status": rec.get("status"),
            "pages_total": len(rec.get("pages", {})),
            "note": "The crawl remains safely queued; the Website Scraper timer will continue it.",
        },
    }

def self_test():
    if TOTAL_ATTEMPTS != MAX_RETRIES + 1 or BACKOFF_SECONDS != [60, 300, 900]:
        raise RuntimeError("Retry policy constants are invalid.")
    wp = load_webpage_module()
    normalized = strip_tracking("https://Example.com/a?utm_source=x&keep=1#fragment", wp)
    if normalized != "https://example.com/a?keep=1":
        raise RuntimeError(f"Tracking-normalization self-test failed: {normalized}")
    parser = LinkParser()
    parser.feed('<html><head><meta name="robots" content="index,follow"></head><body><a href="/a">A</a><a href="/b" rel="nofollow">B</a></body></html>')
    if parser.meta_nofollow or parser.links != [("/a", False), ("/b", True)]:
        raise RuntimeError("Link/nofollow parser self-test failed.")
    scope = {"root_host": "example.com", "allowed_hosts": ["example.com"], "include_subdomains": True}
    if host_allowed("evil-example.com", scope) or not host_allowed("sub.example.com", scope):
        raise RuntimeError("Host-scope self-test failed.")
    if not callable(getattr(wp, "publish_rendered_capture", None)):
        raise RuntimeError("Canonical browser-rendered publish API is missing.")
    return {
        "status": "completed",
        "mode": "self_test",
        "policy": {
            "default_max_pages": DEFAULT_MAX_PAGES,
            "hard_max_pages": HARD_MAX_PAGES,
            "default_max_depth": DEFAULT_MAX_DEPTH,
            "hard_max_depth": HARD_MAX_DEPTH,
            "minimum_delay_seconds": DEFAULT_DELAY_SECONDS,
            "robots_txt_honored": True,
            "nofollow_honored": True,
            "same_host_default": True,
            "browser_renderer_available": browser_available(),
            "login_access_control_bypass": False,
            "canonical_webpage_publish_api": True,
        },
    }

def main():
    p = argparse.ArgumentParser(description="Bounded, provenance-preserving Second Brain website scraper.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scrape")
    s.add_argument("url")
    s.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    s.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    s.add_argument("--render", choices=["auto", "static", "browser"], default="auto")
    s.add_argument("--include-subdomains", action="store_true")
    s.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    s.add_argument("--timeout", type=int, default=1800)

    q = sub.add_parser("queue")
    q.add_argument("url")
    q.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    q.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    q.add_argument("--render", choices=["auto", "static", "browser"], default="auto")
    q.add_argument("--include-subdomains", action="store_true")
    q.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)

    d = sub.add_parser("process-due")
    d.add_argument("--max-crawls", type=int, default=3)
    d.add_argument("--page-budget", type=int, default=3)

    sub.add_parser("status")
    sub.add_parser("list")
    sub.add_parser("self-test")

    args = p.parse_args()
    if os.geteuid() == 0 and args.command not in {"self-test", "process-due"}:
        raise PermanentError("Run Website Scraper CLI commands as theadmin without sudo.")

    if args.command == "scrape":
        result = scrape(args.url, args.max_pages, args.max_depth, args.render, args.include_subdomains, args.delay, args.timeout)
    elif args.command == "queue":
        result = queue_crawl(args.url, args.max_pages, args.max_depth, args.render, args.include_subdomains, args.delay)
    elif args.command == "process-due":
        result = process_due(max(1, min(args.max_crawls, 10)), max(1, min(args.page_budget, 20)))
    elif args.command in {"status", "list"}:
        result = show_status()
    elif args.command == "self-test":
        result = self_test()
    else:
        raise RuntimeError("Unsupported command.")
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(1)
