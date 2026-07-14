#!/usr/bin/env bash
# Start the READ-ONLY WhatsApp bridge. First run shows a QR code —
# scan it in WhatsApp > Settings > Linked Devices. Leave this running.
set -e
cd "$(dirname "$0")/bridge"
exec ./whatsapp-bridge
