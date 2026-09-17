#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import secrets
import subprocess
import threading
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from flask import (
    Flask,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from markupsafe import Markup


AUTH_PATH = Path(
    "/AI/Config/Second Brain/"
    "Ask My Brain/web-auth.json"
)

SB_ASK = "/usr/local/bin/sb-ask"
OBSIDIAN_VAULT_NAME = "Second Brain"

MAX_QUESTION_LENGTH = 1200
ASK_TIMEOUT = 210
SEARCH_TIMEOUT = 45

ASK_LOCK = threading.Lock()


def load_auth():
    return json.loads(
        AUTH_PATH.read_text(
            encoding="utf-8"
        )
    )


AUTH = load_auth()

app = Flask(__name__)

app.secret_key = bytes.fromhex(
    AUTH["session_secret_hex"]
)

app.config.update(
    SESSION_COOKIE_NAME="ask_my_brain_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=32 * 1024,
)


@app.after_request
def security_headers(response):

    response.headers["Cache-Control"] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0"
    )

    response.headers["Pragma"] = "no-cache"

    response.headers[
        "X-Content-Type-Options"
    ] = "nosniff"

    response.headers[
        "X-Frame-Options"
    ] = "DENY"

    response.headers[
        "Referrer-Policy"
    ] = "no-referrer"

    response.headers[
        "Permissions-Policy"
    ] = (
        "camera=(), microphone=(), "
        "geolocation=()"
    )

    response.headers[
        "Content-Security-Policy"
    ] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'"
    )

    return response


def authenticated():
    return bool(
        session.get("authenticated")
    )


def verify_password(supplied):

    salt = bytes.fromhex(
        AUTH["salt_hex"]
    )

    expected = bytes.fromhex(
        AUTH["password_hash_hex"]
    )

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        supplied.encode("utf-8"),
        salt,
        int(AUTH["iterations"]),
    )

    return hmac.compare_digest(
        expected,
        actual,
    )


def csrf_token():

    token = session.get("csrf")

    if not token:
        token = secrets.token_urlsafe(24)
        session["csrf"] = token

    return token


def csrf_valid():

    expected = session.get(
        "csrf",
        "",
    )

    supplied = request.form.get(
        "csrf",
        "",
    )

    return bool(
        expected
        and supplied
        and hmac.compare_digest(
            expected,
            supplied,
        )
    )


def run_sb(arguments, timeout):

    result = subprocess.run(
        [
            SB_ASK,
            *arguments,
        ],
        shell=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )

    if result.returncode != 0:

        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Unknown Ask My Brain error"
        )

        raise RuntimeError(
            message[:2000]
        )

    return result.stdout.strip()


def status_info():

    try:
        raw = run_sb(
            ["status"],
            20,
        )
    except Exception:
        return {
            "INDEX_STATUS": "ERROR"
        }

    values = {}

    for line in raw.splitlines():

        if "=" not in line:
            continue

        key, value = line.split(
            "=",
            1,
        )

        values[key.strip()] = (
            value.strip()
        )

    return values


def parse_sources(raw):

    sources = []

    if "\nSOURCES:\n" not in raw:
        return sources

    source_section = raw.split(
        "\nSOURCES:\n",
        1,
    )[1]

    for line in source_section.splitlines():

        match = re.match(
            r"^\[(S\d+)\]\s+"
            r"(.+?)\s+"
            r"\(lines\s+"
            r"(\d+)-(\d+)\)$",
            line.strip(),
        )

        if not match:
            continue

        label = match.group(1)
        path = match.group(2)

        start = int(
            match.group(3)
        )

        end = int(
            match.group(4)
        )

        obsidian_uri = (
            "obsidian://open?"
            "vault="
            + quote(
                OBSIDIAN_VAULT_NAME,
                safe="",
            )
            + "&file="
            + quote(
                path,
                safe="",
            )
        )

        provenance = {}

        provenance_match = None

        current_index = (
            source_section.splitlines()
            .index(line)
        )

        lines = source_section.splitlines()

        if current_index + 1 < len(lines):
            next_line = lines[
                current_index + 1
            ].strip()

            provenance_match = re.match(
                r"^Provenance:\s+(\{.*\})$",
                next_line,
            )

        if provenance_match:
            try:
                import json

                provenance = json.loads(
                    provenance_match.group(1)
                )

            except Exception:
                provenance = {}

        sources.append(
            {
                "label": label,
                "path": path,
                "start": start,
                "end": end,
                "uri": obsidian_uri,
                "evidence": "",
                "provenance": provenance,
            }
        )

    return sources


