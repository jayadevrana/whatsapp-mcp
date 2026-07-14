# whatsapp-mcp

![status](https://img.shields.io/badge/status-working-brightgreen)
![license](https://img.shields.io/badge/license-MIT-blue)
![go](https://img.shields.io/badge/bridge-Go%20%2B%20whatsmeow-00ADD8)
![python](https://img.shields.io/badge/server-Python%203.11%2B-3776AB)
![mcp](https://img.shields.io/badge/protocol-MCP-black)

Give an AI assistant (Claude, or anything that speaks [MCP](https://modelcontextprotocol.io))
**read access to your own WhatsApp** — and a **narrow, guarded ability to reply** —
so you can turn a messy client chat into a working document and fire back a
code snippet or a file without alt-tabbing into the wrong window.

It runs **entirely on your machine**. Your messages, media, and the WhatsApp
session key never leave the laptop. There is no cloud, no server, no telemetry.

```text
"Fetch everything the client at +91 98765 43210 sent me"
        │
        ▼
context/919876543210/CONTEXT.md   ← their requirements, full transcript,
                                    a table of every photo/video/doc,
                                    and every link they shared
```

> **Heads up — this is unofficial.** It talks to WhatsApp's multi-device
> protocol the same way WhatsApp Web does. That's against WhatsApp's Terms of
> Service, and the linked number carries a small but real risk of being flagged.
> Read [Terms of Service & ban risk](#terms-of-service--ban-risk) before you link
> a number you care about. Reading your own chats is the point here; the account
> risk is the price.

---

## TL;DR

```bash
# 1. build the bridge
cd bridge && go build -o whatsapp-bridge . && cd ..

# 2. set up the python server
cd server && uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install "mcp[cli]>=1.2.0" "requests>=2.31.0" && cd ..

# 3. register the MCP server (Claude Code example)
claude mcp add whatsapp --scope user -- \
  "$PWD/server/.venv/bin/python" "$PWD/server/main.py"

# 4. start the bridge and scan the QR with your phone
./run-bridge.sh          # WhatsApp → Settings → Linked Devices → Link a device

# 5. restart your MCP client, then just ask:
#    "build a context doc for +91 98765 43210"
```

---

## The idea

WhatsApp is where a huge amount of freelance and small-business work actually
happens. A client sends you requirements across 40 messages, three screenshots,
a voice note, and a PDF — and now it's *your* job to reconstruct all of that into
something you can build from. Then you reply by copy-pasting code into a phone
keyboard and hope you tapped the right chat.

This project makes both halves programmable:

- **Read** — pull a whole conversation (text + media + links) into a single tidy
  `CONTEXT.md` you (or your AI) can work from.
- **Reply** — send *one* message or *one* file to *one* recipient, with the human
  confirming first and a hard anti-bulk guard in the bridge.

It is deliberately **not** a marketing/broadcast tool. See
[Safety & anti-bulk design](#safety--anti-bulk-design).

---

## Architecture

Two small processes talk over a local SQLite file and a loopback HTTP port:

```text
┌────────────────────────────┐         ┌─────────────────────────────┐
│  Go bridge (whatsmeow)      │         │  Python MCP server (FastMCP)│
│                            │         │                             │
│  • links like WhatsApp Web │  writes │  • reads the SQLite DB      │
│    via QR (multi-device)   ├────────►│  • build_client_context()   │
│  • stores chats + messages │ SQLite  │  • send_message/send_file   │
│    + media-decrypt keys    │         │    → call the bridge        │
│  • serves 127.0.0.1:8080   │◄────────┤                             │
│    /api/download (read)    │  HTTP   │                             │
│    /api/send*  (guarded)   │         │                             │
└──────────────┬─────────────┘         └──────────────┬──────────────┘
               │                                        │ stdio (MCP)
               ▼                                        ▼
        WhatsApp servers                          Claude / MCP client
```

**Why two processes?** The WhatsApp multi-device protocol (handshake, Signal
session ratchets, media encryption, history sync) is genuinely hard, and the
[`whatsmeow`](https://github.com/tulir/whatsmeow) Go library already implements
it correctly. So the Go side owns "talk to WhatsApp," and the Python side owns
"be an MCP server." They meet at a plain SQLite file plus a loopback port —
nothing is exposed off-host.

---

## Install & setup

### Prerequisites

| Tool | Why | Check |
|------|-----|-------|
| **Go** ≥ 1.24 | build the bridge | `go version` |
| **Python** ≥ 3.11 | MCP server | `python3 --version` |
| **uv** (or pip) | python env | `uv --version` |
| an MCP client | e.g. Claude Code | — |

> On Apple Silicon with an **exFAT** external drive, build the Python `venv` on
> the internal disk — exFAT breaks virtualenvs. The repo can live anywhere.

### 1 — Build the bridge

```bash
cd bridge
go build -o whatsapp-bridge .
cd ..
```

### 2 — Python server

```bash
cd server
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install "mcp[cli]>=1.2.0" "requests>=2.31.0"
cd ..
```

### 3 — Register the MCP server

```bash
claude mcp add whatsapp --scope user -- \
  "$PWD/server/.venv/bin/python" "$PWD/server/main.py"
```

For a generic MCP client, point it at that same command
(`.../.venv/bin/python .../server/main.py`, stdio transport).

### 4 — Link your phone

```bash
./run-bridge.sh
```

A QR code prints in the terminal. On your phone: **WhatsApp → Settings →
Linked Devices → Link a device → scan it.** Leave the bridge running — it
captures new messages live and serves media downloads. Then **restart your MCP
client** so the tools load.

You're linked when you see `[OK] Connected to WhatsApp` and, in a second shell:

```bash
sqlite3 bridge/store/whatsapp.db "SELECT jid FROM whatsmeow_device;"   # → your JID
```

---

## The tools

### Read

| Tool | What it does |
|------|--------------|
| **`build_client_context(phone_number)`** ★ | The headline. Pulls the entire 1:1 conversation into `context/<number>/CONTEXT.md`: their requirements, the full transcript, a table of every photo/video/doc (downloaded into `media/`), and every link shared. |
| `list_chats(query?)` | List conversations, newest-active first. |
| `search_contacts(query)` | Find a contact by name or number. |
| `find_chat_by_number("+91…")` | Resolve a number to its chat JID + metadata. |
| `list_messages(chat_jid?, query?, …)` | Read/search messages, newest first. |
| `list_media(chat_jid)` | Every media item in a chat, with message IDs. |
| `download_media(message_id, chat_jid)` | Decrypt + save one media file. |

### Send — guarded, 1:1, confirm first

| Tool | What it does |
|------|--------------|
| `send_message(recipient, message)` | Send **one** text (a reply, a code snippet) to **one** recipient. |
| `send_file(recipient, file_path, caption?)` | Send **one** local image / video / document. |

Send is intentionally constrained (see below). Your assistant should always
show you the recipient and content and wait for a yes before calling these.

---

## Example session

```text
you  ▸ build a context doc for the client at +91 98765 43210

ai   ▸ build_client_context("+91 98765 43210")
       → 63 messages, 5 media (3 img, 1 pdf, 1 video), 4 links
       → context/919876543210/CONTEXT.md

ai   ▸ "They want a SimpleTire-style catalog, budget ₹4L, 6 weeks.
        Reference sites and the logo/spec are in the context folder.
        Here's a proposed data model…"

you  ▸ reply to them: "Scope + timeline look good, starting today."

ai   ▸ (shows the exact text + recipient, waits for confirmation)
     ▸ send_message("+91 98765 43210", "Scope + timeline look good, starting today.")
       → sent ✓
```

---

## How it works under the hood

A few things worth knowing if you're going to hack on this:

- **Linked device, not a bot account.** `whatsmeow` pairs as one of your phone's
  linked devices (like WhatsApp Web/Desktop). Your phone stays the primary.

- **The SQLite schema is tiny.** `bridge/store/messages.db` has two tables:
  `chats(jid, name, last_message_time)` and `messages(id, chat_jid, sender,
  content, timestamp, is_from_me, media_type, filename, url, media_key,
  file_sha256, file_enc_sha256, file_length)`. The `whatsapp.db` alongside it is
  whatsmeow's own session/device store — **that's the secret; never share it.**

- **Media is decrypted on demand.** WhatsApp media is end-to-end encrypted. The
  bridge stores the per-message media keys at capture time; `download_media`
  re-derives the URL and decrypts the blob only when you ask, then caches it.

- **`@lid` vs `@s.whatsapp.net`.** Some senders (often businesses, or people
  whose number isn't in your contacts) show up under a *hidden* `@lid` JID
  instead of a phone-number JID. Searching by phone number will miss them — use
  `list_chats` / `search_contacts` by name, or search message content. (A real
  gotcha; this is why a "who is 89262…" lookup can come back empty even though
  the chat exists.)

- **History sync is a slice, not an archive.** When you link, WhatsApp pushes a
  window of recent history — not your entire multi-year backlog. Everything from
  the moment you link onward is captured live and in full. Very old messages may
  simply not be there.

- **Timestamps are second-precision.** WhatsApp times are Unix seconds, so the Go
  side writes them with zero fractional part and the Python side parses them with
  a plain `datetime.fromisoformat` — no format juggling needed.

---

## Safety & anti-bulk design

Sending raises the account risk versus pure reading, so send is fenced in at the
**bridge** level (not just politely in the docs):

- **One recipient per call.** `resolveRecipient` rejects anything containing
  `, ; \n \r \t` or more than three spaces — you cannot pass a list.
- **Rate limited.** A sliding window allows at most **8 sends per 60 seconds**;
  the 9th is refused with an anti-bulk error. This kills runaway loops and any
  attempt to blast messages.
- **Human-in-the-loop.** The tool descriptions instruct the assistant to confirm
  recipient + content with you before every send.
- **No group blasting, no contact scraping, no scheduling.** Not built, on
  purpose.

These aren't suggestions in a README — they're enforced in
[`bridge/main.go`](bridge/main.go) (`allowSend`, `resolveRecipient`).

---

## Terms of Service & ban risk

This uses WhatsApp's multi-device protocol unofficially, which **violates
WhatsApp's Terms of Service.** Realistically:

- What actually gets numbers banned is **spam / bulk sending / messaging
  strangers at scale** — none of which this does (that's why send is 1:1 and
  rate-limited).
- For **read-only** use the practical risk is low; the worst common outcome is a
  linked-device disconnect (just re-scan the QR).
- Still, **zero guarantee is impossible.** Link an *established* number you use
  normally, keep volume low and human-like, and don't run multiple automations
  on the same number. If a number is business-critical, consider a secondary one.

You accept this risk by linking. See the [MIT license](LICENSE) — no warranty.

---

## Troubleshooting

| Symptom | Fix |
|--------|-----|
| `websocket: close 1006` right after start, no QR | Your `whatsmeow` is too old; WhatsApp hard-closes outdated clients. Update: `cd bridge && go get go.mau.fi/whatsmeow@latest && go build -o whatsapp-bridge .` |
| No QR appears | The first connect can drop once; it auto-retries. If it times out (3 min), `Ctrl+C` and re-run `./run-bridge.sh`. |
| Tools missing in the client | Restart your MCP client after `claude mcp add`. |
| `build_client_context` says "database not found" | The bridge hasn't run/linked yet, or history hasn't synced. Start the bridge, scan, wait. |
| A known contact isn't found by number | They're probably under an `@lid` hidden JID — search by name or message content instead. |
| Media won't download | The bridge must be running (media decrypts on demand via `127.0.0.1:8080`). |
| `send` returns "bridge not reachable" | Start the bridge; `/api/send` only comes up after you link. |

---

## Project layout

```text
whatsapp-mcp/
├── bridge/            Go bridge (whatsmeow)
│   ├── main.go        connect, capture, /api/download + guarded /api/send*
│   └── store/         session (whatsapp.db) + messages.db   ← gitignored, private
├── server/            Python MCP server
│   ├── main.py        FastMCP tool definitions
│   ├── whatsapp.py    SQLite reads + context builder + send wrappers
│   └── .venv/         mcp, requests                          ← gitignored
├── context/           generated CONTEXT.md + media           ← gitignored, private
├── run-bridge.sh
├── LICENSE
└── README.md
```

Everything private (session keys, messages, downloaded media, generated context)
is **gitignored** and never committed — that's enforced by
[`.gitignore`](.gitignore), so `git push` cannot leak it.

---

## Roadmap / limitations

- Read covers text + image/video/audio/document; reactions, polls, and stickers
  aren't parsed yet.
- History depth is bounded by what WhatsApp pushes on link.
- Group *reading* works; group *sending* is intentionally not exposed.
- No Windows testing (developed on macOS / Apple Silicon).

## Credits

- [`whatsmeow`](https://github.com/tulir/whatsmeow) by Tulir Asokan — the Go
  library that does the hard WhatsApp protocol work.
- The bridge design was inspired by the community
  [`whatsapp-mcp`](https://github.com/lharries/whatsapp-mcp) project; this
  version rewrites the surface around read + a deliberately guarded 1:1 send.

## License

[MIT](LICENSE) © 2026 Jayadev Rana. Provided as-is, without warranty. You are
responsible for how you use it and for complying with WhatsApp's terms.
