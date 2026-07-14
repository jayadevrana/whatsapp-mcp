"""Data access for the WhatsApp MCP (read + guarded single-recipient send).

Reads come from the local SQLite DB written by the Go bridge. Sends go through
the bridge's rate-limited, one-recipient-only /api/send endpoint — there is no
bulk path.
"""
import os
import re
import json
import shutil
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, List

import requests

# Bridge writes to  <repo>/bridge/store/messages.db ; this file lives in <repo>/server/
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MESSAGES_DB_PATH = os.path.join(BASE_DIR, "..", "bridge", "store", "messages.db")
WHATSAPP_API_BASE_URL = "http://127.0.0.1:8080/api"
CONTEXT_DIR = os.path.join(BASE_DIR, "..", "context")

URL_RE = re.compile(r"https?://[^\s<>\"]+")


@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None


@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None

    @property
    def is_group(self) -> bool:
        return self.jid.endswith("@g.us")


@dataclass
class Contact:
    phone_number: str
    name: Optional[str]
    jid: str


def _connect() -> sqlite3.Connection:
    if not os.path.exists(MESSAGES_DB_PATH):
        raise FileNotFoundError(
            f"Message DB not found at {MESSAGES_DB_PATH}. "
            "Start the Go bridge first (run-bridge.sh) and link your phone."
        )
    return sqlite3.connect(MESSAGES_DB_PATH)


# ---------------------------------------------------------------- helpers

def db_ready() -> bool:
    """True once the bridge has created its message DB."""
    return os.path.exists(MESSAGES_DB_PATH)


def get_sender_name(sender_jid: str) -> str:
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT name FROM chats WHERE jid = ? LIMIT 1", (sender_jid,))
        row = cur.fetchone()
        if not row:
            phone = sender_jid.split("@")[0] if "@" in sender_jid else sender_jid
            cur.execute("SELECT name FROM chats WHERE jid LIKE ? LIMIT 1", (f"%{phone}%",))
            row = cur.fetchone()
        return row[0] if row and row[0] else sender_jid
    except (sqlite3.Error, FileNotFoundError):
        return sender_jid
    finally:
        try:
            conn.close()
        except Exception:
            pass


def format_message(m: Message, show_chat_info: bool = True) -> str:
    out = ""
    if show_chat_info and m.chat_name:
        out += f"[{m.timestamp:%Y-%m-%d %H:%M:%S}] Chat: {m.chat_name} "
    else:
        out += f"[{m.timestamp:%Y-%m-%d %H:%M:%S}] "
    prefix = ""
    if m.media_type:
        prefix = f"[{m.media_type} - Message ID: {m.id} - Chat JID: {m.chat_jid}] "
    sender = "Me" if m.is_from_me else get_sender_name(m.sender)
    out += f"From: {sender}: {prefix}{m.content}\n"
    return out


def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> str:
    if not messages:
        return "No messages to display."
    return "".join(format_message(m, show_chat_info) for m in messages)


# ---------------------------------------------------------------- reads

