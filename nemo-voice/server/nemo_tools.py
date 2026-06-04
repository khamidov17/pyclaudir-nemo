"""
Nemo Voice Tools — Complete tool suite for the ADK voice agent.

Wraps existing Python scripts + direct Telegram Bot API + SQLite access + filesystem memory.
Designed to give voice Nemo the same capabilities as Telegram Nemo.
"""

import os
import json
import subprocess
import sqlite3
import struct
import hashlib
import hmac
import requests
import glob as glob_module
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── Configuration ──────────────────────────────────────────────

SCRIPTS_DIR = os.environ.get(
    "NEMO_SCRIPTS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts")
)
DATA_DIR = os.environ.get(
    "NEMO_DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "prod")
)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DEFAULT_CHAT_ID = int(os.environ.get("NEMO_DEFAULT_CHAT_ID", "1965085976"))

MEMORIES_DIR = os.path.join(DATA_DIR, "memories")
DATABASE_PATH = os.path.join(DATA_DIR, "database.db")
REMINDERS_DB_PATH = os.path.join(DATA_DIR, "reminders.db")
OAUTH_DB_PATH = os.environ.get(
    "NEMO_OAUTH_DB",
    os.path.join(DATA_DIR, "oauth_tokens.db")
)

# OAuth encryption
OAUTH_MASTER_SECRET = os.environ.get("OAUTH_MASTER_SECRET", "").encode()
GOOGLE_CLIENT_ID = os.environ.get("NEMO_GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("NEMO_GOOGLE_CLIENT_SECRET", "")


# ─── OAuth Token Decryption (mirrors Rust AES-256-GCM) ─────────

def _derive_key(master_secret: bytes, user_id: int) -> bytes:
    """Derive AES-256 key from master secret + user_id via HMAC-SHA256."""
    user_bytes = struct.pack("<q", user_id)  # i64 little-endian
    return hmac.new(master_secret, user_bytes, hashlib.sha256).digest()


def _decrypt_token(master_secret: bytes, user_id: int, data: bytes) -> str:
    """Decrypt AES-256-GCM: data = nonce (12) || ciphertext || tag (16)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(data) < 28:  # 12 nonce + 16 tag minimum
        raise ValueError("Ciphertext too short")
    key = _derive_key(master_secret, user_id)
    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def get_oauth_tokens(user_id: int, service: str) -> dict:
    """Get decrypted OAuth tokens for a user/service pair."""
    if not OAUTH_MASTER_SECRET:
        return {"error": "OAuth master secret not configured"}
    try:
        conn = sqlite3.connect(OAUTH_DB_PATH)
        row = conn.execute(
            "SELECT access_token_enc, refresh_token_enc FROM oauth_tokens WHERE user_id=? AND service=?",
            (user_id, service)
        ).fetchone()
        conn.close()
        if not row:
            return {"error": f"No {service} token found for user {user_id}. Ask them to connect via /connect in Telegram."}
        access_token = _decrypt_token(OAUTH_MASTER_SECRET, user_id, row[0])
        refresh_token = _decrypt_token(OAUTH_MASTER_SECRET, user_id, row[1]) if row[1] else None
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
        }
    except Exception as e:
        return {"error": f"Token decryption failed: {e}"}


# ─── Helpers ────────────────────────────────────────────────────

# Per-session user context — ContextVar is async-safe: each asyncio task
# gets its own copy, preventing cross-session identity leakage.
from contextvars import ContextVar
_session_user_id: ContextVar[int] = ContextVar('session_user_id', default=DEFAULT_CHAT_ID)


def set_session_user(user_id: int):
    """Set the current session's Telegram user_id for tool calls."""
    _session_user_id.set(user_id)


def _get_user_id() -> int:
    return _session_user_id.get()


def _call_script(script_name: str, operation: str, args: dict) -> dict:
    """Call a Python script with operation and JSON args."""
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    if not os.path.exists(script_path):
        return {"error": f"Script not found: {script_name}"}
    try:
        result = subprocess.run(
            ["python3", script_path, operation, json.dumps(args)],
            capture_output=True, text=True, timeout=60, cwd=SCRIPTS_DIR
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()[:500]}
        resp = json.loads(result.stdout.strip())
        if resp.get("success"):
            return resp.get("result", {"status": "ok"})
        return {"error": resp.get("error", "Unknown error")}
    except subprocess.TimeoutExpired:
        return {"error": "Script timed out"}
    except json.JSONDecodeError:
        return {"error": "Could not parse script output"}
    except Exception as e:
        return {"error": str(e)}


def _call_google_script(script_name: str, operation: str, args: dict, service: str) -> dict:
    """Call a Google API script with auto-injected OAuth tokens."""
    user_id = _get_user_id()
    tokens = get_oauth_tokens(user_id, service)
    if "error" in tokens:
        return tokens
    # Merge tokens into args
    args.update(tokens)
    return _call_script(script_name, operation, args)


def _telegram_api(method: str, **kwargs) -> dict:
    """Call Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        return {"error": "Telegram bot token not configured"}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, **kwargs, timeout=30)
        data = resp.json()
        if data.get("ok"):
            return {"status": "sent", "message_id": data["result"].get("message_id")}
        return {"error": data.get("description", "Telegram API error")}
    except Exception as e:
        return {"error": str(e)}


def _safe_memory_path(path: str) -> str:
    """Resolve a memory path safely, preventing traversal."""
    resolved = os.path.normpath(os.path.join(MEMORIES_DIR, path))
    if not resolved.startswith(os.path.normpath(MEMORIES_DIR)):
        return ""
    return resolved


# ═══════════════════════════════════════════════════════════════
# TIME
# ═══════════════════════════════════════════════════════════════

def get_current_time(utc_offset: int = 5) -> dict:
    """Get the current date and time. Default offset is UTC+5 (Tashkent). Returns formatted date, time, day of week."""
    now_utc = datetime.now(timezone.utc)
    local = now_utc + timedelta(hours=utc_offset)
    return {
        "utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "local": local.strftime("%Y-%m-%d %H:%M:%S") + f" UTC+{utc_offset}",
        "day": local.strftime("%A"),
        "date": local.strftime("%B %d, %Y"),
    }


# ═══════════════════════════════════════════════════════════════
# TELEGRAM MESSAGING
# ═══════════════════════════════════════════════════════════════

def send_telegram_message(chat_id: int, text: str) -> dict:
    """Send a text message to a Telegram chat. Use the user's chat_id for DMs."""
    return _telegram_api("sendMessage", json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML"
    })


