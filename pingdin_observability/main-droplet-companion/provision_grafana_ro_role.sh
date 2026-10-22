#!/usr/bin/env bash
# main-droplet-companion/provision_grafana_ro_role.sh
#
# RUN ON THE MAIN pingdin_platform DROPLET -- this script does not touch
# the observability droplet at all. It exists here (not duplicated
# permanently into pingdin_platform's own scripts/) so this repo stays
# genuinely self-contained and improvable independently, per Hobo's own
# instruction -- but the role it creates has to live where core.tenant's
# real data lives, which is the main droplet's own pingdin-db.
#
# Idempotent, same pattern pingdin_platform's own
# scripts/provision_monitor_role.sh already uses: checks .env first,
# generates a password only if genuinely unset, ALTER ROLE either way.
#
# What this grants, and deliberately nothing more:
#   - CONNECT on the pingdin database
#   - USAGE on the core schema (edge schema: NOT granted at all)
#   - SELECT ONLY on core.audit_log, core.signal_ledger, and
#     core.tenant (id, name, slug) -- core.signal_ledger added
#     BUILD_LOG Journey 134: it's already tokenized/de-identified by
#     design (migration core/065's own "no amount_value/amount_usd...
#     exact accuracy-critical numbers never enter Core at all"), and
#     as of migration core/091 it can also hold genuine tenant/agent-
#     level aggregate rows (subject_token IS NULL) -- pure counts,
#     never traceable to a specific person or message. Same narrow-
#     grant discipline this script already applies to core.audit_log
#     -- not a widening of what this role can see into edge.* (still,
#     and always, NOT granted).
#
# CORRECTION, BUILD_LOG Journey 134: this script's own prior comment
# here claimed core.audit_log is FORCE ROW LEVEL SECURITY and that
# BYPASSRLS was needed to read it. That was factually wrong, found
# during an unrelated RLS audit and confirmed directly against
# pingdin_platform's own migrations/core/012_rls_policies.sql, which
# EXPLICITLY AND DELIBERATELY EXCLUDES core.audit_log from RLS
# entirely (that migration's own comment: "the platform owner's own
# cross-tenant audit review... is a legitimate, real access pattern
# this table must support, and RLS would make that a fight against
# the database rather than a feature of it"). core.signal_ledger
# (migration core/065) was never put under RLS either. Neither table
# this role reads has ever needed a BYPASSRLS bypass -- the real,
# actual protection was always the narrow GRANTs above, which are and
# were correct. BYPASSRLS has been sitting on this role granting an
# elevation it never used. Removed below, per least privilege: a role
# should never hold a capability it has no real table to exercise it
# against, even a harmless one.
set -euo pipefail

# REAL BUG FOUND LIVE: the original relative-path logic
# (`cd "$(dirname "$0")/../.."`) assumed this script would run from a
# subdirectory (e.g. scripts/), so two levels up would land on
# /opt/pingdin_platform. Run directly from /opt/pingdin_platform itself
# (the documented, actual way this got used), `dirname "$0"` is ".",
# so "../.." from "." lands in "/" — which exists, so the `cd` SUCCEEDS
# there instead of failing into the `|| cd /opt/pingdin_platform`
# fallback, and then finds no .env and exits with a confusing error.
# Fixed: just target the one real, documented install path directly —
# no relative-path guessing based on how the script happened to be
# invoked.
cd /opt/pingdin_platform

ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
  echo "Run this from /opt/pingdin_platform (or set cwd there) -- .env not found." >&2
  exit 1
fi

if grep -q '^PINGDIN_GRAFANA_RO_PASSWORD=' "$ENV_FILE" 2>/dev/null; then
  PW="$(grep '^PINGDIN_GRAFANA_RO_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
else
  PW="$(openssl rand -hex 24)"
  echo "PINGDIN_GRAFANA_RO_PASSWORD=${PW}" >> "$ENV_FILE"
fi

docker exec -i pingdin-db psql -U pingdin -d pingdin <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pingdin_grafana_ro') THEN
    CREATE ROLE pingdin_grafana_ro LOGIN PASSWORD '${PW}';
  ELSE
    ALTER ROLE pingdin_grafana_ro LOGIN PASSWORD '${PW}';
  END IF;
END
\$\$;

ALTER ROLE pingdin_grafana_ro NOBYPASSRLS;

GRANT CONNECT ON DATABASE pingdin TO pingdin_grafana_ro;
GRANT USAGE ON SCHEMA core TO pingdin_grafana_ro;
GRANT SELECT ON core.audit_log TO pingdin_grafana_ro;
GRANT SELECT ON core.signal_ledger TO pingdin_grafana_ro;
GRANT SELECT ON core.tenant_usage TO pingdin_grafana_ro;
GRANT SELECT (id, name, slug) ON core.tenant TO pingdin_grafana_ro;
-- Explicitly NOT granting anything on core.pii_vault, core.subject_key_ref,
-- core.consent, core.masked_record, core.dsr_request, or ANY edge.* table.
-- If a future dashboard genuinely needs another core.* table, add it here
-- by name -- never GRANT ... ON ALL TABLES for this role, and never grant
-- edge schema USAGE at all (that's where actual tenant content lives).
SQL

echo "pingdin_grafana_ro provisioned (NOBYPASSRLS, narrow table grants). Password written to ${ENV_FILE} as PINGDIN_GRAFANA_RO_PASSWORD."
echo "Copy that same value into the OBSERVABILITY droplet's own .env (same key name) -- never commit either .env."
