# WhatsApp MCP (read + guarded send)

Pull your **own** WhatsApp chats/media into Claude, and send **individual**
replies (a message, a code snippet, a file) without copy-paste or wrong-window
mistakes. Built for client work: fetch a client's requirements → work → reply.

**Not a bulk tool.** Send is constrained by design:
- **One recipient per call** — comma/newline lists are rejected in the bridge.
- **Rate-limited** — max 8 sends / 60s (anti-bulk / anti-loop guard).
- **Human-in-the-loop** — Claude confirms recipient + content with you before every send.

## How it works

```
Go bridge (whatsmeow) ──► local SQLite ──► Python MCP server ──► Claude
  links like WhatsApp Web    bridge/store/     server/main.py
  local API :8080 = /api/download (read) + /api/send, /api/send_file (guarded)
```

Everything is local. Nothing is uploaded anywhere except the messages you
explicitly send to a recipient.

## One-time setup

1. **Start the bridge** and link your phone (QR the first time):
   ```bash
   ~/whatsapp-mcp/run-bridge.sh
   ```
   Scan in **WhatsApp → Settings → Linked Devices → Link a device**. Leave it running.
2. **Restart Claude Code** so the `whatsapp` MCP server loads.

## Tools

**Read**
- `build_client_context("+91…")` ★ — whole conversation → `context/<number>/CONTEXT.md`
  (requirements, transcript, media table, links) + downloads all media.
- `list_chats`, `search_contacts`, `find_chat_by_number`, `list_messages`,
  `list_media`, `download_media`.

**Send (guarded, 1:1, confirm first)**
- `send_message(recipient, message)` — one text/code reply to one recipient.
- `send_file(recipient, file_path, caption)` — one image/video/document.

## ⚠️ Terms of Service

Uses WhatsApp's multi-device protocol unofficially (like any WhatsApp Web
automation) → against WhatsApp ToS; small but real risk the **linked number**
gets flagged. **Sending raises that risk vs. read-only** — bulk/spam is what
actually gets numbers banned, which is why send here is one-at-a-time,
rate-limited, and human-confirmed. Keep it low-volume and human-like.

> Keep `whatsmeow` current: `cd bridge && go get go.mau.fi/whatsmeow@latest && go build -o whatsapp-bridge .`
> An outdated client gets hard-closed by WhatsApp (websocket 1006).

## Layout

```
whatsapp-mcp/
├── bridge/    Go bridge (whatsmeow) + binary; store/ = session+messages (gitignored)
├── server/    Python MCP server (.venv: mcp, requests)
├── context/   generated client-context docs (gitignored — private)
└── run-bridge.sh
```