_ALLOWED_SEND_PREFIXES = ("/tmp/nemo_voice_", "/tmp/nemo-")


def send_telegram_document(chat_id: int, file_path: str, caption: str = "") -> dict:
    """Send a generated file to a Telegram chat.
    Only files in allowed output directories can be sent (prevents data exfiltration)."""
    real_path = os.path.realpath(file_path)
    if not any(real_path.startswith(p) for p in _ALLOWED_SEND_PREFIXES):
        return {"error": "file_path must be a Nemo-generated output file in /tmp/nemo_voice_*"}
    if not os.path.exists(real_path):
        return {"error": f"File not found: {file_path}"}
    with open(real_path, "rb") as f:
        files = {"document": (os.path.basename(real_path), f)}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        return _telegram_api("sendDocument", data=data, files=files)


# ═══════════════════════════════════════════════════════════════
# MEMORY (Persistent Storage)
# ═══════════════════════════════════════════════════════════════

def create_memory(path: str, content: str) -> dict:
    """Create a new memory file. Path relative to memories/ e.g. 'users/123456.md'. Fails if exists."""
    full = _safe_memory_path(path)
    if not full:
        return {"error": "Invalid path"}
    if os.path.exists(full):
        return {"error": "File already exists. Use edit_memory to modify."}
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return {"status": "created", "path": path}


def read_memory(path: str) -> dict:
    """Read a memory file. Returns content with line numbers."""
    full = _safe_memory_path(path)
    if not full or not os.path.exists(full):
        return {"error": f"File not found: {path}"}
    with open(full, "r") as f:
        lines = f.readlines()
    numbered = "".join(f"{i+1}: {line}" for i, line in enumerate(lines))
    return {"content": numbered, "path": path, "lines": len(lines)}


