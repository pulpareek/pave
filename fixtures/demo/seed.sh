#!/usr/bin/env bash
# Seed the PAVE demo dataset (see docs/DEMO_SCRIPT.md).
#
#   ./fixtures/demo/seed.sh                       # seed D + E (the §6 day-2 fixtures)
#   ./fixtures/demo/seed.sh a b                   # seed specific projects
#   PAVE_URL=http://127.0.0.1:8731 ./seed.sh all  # everything
#
# Submits as the requester persona, then approves as platform (+ compliance for TIER2) so the
# project reaches ACTIVE and its assets show up in the registry / governance sweep.
set -euo pipefail

PAVE_URL="${PAVE_URL:-http://127.0.0.1:8731}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

req_hdrs=(-H 'Content-Type: application/json' -H 'X-Pave-Persona: requester'
          -H 'X-Forwarded-Email: lead.dev@pave.test' -H 'X-Forwarded-Groups: rwe-clinical,platform')
plat_hdrs=(-H 'Content-Type: application/json' -H 'X-Pave-Persona: platform'
           -H 'X-Forwarded-Email: platform@pave.test' -H 'X-Forwarded-Groups: pave-approvers')
comp_hdrs=(-H 'Content-Type: application/json' -H 'X-Pave-Persona: compliance'
           -H 'X-Forwarded-Email: compliance@pave.test' -H 'X-Forwarded-Groups: platform-admins')

seed_one() {
  local file="$1" resp id tier
  resp="$(curl -sS -X POST "$PAVE_URL/api/requests" "${req_hdrs[@]}" -d @"$file")"
  id="$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("request",{}).get("id",""))')"
  if [ -z "$id" ]; then
    echo "  FAILED $(basename "$file"):" >&2
    printf '%s\n' "$resp" | python3 -m json.tool >&2
    return 1
  fi
  tier="$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["routing"]["risk_tier"])')"
  echo "  submitted $(basename "$file" .json) -> $id ($tier)"

  curl -sS -X POST "$PAVE_URL/api/approvals/$id/decision" "${plat_hdrs[@]}" \
    -d '{"decision":"approve","reason":"Standards met","esignature":"Alex Rivera"}' >/dev/null
  if [ "$tier" = "TIER2" ]; then
    curl -sS -X POST "$PAVE_URL/api/approvals/$id/decision" "${comp_hdrs[@]}" \
      -d '{"decision":"approve","reason":"Compliance evidence attached","esignature":"Dr. Lena Fischer"}' >/dev/null
  fi
  echo "  approved  $id"
}

if [ "$#" -eq 0 ]; then
  targets=(d e)                       # default: just the §6 day-2 fixtures
elif [ "$1" = "all" ]; then
  targets=(a b c d e)
else
  targets=("$@")
fi

curl -sS -m 5 "$PAVE_URL/api/health" >/dev/null || { echo "PAVE not reachable at $PAVE_URL" >&2; exit 1; }
for t in "${targets[@]}"; do
  file=$(ls "$HERE/$t"-*.json 2>/dev/null | head -1)
  [ -n "$file" ] || { echo "no fixture for '$t'" >&2; exit 1; }
  seed_one "$file"
done

echo
curl -sS "$PAVE_URL/api/governance/sweep" "${plat_hdrs[@]}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("sweep: {} active | {} past-sunset | {} drift | {} clean".format(
    d["active_assets"], len(d["past_sunset"]), len(d["tag_drift"]), d["clean"]))
for a in d["past_sunset"]:
    print("  past-sunset {} ({}) sunset {}".format(
        a["asset_id"], a["classification"], a["sunset_date"]))'
