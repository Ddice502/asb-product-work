#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import html
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
WORKER_VERSION = "1.1.0"
PIPELINE = "webpage_import"
MAX_RETRIES = 3
TOTAL_ATTEMPTS = 4
BACKOFF_SECONDS = [60, 300, 900]
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MIN_TEXT_CHARS = 100
MIN_ALNUM_CHARS = 60
MAX_EXTRACTED_CHARS = 2_000_000
MAX_EMBEDDED_CHARS = 150_000
REQUEST_RETENTION_DAYS = 183

AI = Path(os.environ.get("SECOND_BRAIN_ROOT", "/AI"))
STATE_DIR = AI / "State/Second Brain/Webpage Import"
STATE_PATH = STATE_DIR / "current.json"
LOCK_PATH = STATE_DIR / "worker.lock"
ARCHIVE_ROOT = AI / "Archive/Webpages"
ORIGINAL_ROOT = ARCHIVE_ROOT / "Originals"
EXTRACTED_ROOT = ARCHIVE_ROOT / "Extracted"
FAILED_ROOT = ARCHIVE_ROOT / "Failed"
NOTE_DIR = AI / "Knowledge/Second Brain/00 Inbox/Webpages"
LOG_DIR = AI / "Logs/Pipelines/Webpages"
EVENT_LOG = LOG_DIR / "webpage-import-events.jsonl"
FAILURE_CONTROL = AI / "Scripts/Second Brain Failure Control/failure_control.py"

USER_AGENT = "SecondBrainWebpageImport/1.0 (+private personal archive)"
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}

class RetryableError(RuntimeError):
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after

class PermanentError(RuntimeError):
    pass

def now_dt() -> datetime:
    return datetime.now(timezone.utc)

def now_iso() -> str:
    return now_dt().isoformat().replace("+00:00", "Z")

def parse_time(value) -> datetime | None:
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

def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass

def atomic_bytes(path: Path, data: bytes, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass

def atomic_text(path: Path, text: str, mode: int = 0o640) -> None:
    atomic_bytes(path, text.encode("utf-8"), mode)

def append_event(event: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

def base_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "worker_version": WORKER_VERSION,
        "updated_at": now_iso(),
        "policy": {
            "max_retries": MAX_RETRIES,
            "total_attempts": TOTAL_ATTEMPTS,
            "progressive_backoff_seconds": BACKOFF_SECONDS,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "private_and_local_targets_blocked": True,
            "allowed_schemes": ["http", "https"],
            "allowed_ports": [80, 443],
        },
        "requests": {},
        "urls": {},
        "summary": {},
    }

def recompute_summary(state: dict) -> None:
    counts = {
        "queued": 0,
        "processing": 0,
        "retry_wait": 0,
        "completed": 0,
        "duplicate": 0,
        "failed_permanent": 0,
    }
    for rec in state.get("requests", {}).values():
        status = rec.get("status")
        if status in counts:
            counts[status] += 1
    state["summary"] = counts
    state["updated_at"] = now_iso()

def prune_requests(state: dict) -> None:
    cutoff = now_dt() - timedelta(days=REQUEST_RETENTION_DAYS)
    remove = []
    for request_id, rec in state.get("requests", {}).items():
        if rec.get("status") not in {"completed", "duplicate"}:
            continue
        d = parse_time(rec.get("completed_at") or rec.get("updated_at"))
        if d and d < cutoff:
            remove.append(request_id)
    for request_id in remove:
        state["requests"].pop(request_id, None)

def load_state() -> dict:
    if not STATE_PATH.exists():
        state = base_state()
        atomic_json(STATE_PATH, state)
        return state
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Webpage Import state is invalid JSON: {exc}")
    if not isinstance(state, dict):
        raise RuntimeError("Webpage Import state is not an object.")
    state.setdefault("requests", {})
    state.setdefault("urls", {})
    return state

class StateLock:
    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.f = LOCK_PATH.open("a+")
        fcntl.flock(self.f.fileno(), fcntl.LOCK_EX)
        return self
    def __exit__(self, exc_type, exc, tb):
        fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        self.f.close()

def update_state(mutator):
    with StateLock():
        state = load_state()
        mutator(state)
        prune_requests(state)
        recompute_summary(state)
        atomic_json(STATE_PATH, state)
        return state

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def safe_text(value, limit=1000) -> str:
    text = str(value or "").replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:limit].strip()

def yaml_string(value) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)

def slugify(value: str, maximum=80) -> str:
    value = safe_text(value, 500)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return (value[:maximum].strip("-") or "webpage")

def normalize_url(url: str) -> str:
    value = safe_text(url, 4096)
    if not value:
        raise PermanentError("URL is empty.")
    if any(ord(ch) < 32 for ch in value):
        raise PermanentError("URL contains control characters.")
    try:
        parts = urllib.parse.urlsplit(value)
    except Exception as exc:
        raise PermanentError(f"URL is invalid: {exc}")
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise PermanentError("Only http:// and https:// URLs are allowed.")
    if not parts.hostname:
        raise PermanentError("URL has no hostname.")
    if parts.username is not None or parts.password is not None:
        raise PermanentError("URLs containing embedded credentials are not allowed.")
    try:
        host = parts.hostname.encode("idna").decode("ascii").lower()
    except Exception:
        raise PermanentError("URL hostname cannot be normalized.")
    try:
        port = parts.port
    except ValueError as exc:
        raise PermanentError(f"URL port is invalid: {exc}")
    default_port = 443 if scheme == "https" else 80
    if port is not None and port not in {80, 443}:
        raise PermanentError("Only TCP ports 80 and 443 are allowed for webpage imports.")
    if port == default_port:
        port = None

    # Block private/local IP literals before any network access.
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        if not literal.is_global:
            raise PermanentError("Private, loopback, link-local, multicast, reserved, and other non-public IP targets are blocked.")
    except ValueError:
        pass

    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if port:
        netloc += f":{port}"

    path = parts.path or "/"
    path = urllib.parse.quote(path, safe="/:@!$&'()*+,;=-._~%")
    query = urllib.parse.quote(parts.query, safe="=&?/:;+,%@!$'()*-._~")
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))

