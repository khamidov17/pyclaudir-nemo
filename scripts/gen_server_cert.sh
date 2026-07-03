#!/usr/bin/env bash
# Generate a long-lived self-signed TLS cert for the Nemo servers (bare-IP
# VPS, so Let's Encrypt isn't an option). The app pins this cert on first
# connect (TOFU), so a MITM with a different cert is refused.
#
# Run ON the server, then add to the .env:
#   NEMO_TLS_CERT=/path/nemo-server.crt   NEMO_TLS_KEY=/path/nemo-server.key
#   VOICE_TLS_CERT=/path/nemo-server.crt  VOICE_TLS_KEY=/path/nemo-server.key
# and switch the app's Server URL to wss://IP:8765.
set -euo pipefail

# Pass the server's public IP explicitly — don't bake a deployment address into
# the repo. Usage: scripts/gen_server_cert.sh <server-ip> [out-dir]
IP="${1:?usage: gen_server_cert.sh <server-ip> [out-dir]   (e.g. 203.0.113.10)}"
OUT_DIR="${2:-$(pwd)/certs}"
mkdir -p "$OUT_DIR"

openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -keyout "$OUT_DIR/nemo-server.key" \
  -out "$OUT_DIR/nemo-server.crt" \
  -subj "/CN=nemo-server" \
  -addext "subjectAltName=IP:$IP"

chmod 600 "$OUT_DIR/nemo-server.key"
echo "Wrote $OUT_DIR/nemo-server.crt and .key (SAN IP:$IP, valid 10y)"
echo "SHA-256 fingerprint (the app pins this automatically on first connect):"
openssl x509 -in "$OUT_DIR/nemo-server.crt" -noout -fingerprint -sha256
