#!/usr/bin/env bash
# Nur auf dem Hauptrechner (nie auf der Reserve) – dann sicherheit-status.py.
. "$(dirname "$(readlink -f "$0")")/../reserve/rollen.conf"
ip -4 addr show "$DEV" | grep -q "inet $HAUPT_IP/" || exit 0
[ "$(cat /var/lib/domus/rolle 2>/dev/null || echo haupt)" = haupt ] || exit 0
exec python3 "$(dirname "$(readlink -f "$0")")/sicherheit-status.py"