def edit_memory(path: str, old_string: str, new_string: str) -> dict:
    """Edit a memory file by replacing an exact unique string."""
    full = _safe_memory_path(path)
    if not full or not os.path.exists(full):
        return {"error": f"File not found: {path}"}
    with open(full, "r") as f:
        content = f.read()
    count = content.count(old_string)
    if count == 0:
        return {"error": "old_string not found in file"}
    if count > 1:
        return {"error": f"old_string found {count} times — must be unique"}
    with open(full, "w") as f:
        f.write(content.replace(old_string, new_string, 1))
    return {"status": "edited", "path": path}


def list_memories(path: str = "") -> dict:
    """List files and folders in the memories directory."""
    full = _safe_memory_path(path) if path else MEMORIES_DIR
    if not full or not os.path.isdir(full):
        return {"error": f"Directory not found: {path}"}
    entries = []
    for entry in sorted(os.listdir(full)):
        if os.path.isdir(os.path.join(full, entry)):
            entries.append(f"{entry}/")
        else:
            entries.append(entry)
    return {"entries": entries, "count": len(entries)}


def search_memories(pattern: str, path: str = "") -> dict:
    """Search memory files for a text pattern. Returns matching lines."""
    search_dir = _safe_memory_path(path) if path else MEMORIES_DIR
    if not search_dir or not os.path.isdir(search_dir):
        return {"error": "Directory not found"}
    matches = []
    for filepath in glob_module.glob(os.path.join(search_dir, "**/*"), recursive=True):
        if os.path.isfile(filepath):
            try:
                with open(filepath, "r") as f:
                    for i, line in enumerate(f, 1):
                        if pattern.lower() in line.lower():
                            rel = os.path.relpath(filepath, MEMORIES_DIR)
                            matches.append(f"{rel}:{i}: {line.strip()}")
            except (UnicodeDecodeError, PermissionError):
                pass
    return {"matches": matches[:50], "total": len(matches)}


def delete_memory(path: str) -> dict:
    """Delete a memory file."""
    full = _safe_memory_path(path)
    if not full or not os.path.exists(full):
        return {"error": f"File not found: {path}"}
    if os.path.isdir(full):
        return {"error": "Cannot delete directories"}
    os.remove(full)
    return {"status": "deleted", "path": path}


# ═══════════════════════════════════════════════════════════════
# REMINDERS
# ═══════════════════════════════════════════════════════════════

def set_reminder(chat_id: int, message: str, trigger_at: str, repeat_cron: str = "") -> dict:
    """Set a reminder. trigger_at: relative ('+30m', '+2h', '+1d') or absolute ('2026-03-15 14:00'). Optional repeat_cron for recurring ('09:00' daily, '+1d' every day)."""
    now = datetime.now(timezone.utc)
    if trigger_at.startswith("+"):
        val, unit = trigger_at[1:-1], trigger_at[-1]
        try:
            n = int(val)
        except ValueError:
            return {"error": f"Invalid relative time: {trigger_at}"}
        deltas = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}
        if unit not in deltas:
            return {"error": f"Unknown unit: {unit}. Use m/h/d/w"}
        trigger_str = (now + deltas[unit]).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        trigger_str = trigger_at
    try:
        conn = sqlite3.connect(REMINDERS_DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO reminders (chat_id, user_id, message, trigger_at, repeat_cron, created_at, active) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (chat_id, _get_user_id(), message, trigger_str, repeat_cron or None, now.isoformat())
        )
        rid = c.lastrowid
        conn.commit()
        conn.close()
        return {"status": "set", "reminder_id": rid, "trigger_at": trigger_str}
    except Exception as e:
        return {"error": str(e)}