def parse_search_evidence(raw):

    evidence = {}

    blocks = re.split(
        r"\n(?=\[S\d+\]\s)",
        raw,
    )

    for block in blocks:

        first = re.search(
            r"^\[(S\d+)\]\s+(.+)$",
            block,
            re.M,
        )

        lines = re.search(
            r"^\s*Lines:\s+"
            r"(\d+)-(\d+)$",
            block,
            re.M,
        )

        snippet = re.search(
            r"^\s*Evidence:\s*(.*)$",
            block,
            re.M,
        )

        if not (
            first
            and lines
            and snippet
        ):
            continue

        path = first.group(2).strip()

        start = int(
            lines.group(1)
        )

        end = int(
            lines.group(2)
        )

        evidence[
            (
                path,
                start,
                end,
            )
        ] = snippet.group(1).strip()

    return evidence


def parse_answer(raw):

    if not raw.startswith("ANSWER:"):
        raise RuntimeError(
            "Unexpected Ask My Brain output"
        )

    content = raw[
        len("ANSWER:"):
    ].lstrip()

    if "\nSOURCES:\n" in content:

        answer_text = content.split(
            "\nSOURCES:\n",
            1,
        )[0].strip()

    else:

        positions = []

        for marker in [
            "\nEVIDENCE_STATUS=",
            "\nMODEL=",
        ]:

            position = content.find(
                marker
            )

            if position >= 0:
                positions.append(
                    position
                )

        if positions:
            answer_text = content[
                :min(positions)
            ].strip()
        else:
            answer_text = (
                content.strip()
            )

    strength_match = re.search(
        r"(?im)^Evidence strength:\s*"
        r"(HIGH|MEDIUM|LOW)\s*$",
        answer_text,
    )

    strength = None

    if strength_match:

        strength = (
            strength_match.group(1)
        )

        answer_text = (
            answer_text[
                :strength_match.start()
            ]
            + answer_text[
                strength_match.end():
            ]
        ).strip()

    evidence_status = None

    match = re.search(
        r"(?m)^EVIDENCE_STATUS=(.+)$",
        raw,
    )

    if match:
        evidence_status = (
            match.group(1).strip()
        )

    model = None

    match = re.search(
        r"(?m)^MODEL=(.+)$",
        raw,
    )

    if match:
        model = (
            match.group(1).strip()
        )

    return {
        "text": answer_text,
        "strength": strength,
        "evidence_status":
            evidence_status,
        "model": model,
        "sources":
            parse_sources(raw),
    }


def safe_answer_html(text):

    escaped = html.escape(text)

    escaped = re.sub(
        r"\[(S\d+)\]",
        (
            r'<a class="citation" '
            r'href="#source-\1">'
            r'[\1]</a>'
        ),
        escaped,
    )

    return Markup(escaped)