def validate_public_resolution(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    if not host:
        raise PermanentError("URL has no hostname.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RetryableError(f"DNS resolution failed for {host}: {exc}")
    if not results:
        raise RetryableError(f"DNS resolution returned no addresses for {host}.")
    addresses = set()
    for item in results:
        try:
            addresses.add(item[4][0].split("%", 1)[0])
        except Exception:
            continue
    if not addresses:
        raise RetryableError(f"DNS resolution returned no usable addresses for {host}.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise PermanentError(f"DNS returned an invalid address for {host}: {address}")
        if not ip.is_global:
            raise PermanentError(
                f"Target {host} resolves to non-public address {address}; private/local targets are blocked."
            )

def retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    if text.isdigit():
        return max(0, min(int(text), 3600))
    try:
        d = parsedate_to_datetime(text)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        seconds = int((d.astimezone(timezone.utc) - now_dt()).total_seconds())
        return max(0, min(seconds, 3600))
    except Exception:
        return None

class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirects += 1
        if self.redirects > 5:
            raise PermanentError("Webpage exceeded the five-redirect safety limit.")
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        normalized = normalize_url(resolved)
        if urllib.parse.urlsplit(req.full_url).scheme == "https" and urllib.parse.urlsplit(normalized).scheme == "http":
            raise PermanentError("HTTPS-to-HTTP redirect downgrade is blocked.")
        validate_public_resolution(normalized)
        return super().redirect_request(req, fp, code, msg, headers, normalized)

def capped_decompress(raw: bytes, wbits: int) -> bytes:
    try:
        d = zlib.decompressobj(wbits)
        out = d.decompress(raw, MAX_RESPONSE_BYTES + 1)
        if len(out) > MAX_RESPONSE_BYTES or d.unconsumed_tail:
            raise PermanentError(
                f"Decompressed webpage response exceeded the {MAX_RESPONSE_BYTES} byte safety limit."
            )
        remaining = MAX_RESPONSE_BYTES + 1 - len(out)
        out += d.flush(max(1, remaining))
        if len(out) > MAX_RESPONSE_BYTES:
            raise PermanentError(
                f"Decompressed webpage response exceeded the {MAX_RESPONSE_BYTES} byte safety limit."
            )
        return out
    except PermanentError:
        raise
    except Exception as exc:
        raise RetryableError(f"Could not decompress webpage response: {exc}")

def decode_response(raw: bytes, headers) -> tuple[bytes, str, str]:
    encoding = (headers.get("Content-Encoding") or "").lower().strip()
    if encoding == "gzip":
        raw = capped_decompress(raw, 16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        try:
            raw = capped_decompress(raw, zlib.MAX_WBITS)
        except RetryableError:
            raw = capped_decompress(raw, -zlib.MAX_WBITS)
    elif encoding and encoding != "identity":
        raise PermanentError(f"Unsupported HTTP content encoding: {encoding}")

    content_type = headers.get_content_type() if hasattr(headers, "get_content_type") else "application/octet-stream"
    charset = headers.get_content_charset() if hasattr(headers, "get_content_charset") else None
    if not charset:
        prefix = raw[:8192].decode("ascii", errors="ignore")
        m = re.search(r'(?i)<meta[^>]+charset=["\']?\s*([a-z0-9._-]+)', prefix)
        if not m:
            m = re.search(r'(?i)charset\s*=\s*([a-z0-9._-]+)', prefix)
        charset = m.group(1) if m else "utf-8"
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
        charset = "utf-8"
    return raw, text, charset

def fetch_url(url: str) -> dict:
    normalized = normalize_url(url)
    validate_public_resolution(normalized)
    redirects = SafeRedirectHandler()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        redirects,
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    req = urllib.request.Request(
        normalized,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    try:
        with opener.open(req, timeout=30) as resp:
            status = int(getattr(resp, "status", 200))
            final_url = normalize_url(resp.geturl())
            validate_public_resolution(final_url)
            length = resp.headers.get("Content-Length")
            if length and str(length).isdigit() and int(length) > MAX_RESPONSE_BYTES:
                raise PermanentError(
                    f"Webpage response is larger than the {MAX_RESPONSE_BYTES} byte safety limit."
                )
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise PermanentError(
                    f"Webpage response exceeded the {MAX_RESPONSE_BYTES} byte safety limit."
                )
            raw, decoded, charset = decode_response(raw, resp.headers)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise PermanentError(
                    f"Decompressed webpage response exceeded the {MAX_RESPONSE_BYTES} byte safety limit."
                )
            content_type = resp.headers.get_content_type()
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise PermanentError(
                    f"Unsupported webpage Content-Type {content_type!r}; use the document pipeline for non-HTML files."
                )
            return {
                "requested_url": normalized,
                "final_url": final_url,
                "redirect_count": redirects.redirects,
                "status": status,
                "content_type": content_type,
                "charset": charset,
                "raw": raw,
                "decoded": decoded,
                "etag": safe_text(resp.headers.get("ETag"), 500),
                "last_modified": safe_text(resp.headers.get("Last-Modified"), 500),
                "capture_mode": "static_http",
                "javascript_rendered": False,
                "renderer": None,
            }
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        msg = f"HTTP {status} while fetching {normalized}"
        if status in RETRYABLE_HTTP:
            raise RetryableError(msg, retry_after_seconds(exc.headers.get("Retry-After")))
        raise PermanentError(msg)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError):
            raise PermanentError(f"TLS validation failed: {reason}")
        raise RetryableError(f"Network request failed: {reason}")
    except TimeoutError as exc:
        raise RetryableError(f"Webpage request timed out: {exc}")
    except socket.timeout as exc:
        raise RetryableError(f"Webpage request timed out: {exc}")

EXCLUDED = {"script", "style", "noscript", "svg", "canvas", "template", "nav", "header", "footer", "aside", "form", "dialog"}
BLOCKS = {
    "p", "div", "section", "article", "main", "br", "hr", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table",
    "tr", "td", "th", "pre", "figure", "figcaption"
}

class ReadableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.body_depth = 0
        self.exclude_depth = 0
        self.primary_depth = 0
        self.title_depth = 0
        self.h1_depth = 0
        self.title_parts = []
        self.h1_parts = []
        self.body_parts = []
        self.primary_parts = []
        self.meta = {}
        self.canonical_href = None

    def _append(self, text: str):
        if self.body_depth <= 0 or self.exclude_depth > 0:
            return
        self.body_parts.append(text)
        if self.primary_depth > 0:
            self.primary_parts.append(text)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr = {str(k).lower(): (v or "") for k, v in attrs}
        if tag == "body":
            self.body_depth += 1
        if tag in EXCLUDED:
            self.exclude_depth += 1
        if tag in {"article", "main"} and self.exclude_depth == 0:
            self.primary_depth += 1
        if tag == "title":
            self.title_depth += 1
        if tag == "h1" and self.body_depth > 0 and self.exclude_depth == 0:
            self.h1_depth += 1
        if tag == "meta":
            name = (attr.get("name") or attr.get("property") or attr.get("itemprop") or "").strip().lower()
            content = safe_text(attr.get("content"), 1000)
            if name and content and name not in self.meta:
                self.meta[name] = content
        if tag == "link":
            rel = {x.strip().lower() for x in attr.get("rel", "").split()}
            href = safe_text(attr.get("href"), 4096)
            if "canonical" in rel and href and not self.canonical_href:
                self.canonical_href = href
        if tag in BLOCKS:
            if tag == "li":
                self._append("\n• ")
            elif tag == "br":
                self._append("\n")
            else:
                self._append("\n\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in BLOCKS:
            self._append("\n")
        if tag == "h1" and self.h1_depth > 0:
            self.h1_depth -= 1
        if tag == "title" and self.title_depth > 0:
            self.title_depth -= 1
        if tag in {"article", "main"} and self.primary_depth > 0 and self.exclude_depth == 0:
            self.primary_depth -= 1
        if tag in EXCLUDED and self.exclude_depth > 0:
            self.exclude_depth -= 1
        if tag == "body" and self.body_depth > 0:
            self.body_depth -= 1

    def handle_data(self, data):
        if self.title_depth > 0:
            self.title_parts.append(data)
        if self.h1_depth > 0 and self.exclude_depth == 0:
            self.h1_parts.append(data)
        self._append(data)

def clean_extracted(parts: list[str]) -> str:
    text = "".join(parts)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_EXTRACTED_CHARS]

def choose_meta(meta: dict, names: list[str]) -> str:
    for name in names:
        value = safe_text(meta.get(name.lower()), 1000)
        if value:
            return value
    return ""

def normalize_published(value: str) -> str:
    value = safe_text(value, 300)
    if not value:
        return ""
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if d.tzinfo is None:
            return value
        return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return value

def extract_page(decoded: str, final_url: str, content_type: str) -> dict:
    if content_type == "text/plain":
        text = clean_extracted([decoded])
        return {
            "title": urllib.parse.urlsplit(final_url).hostname or "Webpage",
            "author": "",
            "published_at": "",
            "canonical_url": "",
            "text": text,
            "extractor": "plain-text-v1",
        }

    parser = ReadableHTMLParser()
    try:
        parser.feed(decoded)
        parser.close()
    except Exception as exc:
        raise PermanentError(f"HTML parser failed: {exc}")

    primary = clean_extracted(parser.primary_parts)
    body = clean_extracted(parser.body_parts)
    text = primary if len(primary) >= 200 else body

    title = (
        choose_meta(parser.meta, ["og:title", "twitter:title", "headline"])
        or safe_text(" ".join(parser.title_parts), 500)
        or safe_text(" ".join(parser.h1_parts), 500)
        or urllib.parse.urlsplit(final_url).hostname
        or "Webpage"
    )
    author = choose_meta(
        parser.meta,
        ["author", "article:author", "byl", "parsely-author", "dc.creator", "creator"],
    )
    published = normalize_published(
        choose_meta(
            parser.meta,
            [
                "article:published_time",
                "datepublished",
                "date",
                "publish-date",
                "pubdate",
                "dc.date",
                "parsely-pub-date",
            ],
        )
    )

    canonical = ""
    if parser.canonical_href:
        try:
            canonical = normalize_url(urllib.parse.urljoin(final_url, parser.canonical_href))
        except Exception:
            canonical = ""

    return {
        "title": safe_text(title, 500),
        "author": safe_text(author, 500),
        "published_at": published,
        "canonical_url": canonical,
        "text": text,
        "extractor": "readable-html-v1",
    }

def markdown_safe_text(text: str) -> str:
    # Web content is untrusted. Keep it readable while preventing Obsidian embeds,
    # wiki-links, remote Markdown images, raw HTML, and accidental headings/fences.
    out = []
    for line in text.splitlines():
        line = line.replace("\\", "\\\\")
        line = line.replace("![[", "!\\[\\[").replace("[[", "\\[\\[").replace("]]", "\\]\\]")
        line = line.replace("![", "!\\[")
        line = line.replace("<", "&lt;").replace(">", "&gt;")
        if re.match(r"^\s{0,3}(#{1,6}\s|>\s|[-+*]\s|\d+[.)]\s|```|~~~|---\s*$)", line):
            line = "\\" + line
        out.append(line)
    return "\n".join(out)

def failure_control(args: list[str]) -> dict:
    if not FAILURE_CONTROL.is_file():
        raise RuntimeError(f"Failure Control worker is missing: {FAILURE_CONTROL}")
    p = subprocess.run(
        ["python3", str(FAILURE_CONTROL), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    if p.returncode != 0:
        raise RuntimeError(f"Failure Control exited {p.returncode}: {(p.stdout or '')[-1500:]}")
    lines = [x for x in (p.stdout or "").splitlines() if x.strip()]
    if not lines:
        raise RuntimeError("Failure Control returned no output.")
    return json.loads(lines[-1])

def request_id_for(url_id: str) -> str:
    return now_dt().strftime("%Y%m%dT%H%M%S%fZ") + "-" + url_id[:12] + "-" + os.urandom(3).hex()

def queue_url(url: str) -> dict:
    normalized = normalize_url(url)
    url_id = sha256_text(normalized)
    request_id = request_id_for(url_id)
    result = {}

    def mutate(state):
        for old_id, old in state["requests"].items():
            if old.get("url_id") == url_id and old.get("status") in {"queued", "processing", "retry_wait"}:
                result.update({
                    "status": "completed",
                    "result": "duplicate_pending",
                    "request_id": old_id,
                    "url_id": url_id,
                    "normalized_url": normalized,
                })
                return
        record = {
            "request_id": request_id,
            "url_id": url_id,
            "source_url": safe_text(url, 4096),
            "normalized_url": normalized,
            "status": "queued",
            "attempt": 0,
            "submitted_at": now_iso(),
            "updated_at": now_iso(),
            "retry_at": None,
            "last_error": None,
        }
        state["requests"][request_id] = record
        result.update({
            "status": "completed",
            "result": "queued",
            "request_id": request_id,
            "url_id": url_id,
            "normalized_url": normalized,
        })

    update_state(mutate)
    append_event({
        "schema_version": SCHEMA_VERSION,
        "event": result["result"],
        "at": now_iso(),
        "request_id": result["request_id"],
        "url_id": url_id,
        "normalized_url": normalized,
    })
    return result

def get_request(request_id: str) -> dict | None:
    state = load_state()
    rec = state.get("requests", {}).get(request_id)
    return dict(rec) if isinstance(rec, dict) else None

def claim_request(request_id: str) -> dict | None:
    claimed = {}

    def mutate(state):
        rec = state["requests"].get(request_id)
        if not rec:
            return
        if rec.get("status") == "retry_wait":
            retry_at = parse_time(rec.get("retry_at"))
            if retry_at and retry_at > now_dt():
                return
        if rec.get("status") not in {"queued", "retry_wait"}:
            return
        rec["status"] = "processing"
        rec["attempt"] = int(rec.get("attempt", 0) or 0) + 1
        rec["capture_reference_at"] = rec.get("capture_reference_at") or now_iso()
        rec["processing_started_at"] = now_iso()
        rec["retry_at"] = None
        rec["updated_at"] = now_iso()
        claimed.update(rec)

    update_state(mutate)
    return claimed or None

def due_request_ids(max_items: int) -> list[str]:
    state = load_state()
    now = now_dt()
    candidates = []
    for request_id, rec in state.get("requests", {}).items():
        status = rec.get("status")
        if status == "queued":
            candidates.append((parse_time(rec.get("submitted_at")) or now, request_id))
        elif status == "retry_wait":
            retry_at = parse_time(rec.get("retry_at"))
            if retry_at and retry_at <= now:
                candidates.append((retry_at, request_id))
    candidates.sort()
    return [x[1] for x in candidates[:max_items]]

def dead_letter_file(rec: dict, error: str) -> Path:
    d = now_dt()
    directory = FAILED_ROOT / f"{d.year:04d}" / f"{d.year:04d}-{d.month:02d}"
    name = f"{rec['request_id']}_{rec['url_id'][:12]}.json"
    path = directory / name
    payload = {
        "schema_version": SCHEMA_VERSION,
        "worker_version": WORKER_VERSION,
        "request_id": rec["request_id"],
        "url_id": rec["url_id"],
        "source_url": rec["source_url"],
        "normalized_url": rec["normalized_url"],
        "attempt": rec["attempt"],
        "failed_at": now_iso(),
        "error": safe_text(error, 4000),
    }
    atomic_json(path, payload)
    return path

def mark_retry(rec: dict, error: str, retry_after_hint: int | None = None) -> dict:
    attempt = int(rec.get("attempt", 1))
    delay = BACKOFF_SECONDS[min(max(attempt - 1, 0), len(BACKOFF_SECONDS) - 1)]
    if retry_after_hint is not None:
        delay = max(delay, min(int(retry_after_hint), 3600))
    retry_at = now_dt() + timedelta(seconds=delay)
    retry_iso = retry_at.isoformat().replace("+00:00", "Z")

    failure_control([
        "register",
        "--pipeline", PIPELINE,
        "--source-id", rec["url_id"],
        "--source-name", rec["normalized_url"],
        "--disposition", "failed_retryable",
        "--attempt", str(attempt),
        "--retry-at", retry_iso,
        "--error", safe_text(error, 2000),
    ])

    def mutate(state):
        current = state["requests"].get(rec["request_id"])
        if not current:
            return
        current.update({
            "status": "retry_wait",
            "retry_at": retry_iso,
            "last_error": safe_text(error, 2000),
            "updated_at": now_iso(),
        })

    update_state(mutate)
    append_event({
        "schema_version": SCHEMA_VERSION,
        "event": "failed_retryable",
        "at": now_iso(),
        "request_id": rec["request_id"],
        "url_id": rec["url_id"],
        "attempt": attempt,
        "retry_at": retry_iso,
        "error": safe_text(error, 1000),
    })
    return {"result": "failed_retryable", "retry_at": retry_iso, "attempt": attempt}

def mark_permanent(rec: dict, error: str) -> dict:
    dead = dead_letter_file(rec, error)
    failure_control([
        "register",
        "--pipeline", PIPELINE,
        "--source-id", rec["url_id"],
        "--source-name", rec["normalized_url"],
        "--disposition", "failed_permanent",
        "--attempt", str(int(rec.get("attempt", 1))),
        "--dead-letter-path", str(dead),
        "--error", safe_text(error, 2000),
    ])

    def mutate(state):
        current = state["requests"].get(rec["request_id"])
        if not current:
            return
        current.update({
            "status": "failed_permanent",
            "dead_letter_path": str(dead),
            "last_error": safe_text(error, 2000),
            "failed_at": now_iso(),
            "updated_at": now_iso(),
        })

    update_state(mutate)
    append_event({
        "schema_version": SCHEMA_VERSION,
        "event": "failed_permanent",
        "at": now_iso(),
        "request_id": rec["request_id"],
        "url_id": rec["url_id"],
        "attempt": rec["attempt"],
        "dead_letter_path": str(dead),
        "error": safe_text(error, 1000),
        "halting": False,
    })
    return {"result": "failed_permanent", "dead_letter_path": str(dead), "attempt": rec["attempt"]}

def publication_paths(rec: dict, page: dict, fetched: dict, content_sha: str, source_sha: str):
    captured = parse_time(rec.get("capture_reference_at")) or parse_time(rec.get("submitted_at")) or now_dt()
    yyyy = captured.strftime("%Y")
    month = captured.strftime("%Y-%m")
    url8 = rec["url_id"][:8]
    content8 = content_sha[:8]
    url_parts = urllib.parse.urlsplit(rec["normalized_url"])
    path_label = Path(url_parts.path).name or "home"
    slug = slugify(f"{url_parts.hostname or 'web'}-{path_label}")
    stem = f"{captured.strftime('%Y-%m-%d')}_{url8}_{content8}_{slug}"
    raw_ext = ".txt" if fetched["content_type"] == "text/plain" else ".html"
    return {
        "raw": ORIGINAL_ROOT / yyyy / month / (stem + raw_ext),
        "text": EXTRACTED_ROOT / yyyy / month / (stem + ".txt"),
        "note": NOTE_DIR / (stem + ".md"),
        "record_id": f"webpage-{rec['url_id'][:16]}-{content_sha[:16]}",
    }

def build_note(rec: dict, page: dict, fetched: dict, paths: dict, source_sha: str, content_sha: str) -> str:
    text = page["text"]
    embedded = text[:MAX_EMBEDDED_CHARS]
    truncated = len(text) > len(embedded)
    domain = urllib.parse.urlsplit(fetched["final_url"]).hostname or ""
    captured_at = rec.get("capture_reference_at") or rec.get("submitted_at") or now_iso()

    lines = [
        "---",
        f"title: {yaml_string(page['title'])}",
        'type: "webpage-import"',
        'status: "review"',
        'source_type: "webpage"',
        f"record_id: {yaml_string(paths['record_id'])}",
        f"import_request_id: {yaml_string(rec['request_id'])}",
        f"source_url: {yaml_string(rec['source_url'])}",
        f"source_url_normalized: {yaml_string(rec['normalized_url'])}",
        f"source_final_url: {yaml_string(fetched['final_url'])}",
        f"source_domain: {yaml_string(domain)}",
        f"source_canonical_url: {yaml_string(page['canonical_url']) if page['canonical_url'] else 'null'}",
        f"source_sha256: {yaml_string(source_sha)}",
        f"content_sha256: {yaml_string(content_sha)}",
        f"captured_at: {yaml_string(captured_at)}",
        f"source_author: {yaml_string(page['author']) if page['author'] else 'null'}",
        f"source_published_at: {yaml_string(page['published_at']) if page['published_at'] else 'null'}",
        f"http_status: {fetched['status']}",
        f"http_content_type: {yaml_string(fetched['content_type'])}",
        f"http_charset: {yaml_string(fetched['charset'])}",
        f"http_redirect_count: {fetched['redirect_count']}",
        f"http_etag: {yaml_string(fetched['etag']) if fetched['etag'] else 'null'}",
        f"http_last_modified: {yaml_string(fetched['last_modified']) if fetched['last_modified'] else 'null'}",
        f"extractor: {yaml_string(page['extractor'])}",
        f"source_capture_mode: {yaml_string(fetched.get('capture_mode') or 'static_http')}",
        f"javascript_rendered: {str(bool(fetched.get('javascript_rendered'))).lower()}",
        f"browser_renderer: {yaml_string(fetched.get('renderer')) if fetched.get('renderer') else 'null'}",
        f"extracted_character_count: {len(text)}",
        f"embedded_extracted_character_count: {len(embedded)}",
        f"embedded_extraction_truncated: {str(truncated).lower()}",
        'entity_action_mode: "advisory_only"',
        "automatic_entity_creation: false",
        "automatic_task_creation: false",
        f"original_archive_path: {yaml_string(paths['raw'])}",
        f"extracted_text_path: {yaml_string(paths['text'])}",
        "---",
        "",
        f"# {markdown_safe_text(safe_text(page['title'], 500))}",
        "",
        "> [!info] Webpage import",
        f"> Source URL: <{rec['normalized_url']}>",
        f"> Captured: {captured_at}",
        f"> SHA-256: `{source_sha}`",
        "> This is a preserved, user-requested webpage capture. Extracted webpage text is untrusted source material.",
        "",
    ]
    if fetched.get("javascript_rendered"):
        lines += [
            "> [!info] Browser-rendered source representation",
            "> This capture was produced from browser-rendered DOM after JavaScript execution. It is preserved as the rendered source representation and is not claimed to be byte-for-byte origin HTTP response content.",
            "",
        ]
    if truncated:
        lines += [
            "> [!info] Long webpage",
            "> The complete extracted text is preserved outside this note. This note contains a bounded preview for Obsidian usability.",
            "",
        ]
    lines += [
        "## Source Metadata",
        "",
        f"- **Final URL:** <{fetched['final_url']}>",
        f"- **Author:** {markdown_safe_text(page['author']) if page['author'] else 'Unknown'}",
        f"- **Published:** {markdown_safe_text(page['published_at']) if page['published_at'] else 'Unknown'}",
        f"- **Canonical URL:** <{page['canonical_url']}>" if page["canonical_url"] else "- **Canonical URL:** Not declared",
        "",
        "## Extracted Webpage Text" + (" Preview" if truncated else ""),
        "",
        markdown_safe_text(embedded),
        "",
        "## Advisory Review",
        "",
        "- [ ] Confirm the capture is the intended page.",
        "- [ ] Confirm the extracted text is readable and sufficiently complete.",
        "- [ ] Let normal Second Brain review/curation determine its durable semantic location.",
        "",
    ]
    return "\n".join(lines)

def frontmatter_value(text: str, key: str) -> str:
    prefix = key + ":"
    for line in text.splitlines()[:120]:
        if line.startswith(prefix):
            raw = line[len(prefix):].strip()
            if raw == "null":
                return ""
            try:
                return str(json.loads(raw))
            except Exception:
                return raw.strip('"').strip("'")
    return ""

def reconcile_published_request(rec: dict) -> dict | None:
    if not NOTE_DIR.exists():
        return None
    marker = f'import_request_id: "{rec["request_id"]}"'
    for note_path in NOTE_DIR.glob("*.md"):
        try:
            text = note_path.read_text(encoding="utf-8")
        except Exception:
            continue
        if marker not in text[:8000]:
            continue
        source_sha = frontmatter_value(text, "source_sha256")
        content_sha = frontmatter_value(text, "content_sha256")
        raw_path = Path(frontmatter_value(text, "original_archive_path"))
        extracted_path = Path(frontmatter_value(text, "extracted_text_path"))
        final_url = frontmatter_value(text, "source_final_url") or rec["normalized_url"]
        record_id = frontmatter_value(text, "record_id")
        title = frontmatter_value(text, "title")
        capture_mode = frontmatter_value(text, "source_capture_mode") or "static_http"
        renderer = frontmatter_value(text, "browser_renderer")
        javascript_rendered = (frontmatter_value(text, "javascript_rendered") or "").lower() == "true"
        if not source_sha or not content_sha or not record_id:
            raise RuntimeError(f"Published webpage note is missing reconciliation identity: {note_path}")
        if not raw_path.is_file() or not raw_path.is_relative_to(ORIGINAL_ROOT):
            raise RuntimeError(f"Published webpage raw archive is missing or unsafe: {raw_path}")
        if not extracted_path.is_file() or not extracted_path.is_relative_to(EXTRACTED_ROOT):
            raise RuntimeError(f"Published webpage extracted text is missing or unsafe: {extracted_path}")
        if sha256_bytes(raw_path.read_bytes()) != source_sha:
            raise RuntimeError(f"Published webpage raw archive hash mismatch: {raw_path}")
        extracted = extracted_path.read_text(encoding="utf-8").rstrip("\n")
        if sha256_text(extracted) != content_sha:
            raise RuntimeError(f"Published webpage extracted text hash mismatch: {extracted_path}")

        completed_at = now_iso()
        failure_control([
            "resolve",
            "--pipeline", PIPELINE,
            "--source-id", rec["url_id"],
            "--message", "Recovered an already-published webpage capture after interrupted state finalization.",
        ])

        def mutate(state):
            current = state["requests"].get(rec["request_id"])
            if current:
                current.update({
                    "status": "completed",
                    "source_sha256": source_sha,
                    "content_sha256": content_sha,
                    "final_url": final_url,
                    "title": title,
                    "capture_mode": capture_mode,
                    "javascript_rendered": javascript_rendered,
                    "renderer": renderer or None,
                    "raw_archive_path": str(raw_path),
                    "extracted_text_path": str(extracted_path),
                    "final_note_path": str(note_path),
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                    "last_error": None,
                    "reconciled_after_interruption": True,
                })
            old = state["urls"].get(rec["url_id"], {})
            captures = list(old.get("captures", []))
            if not any(x.get("request_id") == rec["request_id"] for x in captures if isinstance(x, dict)):
                captures = captures[-19:]
                captures.append({
                    "captured_at": completed_at,
                    "request_id": rec["request_id"],
                    "source_sha256": source_sha,
                    "content_sha256": content_sha,
                    "final_note_path": str(note_path),
                    "raw_archive_path": str(raw_path),
                    "final_url": final_url,
                    "capture_mode": capture_mode,
                    "javascript_rendered": javascript_rendered,
                    "renderer": renderer or None,
                })
            state["urls"][rec["url_id"]] = {
                **old,
                "url_id": rec["url_id"],
                "normalized_url": rec["normalized_url"],
                "latest_source_sha256": source_sha,
                "latest_content_sha256": content_sha,
                "latest_note_path": str(note_path),
                "latest_raw_archive_path": str(raw_path),
                "latest_final_url": final_url,
                "latest_capture_mode": capture_mode,
                "latest_renderer": renderer or None,
                "latest_captured_at": completed_at,
                "captures": captures,
            }

        update_state(mutate)
        append_event({
            "schema_version": SCHEMA_VERSION,
            "event": "reconciled_published_capture",
            "at": completed_at,
            "request_id": rec["request_id"],
            "url_id": rec["url_id"],
            "final_note_path": str(note_path),
        })
        return {
            "result": "reconciled_published_capture",
            "request_id": rec["request_id"],
            "final_note_path": str(note_path),
            "raw_archive_path": str(raw_path),
            "extracted_text_path": str(extracted_path),
            "capture_mode": capture_mode,
            "javascript_rendered": javascript_rendered,
            "renderer": renderer or None,
        }
    return None

def publish_capture(rec: dict, fetched: dict, page: dict) -> dict:
    text = page["text"]
    alnum = sum(ch.isalnum() for ch in text)
    if len(text) < MIN_TEXT_CHARS or alnum < MIN_ALNUM_CHARS:
        raise PermanentError(
            "The page did not contain enough readable static text. It may require JavaScript, authentication, or a browser-based scraper; use the later Website Scraper path for that case."
        )

    source_sha = sha256_bytes(fetched["raw"])
    content_sha = sha256_text(text)
    state = load_state()
    url_record = state.get("urls", {}).get(rec["url_id"], {})
    if url_record.get("latest_content_sha256") == content_sha:
        failure_control([
            "resolve",
            "--pipeline", PIPELINE,
            "--source-id", rec["url_id"],
            "--message", "Webpage fetch succeeded; content matched the latest retained capture.",
        ])

        def mutate(s):
            current = s["requests"].get(rec["request_id"])
            if current:
                current.update({
                    "status": "duplicate",
                    "source_sha256": source_sha,
                    "content_sha256": content_sha,
                    "final_url": fetched["final_url"],
                    "capture_mode": fetched.get("capture_mode") or "static_http",
                    "javascript_rendered": bool(fetched.get("javascript_rendered")),
                    "renderer": fetched.get("renderer"),
                    "duplicate_of_note": url_record.get("latest_note_path"),
                    "completed_at": now_iso(),
                    "updated_at": now_iso(),
                    "last_error": None,
                })
        update_state(mutate)
        append_event({
            "schema_version": SCHEMA_VERSION,
            "event": "duplicate_content",
            "at": now_iso(),
            "request_id": rec["request_id"],
            "url_id": rec["url_id"],
            "content_sha256": content_sha,
            "existing_note_path": url_record.get("latest_note_path"),
        })
        return {
            "result": "duplicate_content",
            "request_id": rec["request_id"],
            "existing_note_path": url_record.get("latest_note_path"),
            "content_sha256": content_sha,
        }

    paths = publication_paths(rec, page, fetched, content_sha, source_sha)
    raw_path = paths["raw"]
    text_path = paths["text"]
    note_path = paths["note"]

    # Crash-safe publication: deterministic paths are tied to the request/content identity.
    # Existing outputs are accepted only when their checksums/identity exactly match.
    if raw_path.exists():
        if sha256_bytes(raw_path.read_bytes()) != source_sha:
            raise PermanentError(f"Raw webpage archive conflict: {raw_path}")
    else:
        atomic_bytes(raw_path, fetched["raw"])

    if text_path.exists():
        if sha256_text(text_path.read_text(encoding="utf-8").rstrip("\n")) != content_sha:
            raise PermanentError(f"Extracted webpage text conflict: {text_path}")
    else:
        atomic_text(text_path, text + "\n")

    note = build_note(rec, page, fetched, paths, source_sha, content_sha)
    if note_path.exists():
        existing_note = note_path.read_text(encoding="utf-8")
        if (
            f'record_id: "{paths["record_id"]}"' not in existing_note
            or f'source_sha256: "{source_sha}"' not in existing_note
            or f'content_sha256: "{content_sha}"' not in existing_note
        ):
            raise PermanentError(f"Webpage note identity conflict: {note_path}")
    else:
        atomic_text(note_path, note, 0o644)

    # Re-read all durable outputs before state says complete.
    if sha256_bytes(raw_path.read_bytes()) != source_sha:
        raise RuntimeError("Raw webpage archive checksum verification failed.")
    if sha256_text(text_path.read_text(encoding="utf-8").rstrip("\n")) != content_sha:
        raise RuntimeError("Extracted webpage text checksum verification failed.")
    if f'record_id: "{paths["record_id"]}"' not in note_path.read_text(encoding="utf-8"):
        raise RuntimeError("Published webpage note identity verification failed.")

    completed_at = now_iso()

    # Resolve any prior retry/permanent record before committing the source state.
    # If state commit later fails, the next retry can safely reconcile the durable outputs.
    failure_control([
        "resolve",
        "--pipeline", PIPELINE,
        "--source-id", rec["url_id"],
        "--message", "Webpage import durable outputs verified successfully.",
    ])

    def mutate(state):
        current = state["requests"].get(rec["request_id"])
        if current:
            current.update({
                "status": "completed",
                "source_sha256": source_sha,
                "content_sha256": content_sha,
                "final_url": fetched["final_url"],
                "title": page["title"],
                "author": page["author"],
                "published_at": page["published_at"],
                "canonical_url": page["canonical_url"],
                "capture_mode": fetched.get("capture_mode") or "static_http",
                "javascript_rendered": bool(fetched.get("javascript_rendered")),
                "renderer": fetched.get("renderer"),
                "raw_archive_path": str(raw_path),
                "extracted_text_path": str(text_path),
                "final_note_path": str(note_path),
                "completed_at": completed_at,
                "updated_at": completed_at,
                "last_error": None,
            })
        old = state["urls"].get(rec["url_id"], {})
        captures = list(old.get("captures", []))[-19:]
        captures.append({
            "captured_at": completed_at,
            "request_id": rec["request_id"],
            "source_sha256": source_sha,
            "content_sha256": content_sha,
            "final_note_path": str(note_path),
            "raw_archive_path": str(raw_path),
            "final_url": fetched["final_url"],
            "capture_mode": fetched.get("capture_mode") or "static_http",
            "javascript_rendered": bool(fetched.get("javascript_rendered")),
            "renderer": fetched.get("renderer"),
        })
        state["urls"][rec["url_id"]] = {
            "url_id": rec["url_id"],
            "normalized_url": rec["normalized_url"],
            "latest_source_sha256": source_sha,
            "latest_content_sha256": content_sha,
            "latest_note_path": str(note_path),
            "latest_raw_archive_path": str(raw_path),
            "latest_final_url": fetched["final_url"],
            "latest_capture_mode": fetched.get("capture_mode") or "static_http",
            "latest_renderer": fetched.get("renderer"),
            "latest_captured_at": completed_at,
            "captures": captures,
        }

    update_state(mutate)
    append_event({
        "schema_version": SCHEMA_VERSION,
        "event": "completed",
        "at": completed_at,
        "request_id": rec["request_id"],
        "url_id": rec["url_id"],
        "source_sha256": source_sha,
        "content_sha256": content_sha,
        "final_note_path": str(note_path),
        "raw_archive_path": str(raw_path),
        "final_url": fetched["final_url"],
        "capture_mode": fetched.get("capture_mode") or "static_http",
        "javascript_rendered": bool(fetched.get("javascript_rendered")),
        "renderer": fetched.get("renderer"),
    })
    return {
        "result": "completed",
        "request_id": rec["request_id"],
        "url_id": rec["url_id"],
        "title": page["title"],
        "source_sha256": source_sha,
        "content_sha256": content_sha,
        "final_note_path": str(note_path),
        "raw_archive_path": str(raw_path),
        "extracted_text_path": str(text_path),
        "capture_mode": fetched.get("capture_mode") or "static_http",
        "javascript_rendered": bool(fetched.get("javascript_rendered")),
        "renderer": fetched.get("renderer"),
    }

def publish_rendered_capture(url: str, rendered_html: str, final_url: str | None = None, http_status: int = 200, renderer: str = "playwright") -> dict:
    """Publish an explicitly labeled browser-rendered DOM through the canonical webpage archive/note path.

    This function intentionally does not launch a browser. Website Scraper owns browser
    isolation and passes only the rendered DOM plus public URL provenance into this layer.
    """
    normalized = normalize_url(url)
    validate_public_resolution(normalized)
    final_normalized = normalize_url(final_url or normalized)
    validate_public_resolution(final_normalized)
    if urllib.parse.urlsplit(normalized).scheme == "https" and urllib.parse.urlsplit(final_normalized).scheme != "https":
        raise PermanentError("HTTPS-to-HTTP browser redirect downgrade is not allowed.")
    if not isinstance(rendered_html, str) or not rendered_html.strip():
        raise PermanentError("Browser renderer returned empty HTML.")
    raw = rendered_html.encode("utf-8")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise PermanentError(f"Browser-rendered DOM exceeded the {MAX_RESPONSE_BYTES} byte safety limit.")

    queued = queue_url(normalized)
    request_id = queued["request_id"]
    if queued.get("result") == "duplicate_pending":
        rec = get_request(request_id)
        if rec and rec.get("status") in {"queued", "retry_wait"}:
            pass
        else:
            return {
                "status": "completed",
                "queue": queued,
                "processing": {
                    "result": "duplicate_pending",
                    "request_id": request_id,
                    "status": rec.get("status") if rec else "unknown",
                },
            }

    rec = claim_request(request_id)
    if not rec:
        existing = get_request(request_id)
        return {
            "status": "completed",
            "queue": queued,
            "processing": {
                "result": "not_due_or_not_processable",
                "request_id": request_id,
                "status": existing.get("status") if existing else "missing",
            },
        }

    fetched = {
        "requested_url": normalized,
        "final_url": final_normalized,
        "redirect_count": 0,
        "status": int(http_status or 200),
        "content_type": "text/html",
        "charset": "utf-8",
        "raw": raw,
        "decoded": rendered_html,
        "etag": "",
        "last_modified": "",
        "capture_mode": "browser_rendered_dom",
        "javascript_rendered": True,
        "renderer": safe_text(renderer, 200) or "browser",
    }
    page = extract_page(rendered_html, final_normalized, "text/html")
    page["extractor"] = "browser-rendered-html-v1"
    try:
        result = publish_capture(rec, fetched, page)
        return {"status": "completed", "queue": queued, "processing": result}
    except RetryableError as exc:
        if int(rec["attempt"]) < TOTAL_ATTEMPTS:
            result = mark_retry(rec, str(exc), exc.retry_after)
        else:
            result = mark_permanent(rec, f"Retry limit exhausted after {TOTAL_ATTEMPTS} attempts: {exc}")
    except PermanentError as exc:
        result = mark_permanent(rec, str(exc))
    except Exception as exc:
        if int(rec["attempt"]) < TOTAL_ATTEMPTS:
            result = mark_retry(rec, f"{type(exc).__name__}: {exc}")
        else:
            result = mark_permanent(rec, f"Retry limit exhausted after {TOTAL_ATTEMPTS} attempts: {type(exc).__name__}: {exc}")
    return {"status": "completed", "queue": queued, "processing": result}


def process_request(request_id: str) -> dict:
    rec = claim_request(request_id)
    if not rec:
        existing = get_request(request_id)
        return {
            "result": "not_due_or_not_processable",
            "request_id": request_id,
            "status": existing.get("status") if existing else "missing",
        }
    try:
        reconciled = reconcile_published_request(rec)
        if reconciled:
            return reconciled
        fetched = fetch_url(rec["normalized_url"])
        page = extract_page(fetched["decoded"], fetched["final_url"], fetched["content_type"])
        return publish_capture(rec, fetched, page)
    except RetryableError as exc:
        if int(rec["attempt"]) < TOTAL_ATTEMPTS:
            return mark_retry(rec, str(exc), exc.retry_after)
        return mark_permanent(rec, f"Retry limit exhausted after {TOTAL_ATTEMPTS} attempts: {exc}")
    except PermanentError as exc:
        return mark_permanent(rec, str(exc))
    except Exception as exc:
        # Unknown worker faults get the same bounded retry contract so one bad item
        # cannot block unrelated future imports.
        if int(rec["attempt"]) < TOTAL_ATTEMPTS:
            return mark_retry(rec, f"{type(exc).__name__}: {exc}")
        return mark_permanent(
            rec,
            f"Retry limit exhausted after {TOTAL_ATTEMPTS} attempts: {type(exc).__name__}: {exc}",
        )

class WorkerLock:
    def __init__(self, nonblocking=False):
        self.nonblocking = nonblocking
        self.file = None
        self.acquired = False
    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.file = (STATE_DIR / "process.lock").open("a+")
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if self.nonblocking else 0)
        try:
            fcntl.flock(self.file.fileno(), flags)
            self.acquired = True
        except BlockingIOError:
            self.acquired = False
        return self
    def __exit__(self, exc_type, exc, tb):
        if self.file:
            if self.acquired:
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()

def process_due(max_items: int) -> dict:
    results = []
    with WorkerLock(nonblocking=True) as lock:
        if not lock.acquired:
            return {"status": "completed", "result": "worker_busy", "processed": 0, "items": []}
        for request_id in due_request_ids(max_items):
            results.append(process_request(request_id))
    return {
        "status": "completed",
        "result": "processed_due",
        "processed": len(results),
        "items": results,
    }

def capture(url: str) -> dict:
    queued = queue_url(url)
    if queued["result"] == "duplicate_pending":
        return queued
    with WorkerLock(nonblocking=True) as lock:
        if not lock.acquired:
            return {
                **queued,
                "result": "queued_worker_busy",
                "note": "The background timer will process the queued URL.",
            }
        result = process_request(queued["request_id"])
    return {
        "status": "completed",
        "queue": queued,
        "processing": result,
    }

def retry_request(request_id: str) -> dict:
    state = load_state()
    old = state.get("requests", {}).get(request_id)
    if not old:
        raise PermanentError(f"Unknown request ID: {request_id}")
    if old.get("status") != "failed_permanent":
        raise PermanentError("Only failed_permanent requests can be manually retried.")
    new_request = queue_url(old["source_url"])
    if new_request["result"] == "queued":
        def mutate(s):
            current = s["requests"].get(new_request["request_id"])
            if current:
                current["manual_retry_of"] = request_id
        update_state(mutate)
        failure_control([
            "resolve",
            "--pipeline", PIPELINE,
            "--source-id", old["url_id"],
            "--message", f"Owner manually requested a fresh webpage attempt after request {request_id}.",
        ])
    return new_request

def show_status() -> dict:
    state = load_state()
    recompute_summary(state)
    unresolved = []
    for rec in state.get("requests", {}).values():
        if rec.get("status") in {"queued", "processing", "retry_wait", "failed_permanent"}:
            unresolved.append({
                "request_id": rec.get("request_id"),
                "status": rec.get("status"),
                "attempt": rec.get("attempt"),
                "normalized_url": rec.get("normalized_url"),
                "retry_at": rec.get("retry_at"),
                "last_error": rec.get("last_error"),
                "dead_letter_path": rec.get("dead_letter_path"),
            })
    unresolved.sort(key=lambda x: x.get("request_id") or "")
    return {
        "status": "completed",
        "state_path": str(STATE_PATH),
        "summary": state["summary"],
        "unresolved": unresolved,
        "note_inbox": str(NOTE_DIR),
        "raw_archive": str(ORIGINAL_ROOT),
    }

def self_test() -> dict:
    if TOTAL_ATTEMPTS != MAX_RETRIES + 1 or BACKOFF_SECONDS != [60, 300, 900]:
        raise RuntimeError("Retry policy constants are invalid.")
    normalized = normalize_url("https://Example.COM:443/a%20b?x=1#fragment")
    if normalized != "https://example.com/a%20b?x=1":
        raise RuntimeError(f"URL normalization self-test failed: {normalized}")
    for blocked in ["http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.1.1/", "ftp://example.com/"]:
        try:
            normalize_url(blocked)
        except PermanentError:
            pass
        else:
            raise RuntimeError(f"Blocked-URL self-test unexpectedly allowed {blocked}")
    sample = """<!doctype html><html><head><title>Test Article</title>
<meta name="author" content="Example Author"><meta property="article:published_time" content="2026-08-01T12:00:00Z">
</head><body><nav>Noise</nav><main><h1>Test Article</h1><p>This is a deterministic local extraction self-test with enough readable text to prove the parser selects the main article and excludes navigation material.</p><p>Second paragraph for extraction quality validation and metadata handling.</p></main><footer>Noise</footer></body></html>"""
    page = extract_page(sample, "https://example.com/article", "text/html")
    if "deterministic local extraction self-test" not in page["text"] or "Noise" in page["text"]:
        raise RuntimeError("Readable HTML extraction self-test failed.")
    if page["title"] != "Test Article" or page["author"] != "Example Author":
        raise RuntimeError("Metadata extraction self-test failed.")
    unsafe = markdown_safe_text("![[Secret.md]]\n# untrusted heading\n<img src=x>")
    if "![[Secret.md]]" in unsafe or "\n# untrusted heading" in unsafe or "<img" in unsafe:
        raise RuntimeError("Obsidian safety escaping self-test failed.")
    return {
        "status": "completed",
        "mode": "self_test",
        "policy": {
            "max_retries": MAX_RETRIES,
            "total_attempts": TOTAL_ATTEMPTS,
            "backoff_seconds": BACKOFF_SECONDS,
            "private_local_targets_blocked": True,
            "allowed_ports": [80, 443],
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "raw_html_preserved": True,
            "meaningful_content_dedupe": True,
            "javascript_execution": False,
            "browser_rendered_publish_api": callable(publish_rendered_capture),
            "browser_execution_owned_by_website_scraper": True,
        },
        "sample": {
            "normalized_url": normalized,
            "title": page["title"],
            "author": page["author"],
            "published_at": page["published_at"],
            "extracted_characters": len(page["text"]),
        },
    }

def main():
    parser = argparse.ArgumentParser(
        description="Second Brain canonical single-webpage archive/import layer. sb-web-import remains static-only; Website Scraper may supply an explicitly labeled browser-rendered DOM."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture")
    cap.add_argument("url")

    que = sub.add_parser("queue")
    que.add_argument("url")

    due = sub.add_parser("process-due")
    due.add_argument("--max-items", type=int, default=5)

    ret = sub.add_parser("retry")
    ret.add_argument("request_id")

    sub.add_parser("status")
    sub.add_parser("list")
    sub.add_parser("self-test")

    args = parser.parse_args()

    if os.geteuid() == 0 and args.command != "self-test":
        raise PermanentError("Run webpage-import commands as theadmin without sudo; the systemd service already runs with the correct owner.")

    if args.command == "capture":
        result = capture(args.url)
    elif args.command == "queue":
        result = queue_url(args.url)
    elif args.command == "process-due":
        result = process_due(max(1, min(int(args.max_items), 20)))
    elif args.command == "retry":
        result = retry_request(args.request_id)
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
        print(json.dumps({
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False))
        sys.exit(1)