def list_reminders(chat_id: int = 0) -> dict:
    """List active reminders. Optionally filter by chat_id."""
    try:
        conn = sqlite3.connect(REMINDERS_DB_PATH)
        if chat_id:
            rows = conn.execute("SELECT id, chat_id, message, trigger_at, repeat_cron FROM reminders WHERE active=1 AND chat_id=?", (chat_id,)).fetchall()
        else:
            rows = conn.execute("SELECT id, chat_id, message, trigger_at, repeat_cron FROM reminders WHERE active=1 ORDER BY trigger_at LIMIT 20").fetchall()
        conn.close()
        return {"reminders": [{"id": r[0], "chat_id": r[1], "message": r[2], "trigger_at": r[3], "repeat": r[4]} for r in rows], "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


def cancel_reminder(reminder_id: int) -> dict:
    """Cancel a reminder by its ID."""
    try:
        conn = sqlite3.connect(REMINDERS_DB_PATH)
        c = conn.execute("UPDATE reminders SET active=0 WHERE id=?", (reminder_id,))
        if c.rowcount == 0:
            conn.close()
            return {"error": f"Reminder {reminder_id} not found"}
        conn.commit()
        conn.close()
        return {"status": "cancelled", "reminder_id": reminder_id}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# DATABASE QUERIES
# ═══════════════════════════════════════════════════════════════

def query_database(sql: str) -> dict:
    """Execute a SELECT query on the message/user database. Tables: messages (message_id, chat_id, user_id, username, timestamp, text), users (user_id, username, first_name, message_count, status)."""
    sql_clean = sql.strip()
    if not sql_clean.upper().startswith("SELECT"):
        return {"error": "Only SELECT queries allowed"}
    for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH"]:
        if kw in sql_clean.upper():
            return {"error": f"Forbidden: {kw}"}
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql_clean).fetchmany(100)
        if not rows:
            conn.close()
            return {"result": "No rows", "count": 0}
        result = [{col: str(row[col])[:200] for col in row.keys()} for row in rows]
        conn.close()
        return {"rows": result, "count": len(result)}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# DOCUMENTS
# ═══════════════════════════════════════════════════════════════

def create_pdf(filename: str, html_content: str, chat_id: int = 0) -> dict:
    """Create a PDF from HTML and send to user's Telegram DM. Use well-formatted HTML with inline CSS."""
    cid = chat_id or _get_user_id()
    tmp_html = f"/tmp/nemo_voice_pdf_{os.getpid()}.html"
    tmp_pdf = f"/tmp/{filename}"
    try:
        with open(tmp_html, "w") as f:
            f.write(html_content)
        subprocess.run(["wkhtmltopdf", "--quiet", tmp_html, tmp_pdf], capture_output=True, timeout=30)
        if not os.path.exists(tmp_pdf):
            return {"error": "PDF creation failed"}
        return send_telegram_document(cid, tmp_pdf, caption=f"📄 {filename}")
    except Exception as e:
        return {"error": str(e)}
    finally:
        for f in [tmp_html, tmp_pdf]:
            try:
                os.remove(f)
            except OSError:
                pass


def create_word_document(filename: str, markdown_content: str, chat_id: int = 0) -> dict:
    """Create a Word doc from Markdown and send to user's Telegram DM."""
    cid = chat_id or _get_user_id()
    tmp_md = f"/tmp/nemo_voice_word_{os.getpid()}.md"
    tmp_docx = f"/tmp/{filename}"
    try:
        with open(tmp_md, "w") as f:
            f.write(markdown_content)
        subprocess.run(["pandoc", tmp_md, "-o", tmp_docx], capture_output=True, timeout=30)
        if not os.path.exists(tmp_docx):
            return {"error": "Word creation failed"}
        return send_telegram_document(cid, tmp_docx, caption=f"📝 {filename}")
    except Exception as e:
        return {"error": str(e)}
    finally:
        for f in [tmp_md, tmp_docx]:
            try:
                os.remove(f)
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════════
# WEB / FETCH
# ═══════════════════════════════════════════════════════════════

