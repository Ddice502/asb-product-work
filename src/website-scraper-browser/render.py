#!/usr/bin/env python3
from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

MAX_DOM_BYTES = 20 * 1024 * 1024
OUTPUT = Path("/output/second-brain-render.html")
USER_AGENT = "SecondBrainWebsiteScraper/1.0 (+private personal archive)"
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}

class RetryableError(RuntimeError):
    pass

class PermanentError(RuntimeError):
    pass

def normalize_primary(url: str) -> str:
    p = urllib.parse.urlsplit(str(url).strip())
    scheme = p.scheme.lower()
    if scheme not in {"http", "https"}:
        raise PermanentError("Only http/https URLs are allowed.")
    if p.username or p.password:
        raise PermanentError("User-info in URLs is not allowed.")
    host = p.hostname
    if not host:
        raise PermanentError("URL hostname is missing.")
    try:
        port = p.port
    except ValueError as exc:
        raise PermanentError(f"Invalid URL port: {exc}")
    if port is not None and port not in {80, 443}:
        raise PermanentError("Only ports 80 and 443 are allowed.")
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise PermanentError("Private/local/non-public IP targets are blocked.")
    except ValueError:
        pass
    netloc = host.lower()
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc += f":{port}"
    path = p.path or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, p.query, ""))

def validate_public_resolution(url: str) -> None:
    p = urllib.parse.urlsplit(url)
    host = p.hostname
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RetryableError(f"DNS resolution failed: {exc}")
    if not infos:
        raise RetryableError("DNS resolution returned no addresses.")
    bad = []
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            bad.append(addr)
            continue
        if not ip.is_global:
            bad.append(addr)
    if bad:
        raise PermanentError("Target resolved to a private/local/non-public address.")

def route_request(route):
    req = route.request
    u = urllib.parse.urlsplit(req.url)
    if u.scheme in {"data", "blob", "about"}:
        route.continue_()
        return
    if u.scheme not in {"http", "https"}:
        route.abort()
        return
    if req.resource_type in {"media", "font"}:
        route.abort()
        return
    route.continue_()

def main() -> int:
    raw_url = os.environ.get("SB_RENDER_URL", "")
    url = normalize_primary(raw_url)
    validate_public_resolution(url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=False,
            service_workers="block",
            java_script_enabled=True,
            ignore_https_errors=False,
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 1200},
        )
        context.route("**/*", route_request)
        context.route_web_socket("**/*", lambda ws: None)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(45000)

        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except PlaywrightTimeoutError as exc:
            raise RetryableError(f"Browser navigation timed out: {exc}")

        if response is None:
            raise RetryableError("Browser navigation returned no HTTP response.")
        status = int(response.status)
        if status in RETRYABLE_HTTP:
            raise RetryableError(f"Browser navigation returned retryable HTTP {status}.")
        if status in {401, 403}:
            raise PermanentError(f"Browser navigation returned HTTP {status}; access-control bypass is disabled.")
        if status >= 400:
            raise PermanentError(f"Browser navigation returned HTTP {status}.")

        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeoutError:
            pass

        # Trigger common lazy-loaded article text while media/font requests remain blocked.
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(750)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(250)
        except Exception:
            pass

        final_url = normalize_primary(page.url)
        if urllib.parse.urlsplit(url).scheme == "https" and urllib.parse.urlsplit(final_url).scheme != "https":
            raise PermanentError("HTTPS-to-HTTP redirect downgrade is not allowed.")
        validate_public_resolution(final_url)

        html = page.content()
        payload = html.encode("utf-8")
        if len(payload) > MAX_DOM_BYTES:
            raise PermanentError(f"Rendered DOM exceeded the {MAX_DOM_BYTES} byte safety limit.")
        OUTPUT.write_bytes(payload)
        context.close()
        browser.close()

    print(json.dumps({
        "status": "completed",
        "requested_url": url,
        "final_url": final_url,
        "http_status": status,
        "output_path": str(OUTPUT),
        "size_bytes": len(payload),
        "renderer": "playwright-chromium-1.61.0",
        "javascript_rendered": True,
    }))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        retryable = isinstance(exc, RetryableError)
        print(json.dumps({
            "status": "failed",
            "retryable": retryable,
            "error": f"{type(exc).__name__}: {exc}",
        }))
        raise SystemExit(75 if retryable else 64)