def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
) -> str:
    """List messages matching filters (newest first), formatted for reading."""
    try:
        conn = _connect()
        cur = conn.cursor()
        parts = [
            "SELECT messages.timestamp, messages.sender, chats.name, messages.content, "
            "messages.is_from_me, chats.jid, messages.id, messages.media_type FROM messages",
            "JOIN chats ON messages.chat_jid = chats.jid",
        ]
        where, params = [], []
        if after:
            where.append("messages.timestamp > ?"); params.append(datetime.fromisoformat(after))
        if before:
            where.append("messages.timestamp < ?"); params.append(datetime.fromisoformat(before))
        if sender_phone_number:
            where.append("messages.sender LIKE ?"); params.append(f"%{sender_phone_number}%")
        if chat_jid:
            where.append("messages.chat_jid = ?"); params.append(chat_jid)
        if query:
            where.append("LOWER(messages.content) LIKE LOWER(?)"); params.append(f"%{query}%")
        if where:
            parts.append("WHERE " + " AND ".join(where))
        parts.append("ORDER BY messages.timestamp DESC LIMIT ? OFFSET ?")
        params.extend([limit, page * limit])

        cur.execute(" ".join(parts), tuple(params))
        rows = cur.fetchall()
        result = [
            Message(
                timestamp=datetime.fromisoformat(r[0]), sender=r[1], chat_name=r[2],
                content=r[3], is_from_me=bool(r[4]), chat_jid=r[5], id=r[6], media_type=r[7],
            ) for r in rows
        ]
        return format_messages_list(result)
    except (sqlite3.Error, FileNotFoundError) as e:
        return f"Database error: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_chats(query: Optional[str] = None, limit: int = 20, page: int = 0,
               sort_by: str = "last_active") -> List[dict]:
    """List chats (dicts), newest-active first by default."""
    try:
        conn = _connect()
        cur = conn.cursor()
        parts = [
            "SELECT chats.jid, chats.name, chats.last_message_time, messages.content, "
            "messages.sender, messages.is_from_me FROM chats",
            "LEFT JOIN messages ON chats.jid = messages.chat_jid AND chats.last_message_time = messages.timestamp",
        ]
        where, params = [], []
        if query:
            where.append("(LOWER(chats.name) LIKE LOWER(?) OR chats.jid LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if where:
            parts.append("WHERE " + " AND ".join(where))
        order = "chats.last_message_time DESC" if sort_by == "last_active" else "chats.name"
        parts.append(f"ORDER BY {order} LIMIT ? OFFSET ?")
        params.extend([limit, page * limit])

        cur.execute(" ".join(parts), tuple(params))
        out = []
        for r in cur.fetchall():
            c = Chat(
                jid=r[0], name=r[1],
                last_message_time=datetime.fromisoformat(r[2]) if r[2] else None,
                last_message=r[3], last_sender=r[4], last_is_from_me=bool(r[5]) if r[5] is not None else None,
            )
            d = asdict(c); d["is_group"] = c.is_group
            if d["last_message_time"]:
                d["last_message_time"] = d["last_message_time"].isoformat()
            out.append(d)
        return out
    except (sqlite3.Error, FileNotFoundError) as e:
        return [{"error": str(e)}]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def search_contacts(query: str) -> List[dict]:
    """Find contacts (non-group chats) by name or number."""
    try:
        conn = _connect()
        cur = conn.cursor()
        pat = f"%{query}%"
        cur.execute(
            "SELECT DISTINCT jid, name FROM chats "
            "WHERE (LOWER(name) LIKE LOWER(?) OR LOWER(jid) LIKE LOWER(?)) AND jid NOT LIKE '%@g.us' "
            "ORDER BY name, jid LIMIT 50",
            (pat, pat),
        )
        return [asdict(Contact(phone_number=r[0].split("@")[0], name=r[1], jid=r[0])) for r in cur.fetchall()]
    except (sqlite3.Error, FileNotFoundError) as e:
        return [{"error": str(e)}]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_direct_chat_by_contact(phone_number: str) -> Optional[Chat]:
    """Resolve a phone number to its 1:1 chat (ignores groups)."""
    digits = re.sub(r"\D", "", phone_number)
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT c.jid, c.name, c.last_message_time, m.content, m.sender, m.is_from_me FROM chats c "
            "LEFT JOIN messages m ON c.jid = m.chat_jid AND c.last_message_time = m.timestamp "
            "WHERE c.jid LIKE ? AND c.jid NOT LIKE '%@g.us' LIMIT 1",
            (f"%{digits}%",),
        )
        r = cur.fetchone()
        if not r:
            return None
        return Chat(
            jid=r[0], name=r[1],
            last_message_time=datetime.fromisoformat(r[2]) if r[2] else None,
            last_message=r[3], last_sender=r[4], last_is_from_me=bool(r[5]) if r[5] is not None else None,
        )
    except (sqlite3.Error, FileNotFoundError):
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _all_messages_for_chat(chat_jid: str) -> List[Message]:
    """Every stored message for a chat, oldest first."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT messages.timestamp, messages.sender, chats.name, messages.content, "
            "messages.is_from_me, messages.chat_jid, messages.id, messages.media_type, messages.filename "
            "FROM messages JOIN chats ON messages.chat_jid = chats.jid "
            "WHERE messages.chat_jid = ? ORDER BY messages.timestamp ASC",
            (chat_jid,),
        )
        out = []
        for r in cur.fetchall():
            m = Message(
                timestamp=datetime.fromisoformat(r[0]), sender=r[1], chat_name=r[2],
                content=r[3], is_from_me=bool(r[4]), chat_jid=r[5], id=r[6], media_type=r[7],
            )
            m.filename = r[8]  # type: ignore[attr-defined]
            out.append(m)
        return out
    finally:
        conn.close()


def list_media(chat_jid: str) -> List[dict]:
    """List every media message (image/video/audio/document) in a chat."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, timestamp, sender, is_from_me, media_type, filename, content "
            "FROM messages WHERE chat_jid = ? AND media_type != '' "
            "ORDER BY timestamp ASC",
            (chat_jid,),
        )
        return [
            {
                "message_id": r[0], "timestamp": r[1], "sender": r[2],
                "is_from_me": bool(r[3]), "media_type": r[4], "filename": r[5], "caption": r[6],
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def download_media(message_id: str, chat_jid: str) -> Optional[str]:
    """Ask the bridge to decrypt+download one media file. Returns local path."""
    try:
        resp = requests.post(
            f"{WHATSAPP_API_BASE_URL}/download",
            json={"message_id": message_id, "chat_jid": chat_jid},
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                return data.get("path")
        return None
    except requests.RequestException:
        return None


# ---------------------------------------------------------------- guarded send

def send_message(recipient: str, message: str) -> dict:
    """Send ONE text message to ONE recipient via the bridge (rate-limited)."""
    if not recipient:
        return {"ok": False, "error": "recipient required"}
    try:
        resp = requests.post(
            f"{WHATSAPP_API_BASE_URL}/send",
            json={"recipient": recipient, "message": message},
            timeout=60,
        )
        data = resp.json()
        return {"ok": bool(data.get("success")), "detail": data.get("message")}
    except requests.RequestException as e:
        return {"ok": False, "error": f"bridge not reachable ({e}). Is the bridge running/restarted?"}


def send_file(recipient: str, file_path: str, caption: str = "") -> dict:
    """Send ONE local file to ONE recipient via the bridge (rate-limited)."""
    if not recipient:
        return {"ok": False, "error": "recipient required"}
    if not os.path.isfile(file_path):
        return {"ok": False, "error": f"file not found: {file_path}"}
    try:
        resp = requests.post(
            f"{WHATSAPP_API_BASE_URL}/send_file",
            json={"recipient": recipient, "file_path": os.path.abspath(file_path), "caption": caption},
            timeout=180,
        )
        data = resp.json()
        return {"ok": bool(data.get("success")), "detail": data.get("message")}
    except requests.RequestException as e:
        return {"ok": False, "error": f"bridge not reachable ({e}). Is the bridge running/restarted?"}


# ---------------------------------------------------------------- the headline tool

def build_client_context(phone_number: str, download_media_files: bool = True) -> dict:
    """Pull the WHOLE 1:1 conversation with `phone_number` into a working context.

    Produces context/<digits>/CONTEXT.md plus a media/ folder of every photo,
    video, doc and audio the client shared. Returns a summary dict.
    """
    if not db_ready():
        return {
            "ok": False,
            "error": "Bridge database not found yet. Start the bridge (run-bridge.sh), "
                     "scan the QR with your phone, and wait for history sync before building context.",
        }
    chat = get_direct_chat_by_contact(phone_number)
    if chat is None:
        return {
            "ok": False,
            "error": f"No 1:1 chat found for '{phone_number}'. "
                     "Make sure the bridge is running, your phone is linked, and this "
                     "contact has messaged you since linking (or was in the synced history). "
                     "Try search_contacts to see what's captured.",
        }

    digits = re.sub(r"\D", "", phone_number)
    out_dir = os.path.abspath(os.path.join(CONTEXT_DIR, digits))
    media_dir = os.path.join(out_dir, "media")
    os.makedirs(media_dir, exist_ok=True)

    messages = _all_messages_for_chat(chat.jid)
    contact_name = chat.name or digits

    # Partition + collect
    client_msgs = [m for m in messages if not m.is_from_me and (m.content or m.media_type)]
    media_rows = []
    links = []
    counts = {"image": 0, "video": 0, "audio": 0, "document": 0}

    for m in messages:
        for url in URL_RE.findall(m.content or ""):
            links.append((url, "Me" if m.is_from_me else contact_name, m.timestamp))
        if m.media_type:
            counts[m.media_type] = counts.get(m.media_type, 0) + 1
            local = None
            if download_media_files:
                path = download_media(m.id, chat.jid)
                if path and os.path.exists(path):
                    dest = os.path.join(media_dir, os.path.basename(path))
                    try:
                        if os.path.abspath(path) != os.path.abspath(dest):
                            shutil.copy2(path, dest)
                        local = os.path.relpath(dest, out_dir)
                    except Exception:
                        local = path
            media_rows.append({
                "type": m.media_type,
                "when": m.timestamp,
                "from": "Me" if m.is_from_me else contact_name,
                "caption": m.content or "",
                "local": local or "(not downloaded)",
                "message_id": m.id,
            })

    first = messages[0].timestamp if messages else None
    last = messages[-1].timestamp if messages else None
    generated = datetime.now().isoformat(timespec="seconds")

    # --- write CONTEXT.md ---
    lines = []
    lines.append(f"# Client Context — {contact_name} (+{digits})\n")
    lines.append(f"- **Chat JID:** `{chat.jid}`")
    lines.append(f"- **Contact name:** {contact_name}")
    lines.append(f"- **Messages captured:** {len(messages)}")
    if first and last:
        lines.append(f"- **Date range:** {first:%Y-%m-%d %H:%M} → {last:%Y-%m-%d %H:%M}")
    total_media = sum(counts.values())
    lines.append(
        f"- **Media shared:** {total_media} "
        f"({counts.get('image',0)} img, {counts.get('video',0)} vid, "
        f"{counts.get('document',0)} doc, {counts.get('audio',0)} audio)"
    )
    lines.append(f"- **Links shared:** {len(links)}")
    lines.append(f"- **Generated:** {generated}\n")
    lines.append(
        "> ⚠️ Read-only capture. Covers what the bridge synced: the history WhatsApp "
        "pushed when you linked + everything live since. Very old history may be partial.\n"
    )

    lines.append("## 📋 What the client asked (their messages)\n")
    if client_msgs:
        for m in client_msgs:
            tag = f"`[{m.media_type}]` " if m.media_type else ""
            body = m.content.strip() if m.content else f"({m.media_type} shared)"
            lines.append(f"- **{m.timestamp:%Y-%m-%d %H:%M}** — {tag}{body}")
    else:
        lines.append("_No inbound messages captured yet._")
    lines.append("")

    if total_media:
        lines.append("## 🖼 Media shared\n")
        lines.append("| # | Type | When | From | Caption | Local file |")
        lines.append("|---|------|------|------|---------|-----------|")
        for i, r in enumerate(media_rows, 1):
            cap = (r["caption"] or "").replace("|", "\\|").replace("\n", " ")[:60]
            lines.append(
                f"| {i} | {r['type']} | {r['when']:%Y-%m-%d %H:%M} | {r['from']} | {cap} | `{r['local']}` |"
            )
        lines.append("")

    if links:
        lines.append("## 🔗 Links shared\n")
        for url, who, when in links:
            lines.append(f"- {url}  — _{who}, {when:%Y-%m-%d %H:%M}_")
        lines.append("")

    lines.append("## 💬 Full transcript\n")
    for m in messages:
        who = "Me" if m.is_from_me else contact_name
        tag = f"`[{m.media_type}]` " if m.media_type else ""
        body = (m.content or "").strip()
        lines.append(f"**{m.timestamp:%Y-%m-%d %H:%M} · {who}:** {tag}{body}".rstrip())
    lines.append("")

    context_path = os.path.join(out_dir, "CONTEXT.md")
    with open(context_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # machine-readable sidecar
    with open(os.path.join(out_dir, "context.json"), "w", encoding="utf-8") as f:
        json.dump({
            "phone": digits, "chat_jid": chat.jid, "contact_name": contact_name,
            "message_count": len(messages), "media": media_rows,
            "links": [{"url": u, "from": w, "when": t.isoformat()} for u, w, t in links],
            "generated": generated,
        }, f, indent=2, default=str)

    return {
        "ok": True,
        "contact": contact_name,
        "phone": f"+{digits}",
        "chat_jid": chat.jid,
        "messages_captured": len(messages),
        "client_message_count": len(client_msgs),
        "media_count": total_media,
        "media_breakdown": counts,
        "links_count": len(links),
        "context_file": context_path,
        "media_folder": media_dir,
        "date_range": f"{first:%Y-%m-%d} → {last:%Y-%m-%d}" if first and last else None,
    }