def fetch_url(url: str) -> dict:
    """Fetch and read text content of a public web page or PDF URL.
    Private/internal addresses are blocked (SSRF protection)."""
    try:
        import re, ipaddress, urllib.parse, socket
        # SSRF guard: reject private/internal hosts
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        # Resolve hostname and check all IPs
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                ip = ipaddress.ip_address(info[4][0])
                if (ip.is_private or ip.is_loopback or ip.is_link_local or
                        ip.is_reserved or ip.is_unspecified or
                        ip.is_multicast or str(ip).startswith("100.64.")):
                    return {"error": f"Blocked: {host} resolves to private/internal address"}
        except socket.gaierror:
            return {"error": f"DNS resolution failed for {host}"}
        resp = requests.get(url, timeout=15, headers={"User-Agent": "NemoBot/1.0"})
        text = re.sub(r'<script[^>]*>.*?</script>', '', resp.text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return {"content": text[:8000], "url": url}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# GMAIL (with auto OAuth token injection)
# ═══════════════════════════════════════════════════════════════

def gmail_read_inbox(max_results: int = 5) -> dict:
    """Read recent emails from Gmail inbox."""
    return _call_google_script("gmail_tools.py", "read", {"max_results": max_results}, "gmail")


def gmail_search(query: str, max_results: int = 5) -> dict:
    """Search Gmail. Use syntax: 'from:user@example.com', 'subject:meeting', 'is:unread'."""
    return _call_google_script("gmail_tools.py", "search", {"query": query, "max_results": max_results}, "gmail")


def gmail_send(to: str, subject: str, body: str) -> dict:
    """Send an email via Gmail."""
    return _call_google_script("gmail_tools.py", "send", {"to": to, "subject": subject, "body": body}, "gmail")


def gmail_get_email(email_id: str) -> dict:
    """Get the full content of a specific email by its ID."""
    return _call_google_script("gmail_tools.py", "get", {"email_id": email_id}, "gmail")


def gmail_reply(email_id: str, body: str) -> dict:
    """Reply to a specific email."""
    return _call_google_script("gmail_tools.py", "reply", {"email_id": email_id, "body": body}, "gmail")


def gmail_forward(email_id: str, to: str) -> dict:
    """Forward an email to another address."""
    return _call_google_script("gmail_tools.py", "forward", {"email_id": email_id, "to": to}, "gmail")


# ═══════════════════════════════════════════════════════════════
# GOOGLE CALENDAR (with auto OAuth)
# ═══════════════════════════════════════════════════════════════

def calendar_get_events(max_results: int = 5, time_min: str = "", time_max: str = "") -> dict:
    """Get upcoming Google Calendar events."""
    args = {"max_results": max_results}
    if time_min:
        args["time_min"] = time_min
    if time_max:
        args["time_max"] = time_max
    return _call_google_script("calendar_tools.py", "get_events", args, "google_calendar")


def calendar_create_event(summary: str, start_time: str, end_time: str, description: str = "", location: str = "") -> dict:
    """Create a Google Calendar event. Times in ISO8601."""
    args = {"summary": summary, "start_time": start_time, "end_time": end_time}
    if description:
        args["description"] = description
    if location:
        args["location"] = location
    return _call_google_script("calendar_tools.py", "create_event", args, "google_calendar")


def calendar_update_event(event_id: str, summary: str = "", start_time: str = "", end_time: str = "") -> dict:
    """Update a Google Calendar event."""
    args = {"event_id": event_id}
    if summary:
        args["summary"] = summary
    if start_time:
        args["start_time"] = start_time
    if end_time:
        args["end_time"] = end_time
    return _call_google_script("calendar_tools.py", "update_event", args, "google_calendar")


def calendar_delete_event(event_id: str) -> dict:
    """Delete a Google Calendar event."""
    return _call_google_script("calendar_tools.py", "delete_event", {"event_id": event_id}, "google_calendar")


# ═══════════════════════════════════════════════════════════════
# GOOGLE DRIVE (with auto OAuth)
# ═══════════════════════════════════════════════════════════════

def drive_search(query: str, max_results: int = 5) -> dict:
    """Search Google Drive for files and folders."""
    return _call_google_script("drive_tools.py", "search", {"query": query, "max_results": max_results}, "google_drive")


def drive_list_folder(folder_id: str = "root", max_results: int = 10) -> dict:
    """List files in a Google Drive folder."""
    return _call_google_script("drive_tools.py", "list_folder", {"folder_id": folder_id, "max_results": max_results}, "google_drive")


def drive_create_file(name: str, content: str, folder_id: str = "root") -> dict:
    """Create a new file in Google Drive."""
    return _call_google_script("drive_tools.py", "create_file", {"name": name, "content": content, "folder_id": folder_id}, "google_drive")


def drive_create_folder(name: str, parent_id: str = "root") -> dict:
    """Create a new folder in Google Drive."""
    return _call_google_script("drive_tools.py", "create_folder", {"name": name, "parent_id": parent_id}, "google_drive")


def drive_share(file_id: str, email: str, role: str = "reader") -> dict:
    """Share a Google Drive file. Roles: reader, writer, commenter."""
    return _call_google_script("drive_tools.py", "share", {"file_id": file_id, "email": email, "role": role}, "google_drive")


# ═══════════════════════════════════════════════════════════════
# SLACK / NOTION / OUTLOOK / CANVAS
# ═══════════════════════════════════════════════════════════════

def slack_read_messages(channel: str, limit: int = 10) -> dict:
    """Read recent messages from a Slack channel."""
    return _call_script("slack_tools.py", "read", {"channel": channel, "limit": limit})


def slack_send_message(channel: str, text: str) -> dict:
    """Send a message to a Slack channel."""
    return _call_script("slack_tools.py", "send", {"channel": channel, "text": text})


def notion_search(query: str) -> dict:
    """Search Notion pages and databases."""
    return _call_script("notion_tools.py", "search", {"query": query})


def outlook_read_mail(max_results: int = 5) -> dict:
    """Read recent Outlook emails."""
    return _call_script("outlook_tools.py", "read_mail", {"max_results": max_results})


def outlook_send_mail(to: str, subject: str, body: str) -> dict:
    """Send an email via Outlook."""
    return _call_script("outlook_tools.py", "send_mail", {"to": to, "subject": subject, "body": body})


def canvas_get_courses() -> dict:
    """Get courses from Canvas LMS."""
    return _call_script("canvas_tools.py", "courses", {})


def canvas_get_assignments(course_id: str) -> dict:
    """Get assignments for a Canvas course."""
    return _call_script("canvas_tools.py", "assignments", {"course_id": course_id})


# ═══════════════════════════════════════════════════════════════
# VOICE SESSION MEMORY
# ═══════════════════════════════════════════════════════════════

def save_voice_session_note(user_name: str, summary: str) -> dict:
    """Save a summary of this voice conversation to memory for future reference."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    note = f"\n## Voice session — {timestamp}\n**User:** {user_name}\n{summary}\n"
    path = os.path.join(MEMORIES_DIR, "notes", "voice_sessions.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(note)
    return {"status": "saved", "timestamp": timestamp}


# ═══════════════════════════════════════════════════════════════
# TOOL REGISTRIES
# ═══════════════════════════════════════════════════════════════

# Public tools — anyone can use (anonymous visitors)
PUBLIC_TOOLS = [
    get_current_time,
    fetch_url,
]

# All tools — authenticated Telegram users get everything
NEMO_TOOLS = [
    # Telegram
    send_telegram_message,
    send_telegram_document,
    # Memory
    create_memory, read_memory, edit_memory, list_memories, search_memories, delete_memory,
    # Reminders
    set_reminder, list_reminders, cancel_reminder,
    # Database
    query_database,
    # Documents
    create_pdf, create_word_document,
    # Gmail
    gmail_read_inbox, gmail_search, gmail_send, gmail_get_email, gmail_reply, gmail_forward,
    # Calendar
    calendar_get_events, calendar_create_event, calendar_update_event, calendar_delete_event,
    # Drive
    drive_search, drive_list_folder, drive_create_file, drive_create_folder, drive_share,
    # Slack
    slack_read_messages, slack_send_message,
    # Notion
    notion_search,
    # Outlook
    outlook_read_mail, outlook_send_mail,
    # Canvas
    canvas_get_courses, canvas_get_assignments,
    # Voice session
    save_voice_session_note,
]
