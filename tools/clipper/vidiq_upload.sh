#!/bin/sh
# vidIQ-Upload eines Clips aus GTA-Clipper/out (resumable Upload zu storage.googleapis.com).
# Aufruf: bash vidiq_upload.sh <datei> <bytes> <signierte-url> video/mp4 <upload-id>
set -u; umask 077; F="$1"; N="$2"; U="$3"; T="$4"; ID="$5"
case "$U" in https://storage.googleapis.com/vidiq-user-videos/*) ;; *) echo "Nur vidIQ-Storage erlaubt" >&2; exit 1 ;; esac
case "$F" in */GTA-Clipper/out/*) ;; *) echo "Nur Dateien aus GTA-Clipper/out" >&2; exit 1 ;; esac
S="${TMPDIR:-/tmp}/vidiq-upload-$ID"
fail() { printf "vidIQ upload stopped: %s\n" "$*" >&2; exit 1; }
c() { curl -q -sS -g --connect-timeout 30 "$@"; }
code() { tr -d "\r" | sed -n "s/^HTTP\/[0-9.]* \([0-9][0-9][0-9]\).*/\1/p" | tail -n 1; }
hdr() { tr -d "\r" | grep -i "^$1:" | tail -n 1 | sed "s/^[^:]*: *//"; }
status() { c -m 60 -o /dev/null -D - -X PUT -H "Content-Length: 0" -H "Content-Range: bytes */$N" "$X" || fail "Verbindung weg, nochmal starten"; }
[ -f "$F" ] && [ -r "$F" ] || fail "kann $F nicht lesen"
A=$(wc -c < "$F" | tr -d " "); [ "$A" = "$N" ] || fail "$F hat $A Bytes, nicht $N"
X=$(cat "$S" 2>/dev/null); Q=""; [ -n "$X" ] && { Q=$(status) || exit 1; }
case "$(printf "%s\n" "$Q" | code)" in 200|201|308|408|429|5??) ;; *) X="" ;; esac
if [ -z "$X" ]; then
  R=$(c -m 60 -o /dev/null -D - -X POST -H "x-goog-resumable: start" -H "x-upload-content-type: $T" -H "x-upload-content-length: $N" -H "Content-Length: 0" "$U") || fail "storage nicht erreichbar"
  X=$(printf "%s\n" "$R" | hdr location); [ -n "$X" ] || fail "Link abgelaufen (1 h), neuen anfordern"
  { printf "%s\n" "$X" > "$S"; } 2>/dev/null; Q=$(status) || exit 1
fi
case "$(printf "%s\n" "$Q" | code)" in
  200|201) ;;
  308) R=$(printf "%s\n" "$Q" | hdr range | sed "s/.*-//"); O=$(( ${R:--1} + 1 ))
       W=$(c -Y 1 -y 120 -o /dev/null -w "%{http_code}" -X PUT -C "$O" -T "$F" "$X")
       case "$W" in 200|201) ;; *) fail "abgebrochen, nochmal starten" ;; esac ;;
  *) fail "unerwartete Antwort, nochmal starten" ;;
esac
echo "vidIQ upload complete: $ID"
