"""WhatsApp MCP server (read + guarded single-recipient send).

Read tools: chats, messages, media, and build_client_context.
Send tools: send_message / send_file — ONE recipient per call, rate-limited by
the bridge (no bulk). Always confirm recipient + content with the human before
calling a send tool.
"""
from typing import Optional

from mcp.server.fastmcp import FastMCP

import whatsapp as wa

mcp = FastMCP("whatsapp")


@mcp.tool()
def list_chats(query: Optional[str] = None, limit: int = 20, page: int = 0,
               sort_by: str = "last_active") -> list:
    """List WhatsApp chats. Optional `query` filters by name/JID. `sort_by`: 'last_active' or 'name'."""
    return wa.list_chats(query=query, limit=limit, page=page, sort_by=sort_by)


@mcp.tool()
def search_contacts(query: str) -> list:
    """Search your contacts (1:1 chats) by name or phone number."""
    return wa.search_contacts(query)


@mcp.tool()
def find_chat_by_number(phone_number: str) -> dict:
    """Resolve a phone number (any format, e.g. +91 98765 43210) to its 1:1 chat + metadata."""
    chat = wa.get_direct_chat_by_contact(phone_number)
    if chat is None:
        return {"found": False, "phone_number": phone_number}
    return {
        "found": True,
        "jid": chat.jid,
        "name": chat.name,
        "last_message_time": chat.last_message_time.isoformat() if chat.last_message_time else None,
        "last_message": chat.last_message,
    }


@mcp.tool()
def list_messages(chat_jid: Optional[str] = None, sender_phone_number: Optional[str] = None,
                  query: Optional[str] = None, after: Optional[str] = None,
                  before: Optional[str] = None, limit: int = 20, page: int = 0) -> str:
    """Read messages (newest first). Filter by chat_jid, sender number, text `query`, or ISO date range."""
    return wa.list_messages(
        after=after, before=before, sender_phone_number=sender_phone_number,
        chat_jid=chat_jid, query=query, limit=limit, page=page,
    )


@mcp.tool()
def list_media(chat_jid: str) -> list:
    """List every media item (image/video/audio/document) shared in a chat, with message IDs."""
    return wa.list_media(chat_jid)


@mcp.tool()
def download_media(message_id: str, chat_jid: str) -> dict:
    """Decrypt+download one media file (by message_id + chat_jid). Returns the local file path."""
    path = wa.download_media(message_id, chat_jid)
    if path:
        return {"ok": True, "path": path}
    return {"ok": False, "error": "Download failed. Is the bridge running and the message a media message?"}


@mcp.tool()
def build_client_context(phone_number: str, download_media_files: bool = True) -> dict:
    """★ Pull the WHOLE conversation with a client number into a ready-to-work context.

    Writes context/<number>/CONTEXT.md (their requirements, full transcript, a media
    table, and every link they shared) and downloads all photos/videos/docs into a
    media/ folder. Give it the client's number and start working from CONTEXT.md.
    """
    return wa.build_client_context(phone_number, download_media_files=download_media_files)


@mcp.tool()
def send_message(recipient: str, message: str) -> dict:
    """Send ONE text message (e.g. a reply or a code snippet) to ONE recipient.

    `recipient` is a phone number (+91…) or a chat JID. Lists are rejected and
    the bridge rate-limits sends. Confirm recipient + content with the user first.
    """
    return wa.send_message(recipient, message)


@mcp.tool()
def send_file(recipient: str, file_path: str, caption: str = "") -> dict:
    """Send ONE local file (image/video/document, e.g. a code file) to ONE recipient.

    `recipient` is a phone number or chat JID. One recipient only; rate-limited.
    Confirm recipient + file with the user first.
    """
    return wa.send_file(recipient, file_path, caption)


if __name__ == "__main__":
    mcp.run(transport="stdio")
