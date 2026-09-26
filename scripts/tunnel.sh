#!/usr/bin/env bash
# Public access through Cloudflare Tunnel (free, no open ports).
#   ./scripts/tunnel.sh start   quick tunnel -> random https://<words>.trycloudflare.com link
#   ./scripts/tunnel.sh named   permanent link on your Cloudflare account (TUNNEL_TOKEN in .env)
#   ./scripts/tunnel.sh url     print the current quick-tunnel link
#   ./scripts/tunnel.sh stop    stop public access (the app keeps running locally)
#
# Uses a natively installed `cloudflared` when available (recommended on macOS/Windows: Docker Desktop's
# network layer can drop the tunnel's UDP/QUIC connection); otherwise the `tunnel` Docker service
# (fine on Linux servers). The free quick-tunnel API is sometimes slow, so the start is retried.
set -uo pipefail
cd "$(dirname "$0")/.."
STATE=.tunnel
mkdir -p "$STATE"
C="docker compose -f docker-compose.yml"
HOST=$(grep -E '^DOMAIN=' .env 2>/dev/null | cut -d= -f2 | cut -d' ' -f1); HOST=${HOST:-localhost}
NATIVE=$(command -v cloudflared || true)
ARGS=(tunnel --no-autoupdate --url https://localhost --no-tls-verify --http-host-header "$HOST" --origin-server-name "$HOST")

url_from() { grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$1" 2>/dev/null | grep -v 'https://api\.' | tail -1; }

stop() {
  [ -f "$STATE/pid" ] && kill "$(cat "$STATE/pid")" 2>/dev/null
  rm -f "$STATE/pid" "$STATE/url"
  $C --profile tunnel --profile tunnel-named stop tunnel tunnel-named >/dev/null 2>&1 || true
}

wait_ready() {  # $1 = log file; returns 0 when the tunnel has a link and an edge connection
  for _ in $(seq 1 25); do
    grep -q "Registered tunnel connection" "$1" 2>/dev/null && [ -n "$(url_from "$1")" ] && return 0
    grep -q "failed to request quick Tunnel" "$1" 2>/dev/null && return 1
    sleep 2
  done
  return 1
}

start() {
  curl -sk -o /dev/null https://localhost/login || { echo "GleasonAI is not running - run 'make up' first"; exit 1; }
  stop
  for attempt in $(seq 1 8); do
    if [ -n "$NATIVE" ]; then
      nohup "$NATIVE" "${ARGS[@]}" > "$STATE/log" 2>&1 &
      echo $! > "$STATE/pid"
    else
      $C --profile tunnel up -d --force-recreate tunnel >/dev/null 2>&1
      sleep 3
      $C --profile tunnel logs tunnel > "$STATE/log" 2>&1
    fi
    if [ -n "$NATIVE" ] && wait_ready "$STATE/log"; then break; fi
    if [ -z "$NATIVE" ]; then
      for _ in $(seq 1 25); do $C --profile tunnel logs tunnel > "$STATE/log" 2>&1
        grep -q "Registered tunnel connection" "$STATE/log" && [ -n "$(url_from "$STATE/log")" ] && break 2; sleep 2; done
    fi
    echo "  attempt $attempt: Cloudflare's quick-tunnel service did not answer in time, retrying..."
    stop; sleep 3
  done
  url=$(url_from "$STATE/log")
  [ -n "$url" ] || { echo "could not start the tunnel - see $STATE/log"; exit 1; }
  echo "$url" > "$STATE/url"
  echo
  echo "  GleasonAI is online at:  $url"
  echo "  (share this link; it changes every time the tunnel restarts - use 'make tunnel-named' for a fixed one)"
  echo
}

named() {
  token=$(grep -E '^TUNNEL_TOKEN=' .env 2>/dev/null | cut -d= -f2-)
  [ -n "$token" ] || { echo "set TUNNEL_TOKEN in .env first (docs/SETUP_NEW_MACHINE.md part C2)"; exit 1; }
  stop
  if [ -n "$NATIVE" ]; then
    TUNNEL_TOKEN="$token" nohup "$NATIVE" tunnel --no-autoupdate run > "$STATE/log" 2>&1 &
    echo $! > "$STATE/pid"
  else
    $C --profile tunnel-named up -d tunnel-named
  fi
  echo "named tunnel started - your app is at the hostname you configured in Cloudflare"
}

case "${1:-start}" in
  start) start ;;
  named) named ;;
  url) cat "$STATE/url" 2>/dev/null || echo "no quick tunnel running (make tunnel)" ;;
  stop) stop; echo "public access stopped" ;;
  *) echo "usage: $0 start|named|url|stop"; exit 1 ;;
esac