PAGE = r"""
<!doctype html>
<html lang="en">
<head>

<meta charset="utf-8">

<meta
  name="viewport"
  content="width=device-width, initial-scale=1"
>

<title>Ask My Brain</title>

<style>

:root {
  color-scheme: dark;
  --bg: #0c1016;
  --surface: #131922;
  --surface2: #19212d;
  --border: #293444;
  --text: #edf2f7;
  --muted: #9aa9ba;
  --accent: #8ab4ff;
  --good: #78dba9;
  --medium: #f2cc60;
  --low: #ff9b91;
  --danger: #ff8585;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family:
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
}

main {
  width: min(880px, calc(100% - 28px));
  margin: 0 auto;
  padding: 28px 0 70px;
}

h1 {
  margin: 0 0 5px;
  font-size: clamp(28px, 6vw, 42px);
  letter-spacing: -0.04em;
}

h2 {
  margin-top: 0;
}

.subtitle {
  color: var(--muted);
  font-size: 14px;
}

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 20px;
  margin: 14px 0;
}

textarea,
input[type=password] {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 13px;
  background: var(--surface2);
  color: var(--text);
  padding: 15px;
  font: inherit;
  outline: none;
}

textarea {
  min-height: 130px;
  resize: vertical;
}

textarea:focus,
input:focus {
  border-color: var(--accent);
}

button {
  margin-top: 12px;
  border: 0;
  border-radius: 12px;
  background: var(--accent);
  color: #08111e;
  padding: 12px 18px;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

button:disabled {
  opacity: 0.65;
  cursor: wait;
}

.answer {
  white-space: pre-wrap;
  line-height: 1.65;
  overflow-wrap: anywhere;
}

.citation {
  color: var(--accent);
  font-weight: 700;
  text-decoration: none;
}

.badge {
  display: inline-block;
  margin-top: 15px;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}

.high {
  color: var(--good);
  border: 1px solid var(--good);
}

.medium {
  color: var(--medium);
  border: 1px solid var(--medium);
}

.low {
  color: var(--low);
  border: 1px solid var(--low);
}

.source {
  border-top: 1px solid var(--border);
  padding: 14px 0;
}

.source:first-child {
  border-top: 0;
}

.source a {
  color: var(--accent);
  text-decoration: none;
}

.source-path {
  margin-top: 5px;
  color: var(--muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}

details {
  margin-top: 8px;
}

summary {
  color: var(--muted);
  cursor: pointer;
}

.evidence {
  padding-top: 8px;
  color: #d3dbe5;
  line-height: 1.5;
  font-size: 14px;
  white-space: pre-wrap;
}

.system {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  color: var(--muted);
  font-size: 12px;
  margin-top: 18px;
}

.error {
  color: var(--danger);
  white-space: pre-wrap;
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.signout {
  background: transparent;
  color: var(--muted);
  margin: 0;
  padding: 6px;
  font-size: 13px;
}

.login {
  max-width: 440px;
  margin: 12vh auto 0;
}

@media (max-width: 600px) {

  main {
    width: min(100% - 20px, 880px);
    padding-top: 18px;
  }

  .card {
    padding: 16px;
    border-radius: 14px;
  }
}

</style>
</head>

<body>
<main>

{% if login %}

<div class="login">

  <h1>Ask My Brain</h1>

  <div class="subtitle">
    Private Second Brain retrieval
  </div>

  <section class="card">

    {% if error %}
      <p class="error">
        {{ error }}
      </p>
    {% endif %}

    <form method="post" action="/login">

      <label for="password">
        Password
      </label>

      <input
        id="password"
        name="password"
        type="password"
        autocomplete="current-password"
        required
        autofocus
      >

      <button type="submit">
        Sign in
      </button>

    </form>

  </section>

</div>

{% else %}

<div class="header-row">

  <div>

    <h1>
      Ask My Brain
    </h1>

    <div class="subtitle">
      Answers grounded in your Second Brain
    </div>

  </div>

  <form
    method="post"
    action="/logout"
  >

    <input
      type="hidden"
      name="csrf"
      value="{{ csrf }}"
    >

    <button
      class="signout"
      type="submit"
    >
      Sign out
    </button>

  </form>

</div>

<section class="card">

  <form
    id="ask-form"
    method="post"
    action="/ask"
  >

    <input
      type="hidden"
      name="csrf"
      value="{{ csrf }}"
    >

    <textarea
      name="question"
      maxlength="1200"
      placeholder="What do you want to know?"
      required
      autofocus
    >{{ question or "" }}</textarea>

    <button
      id="ask-button"
      type="submit"
    >
      Ask My Brain
    </button>

  </form>

</section>

{% if error %}

<section class="card">

  <div class="error">
    {{ error }}
  </div>

</section>

{% endif %}

{% if answer %}

<section class="card">

  <h2>
    Answer
  </h2>

  <div class="answer">
    {{ answer.html }}
  </div>

  {% if answer.strength %}

    <div class="badge {{ answer.strength|lower }}">
      Evidence {{ answer.strength }}
    </div>

  {% elif answer.evidence_status == "NONE" %}

    <div class="badge low">
      No matching evidence
    </div>

  {% endif %}

</section>

{% if answer.sources %}

<section class="card">

  <h2>
    Sources
  </h2>

  {% for source in answer.sources %}

    <div
      class="source"
      id="source-{{ source.label }}"
    >

      <strong>
        {{ source.label }}
      </strong>

      &nbsp;

      <a href="{{ source.uri }}">
        Open in Obsidian
      </a>

      <div class="source-path">

        {{ source.path }}

        · lines

        {{ source.start }}–{{ source.end }}

      </div>

      {% if source.provenance %}
        <details class="provenance">
          <summary>
            Source provenance
          </summary>

          <dl>
          {% for key, value in source.provenance.items() %}
            <dt>{{ key }}</dt>
            <dd>{{ value }}</dd>
          {% endfor %}
          </dl>
        </details>
      {% endif %}

      {% if source.evidence %}

      <details>

        <summary>
          Show retrieved evidence
        </summary>

        <div class="evidence">
          {{ source.evidence }}
        </div>

      </details>

      {% endif %}

    </div>

  {% endfor %}

</section>

{% endif %}
{% endif %}

<div class="system">

  <span>
    Index:
    {{ status.get("INDEX_STATUS", "UNKNOWN") }}
  </span>

  <span>
    Notes:
    {{ status.get("NOTES_INDEXED", "?") }}
  </span>

  <span>
    Chunks:
    {{ status.get("CHUNKS_INDEXED", "?") }}
  </span>

  <span>
    Model:
    {% if answer and answer.model %}
      {{ answer.model }}
    {% else %}
      not invoked
    {% endif %}
  </span>

</div>

<script>

const form =
  document.getElementById("ask-form");

const button =
  document.getElementById("ask-button");

if (form && button) {

  form.addEventListener(
    "submit",
    () => {

      button.disabled = true;

      button.textContent =
        "Thinking…";
    }
  );
}

</script>

{% endif %}

</main>
</body>
</html>
"""


def render_main(
    question="",
    answer=None,
    error=None,
):

    if answer:

        answer["html"] = (
            safe_answer_html(
                answer["text"]
            )
        )

    return render_template_string(
        PAGE,
        login=False,
        question=question,
        answer=answer,
        error=error,
        csrf=csrf_token(),
        status=status_info(),
    )


@app.get("/healthz")
def healthz():

    status = status_info()

    healthy = (
        status.get(
            "INDEX_STATUS"
        ) == "READY"
        and status.get(
            "INTEGRITY"
        ) == "ok"
    )

    return {
        "service":
            "ask-my-brain",
        "healthy":
            healthy,
        "index_status":
            status.get(
                "INDEX_STATUS",
                "UNKNOWN",
            ),
    }, 200 if healthy else 503


@app.route(
    "/login",
    methods=["GET", "POST"],
)
def login():

    if authenticated():
        return redirect(
            url_for("home")
        )

    error = None

    if request.method == "POST":

        password = request.form.get(
            "password",
            "",
        )

        if verify_password(password):

            session.clear()

            session.permanent = True

            session[
                "authenticated"
            ] = True

            session["csrf"] = (
                secrets.token_urlsafe(
                    24
                )
            )

            return redirect(
                url_for("home")
            )

        error = "Incorrect password."

    return render_template_string(
        PAGE,
        login=True,
        error=error,
    )


@app.post("/logout")
def logout():

    if (
        authenticated()
        and csrf_valid()
    ):
        session.clear()

    return redirect(
        url_for("login")
    )


@app.get("/")
def home():

    if not authenticated():

        return redirect(
            url_for("login")
        )

    return render_main()


@app.post("/ask")
def ask_question():

    if not authenticated():

        return redirect(
            url_for("login")
        )

    if not csrf_valid():

        return (
            render_main(
                error=(
                    "Security token expired. "
                    "Refresh the page and "
                    "try again."
                )
            ),
            403,
        )

    question = request.form.get(
        "question",
        "",
    ).strip()

    if not question:

        return (
            render_main(
                error="Enter a question."
            ),
            400,
        )

    if (
        len(question)
        > MAX_QUESTION_LENGTH
    ):

        return (
            render_main(
                question=question[
                    :MAX_QUESTION_LENGTH
                ],
                error=(
                    "Question is too long."
                ),
            ),
            400,
        )

    try:

        with ASK_LOCK:

            raw_answer = run_sb(
                [
                    "ask",
                    question,
                    "--top",
                    "4",
                ],
                ASK_TIMEOUT,
            )

            answer = parse_answer(
                raw_answer
            )

            if answer["sources"]:

                raw_search = run_sb(
                    [
                        "search",
                        question,
                        "--top",
                        "8",
                    ],
                    SEARCH_TIMEOUT,
                )

                evidence = (
                    parse_search_evidence(
                        raw_search
                    )
                )

                for source in answer[
                    "sources"
                ]:

                    key = (
                        source["path"],
                        source["start"],
                        source["end"],
                    )

                    source[
                        "evidence"
                    ] = evidence.get(
                        key,
                        "",
                    )

    except subprocess.TimeoutExpired:

        return (
            render_main(
                question=question,
                error=(
                    "Ask My Brain timed out "
                    "while waiting for the "
                    "local AI model."
                ),
            ),
            504,
        )

    except Exception as exc:

        return (
            render_main(
                question=question,
                error=(
                    "Ask My Brain could not "
                    "complete this request.\n\n"
                    + str(exc)[:1200]
                ),
            ),
            500,
        )

    return render_main(
        question=question,
        answer=answer,
    )


if __name__ == "__main__":

    raise SystemExit(
        "Run through Waitress."
    )
