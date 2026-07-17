"""Lakebase (Postgres) connection pool + PAVE operational state.

Adapted from the med_agent DatabasePool: OAuth token refresh before the ~1h
Lakebase credential expiry, and graceful `demo_mode` fallback (in-memory) so the
app stays runnable without a database during local dev / demos.

Schema (in PAVE_SCHEMA):
  owners(owner_id, email, group_name, cost_center, active, ...)
  requests(id, project_id, ..., status, risk_tier, resources jsonb, ...)
  approvals(id, request_id, approver, decision, reason, esignature, gate, ...)
  assets(asset_id, request_id, type, names, external_id, owner_id, project_id,
         applied_tags, mode, status, provisioned_at, sunset_date, ...)
  quotas(principal, resource_type, used, limit_val)
  audit_events(event_id, ts, actor, request_id, asset_id, event_type,
               from_state, to_state, payload, reason)   -- append-only

The audit_events table is append-only BY CONVENTION: this module never issues
UPDATE/DELETE against it (ALCOA+ / 21 CFR Part 11 intent).
"""
import json
import logging
import time
from typing import Any, Optional

from . import config

logger = logging.getLogger("pave.db")

_TOKEN_TTL = 50 * 60  # refresh Lakebase credential before its ~1h expiry
S = config.PAVE_SCHEMA


def _coerce(row: dict, keys: tuple[str, ...]) -> dict:
    """Defensive: decode any jsonb field that came back as a JSON string (e.g.
    legacy double-encoded rows) so all consumers always get dict/list."""
    for k in keys:
        v = row.get(k)
        if isinstance(v, str):
            try:
                row[k] = json.loads(v)
            except Exception:  # noqa: BLE001
                pass
    return row


def _flatten(rec: dict) -> dict:
    """Surface expanded `metadata` jsonb fields at the top level (without clobbering
    core columns) so validation/tagging/spec consumers see them uniformly."""
    md = rec.get("metadata")
    if isinstance(md, dict):
        for k, v in md.items():
            rec.setdefault(k, v)
    return rec


async def _init_conn(conn):
    # Transparently encode/decode jsonb as Python objects.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


class Database:
    def __init__(self):
        self._pool: Optional[Any] = None
        self._token_ts: float = 0.0
        self.demo_mode: bool = False
        self.last_error: str = ""
        # in-memory fallback stores (demo mode only)
        self._mem: dict[str, list[dict]] = {
            "owners": [], "requests": [], "approvals": [],
            "assets": [], "quotas": [], "audit_events": [],
        }

    # ---- connection -----------------------------------------------------
    async def _connect(self) -> Optional[Any]:
        if not config.PGHOST:
            self.demo_mode = True
            self.last_error = "PGHOST not set (database resource not injected)"
            return None
        import asyncpg  # lazy: app still imports/runs (demo mode) without the driver
        password = config.get_db_password()
        self._token_ts = time.time()
        return await asyncpg.create_pool(
            host=config.PGHOST,
            port=config.PGPORT,
            database=config.PGDATABASE,
            user=config.PGUSER or "token",
            password=password,
            ssl="require",
            min_size=1,
            max_size=8,
            init=_init_conn,
        )

    async def pool(self) -> Optional[Any]:
        if self.demo_mode:
            return None
        if self._pool is not None and (time.time() - self._token_ts) > _TOKEN_TTL:
            await self._pool.close()
            self._pool = None
        if self._pool is None:
            try:
                self._pool = await self._connect()
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                logger.warning("Lakebase connection failed, demo mode: %s", e)
                self.demo_mode = True
                return None
        return self._pool

    async def health(self) -> dict:
        if self.demo_mode:
            return {"status": "demo", "error": self.last_error}
        try:
            p = await self.pool()
            if p is None:
                return {"status": "demo", "error": self.last_error}
            async with p.acquire() as c:
                await c.fetchval("SELECT 1")
            return {"status": "healthy", "schema": S}
        except Exception as e:  # noqa: BLE001
            return {"status": "unhealthy", "error": str(e)}

    # ---- schema ---------------------------------------------------------
    async def init_schema(self):
        p = await self.pool()
        if p is None:
            logger.warning("Lakebase unavailable; running with in-memory demo store")
            return
        async with p.acquire() as c:
            await c.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
            await c.execute(f"""
                CREATE TABLE IF NOT EXISTS {S}.owners (
                    owner_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    group_name TEXT,
                    cost_center TEXT,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")
            await c.execute(f"""
                CREATE TABLE IF NOT EXISTS {S}.requests (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    project_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    owner_id TEXT REFERENCES {S}.owners(owner_id),
                    owner_group TEXT,
                    owner_email TEXT,
                    cost_center TEXT,
                    business_domain TEXT,
                    data_classification TEXT,
                    environment TEXT,
                    region TEXT,
                    compliance_scope TEXT[] DEFAULT '{{}}',
                    custom_tags JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    resources JSONB NOT NULL DEFAULT '[]'::jsonb,
                    description TEXT,
                    justification TEXT,
                    gxp_relevant BOOLEAN DEFAULT FALSE,
                    contains_phi BOOLEAN DEFAULT FALSE,
                    sunset_date DATE,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    status TEXT NOT NULL DEFAULT 'SUBMITTED',
                    risk_tier TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")
            await c.execute(f"""
                CREATE TABLE IF NOT EXISTS {S}.approvals (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    request_id UUID NOT NULL REFERENCES {S}.requests(id) ON DELETE CASCADE,
                    approver TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT,
                    esignature TEXT,
                    gate TEXT,
                    signed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")
            await c.execute(f"""
                CREATE TABLE IF NOT EXISTS {S}.assets (
                    asset_id TEXT PRIMARY KEY,
                    request_id UUID REFERENCES {S}.requests(id) ON DELETE SET NULL,
                    type TEXT NOT NULL,
                    names JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    external_id TEXT,
                    owner_id TEXT REFERENCES {S}.owners(owner_id),
                    project_id TEXT,
                    applied_tags JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    mode TEXT NOT NULL DEFAULT 'simulated',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    provisioned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    sunset_date DATE,
                    provenance JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    decommissioned_at TIMESTAMPTZ
                )""")
            await c.execute(f"""
                CREATE TABLE IF NOT EXISTS {S}.quotas (
                    principal TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    used INT NOT NULL DEFAULT 0,
                    limit_val INT NOT NULL DEFAULT 100,
                    PRIMARY KEY (principal, resource_type)
                )""")
            await c.execute(f"""
                CREATE TABLE IF NOT EXISTS {S}.audit_events (
                    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                    actor TEXT NOT NULL,
                    request_id UUID,
                    asset_id TEXT,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    reason TEXT
                )""")
            # Idempotent migrations: CREATE TABLE IF NOT EXISTS won't add new columns
            # to a pre-existing table, so evolve the schema explicitly.
            for col_def in (
                "description TEXT", "justification TEXT",
                "gxp_relevant BOOLEAN DEFAULT FALSE", "contains_phi BOOLEAN DEFAULT FALSE",
                "sunset_date DATE", "metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
                "risk_tier TEXT",
            ):
                await c.execute(f"ALTER TABLE {S}.requests ADD COLUMN IF NOT EXISTS {col_def}")
            await c.execute(f"ALTER TABLE {S}.assets ADD COLUMN IF NOT EXISTS recertified_at TIMESTAMPTZ")
            await c.execute(f"ALTER TABLE {S}.assets ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{{}}'::jsonb")

            await c.execute(f"CREATE INDEX IF NOT EXISTS idx_req_status ON {S}.requests(status, created_at DESC)")
            await c.execute(f"CREATE INDEX IF NOT EXISTS idx_asset_owner ON {S}.assets(owner_id)")
            await c.execute(f"CREATE INDEX IF NOT EXISTS idx_audit_req ON {S}.audit_events(request_id, ts)")
        logger.info("PAVE schema '%s' initialized", S)

    # ---- audit (append-only) -------------------------------------------
    async def add_audit(self, *, actor: str, event_type: str, request_id: Optional[str] = None,
                        asset_id: Optional[str] = None, from_state: Optional[str] = None,
                        to_state: Optional[str] = None, payload: Optional[dict] = None,
                        reason: Optional[str] = None):
        payload = payload or {}
        p = await self.pool()
        if p is None:
            self._mem["audit_events"].append({
                "actor": actor, "event_type": event_type, "request_id": request_id,
                "asset_id": asset_id, "from_state": from_state, "to_state": to_state,
                "payload": payload, "reason": reason, "ts": time.time(),
            })
            return
        async with p.acquire() as c:
            await c.execute(
                f"""INSERT INTO {S}.audit_events
                    (actor, event_type, request_id, asset_id, from_state, to_state, payload, reason)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                actor, event_type,
                request_id, asset_id, from_state, to_state, payload, reason,
            )

    async def list_audit(self, request_id: Optional[str] = None, limit: int = 200) -> list[dict]:
        p = await self.pool()
        if p is None:
            evs = self._mem["audit_events"]
            if request_id:
                evs = [e for e in evs if e.get("request_id") == request_id]
            return evs[-limit:][::-1]
        if request_id:
            rows = await p.fetch(
                f"SELECT * FROM {S}.audit_events WHERE request_id=$1 ORDER BY ts DESC LIMIT $2",
                request_id, limit)
        else:
            rows = await p.fetch(
                f"SELECT * FROM {S}.audit_events ORDER BY ts DESC LIMIT $1", limit)
        return [_coerce(dict(r), ("payload",)) for r in rows]

    # ---- owners ---------------------------------------------------------
    async def upsert_owner(self, owner_id: str, email: str, group_name: str = "",
                           cost_center: str = "") -> dict:
        p = await self.pool()
        rec = {"owner_id": owner_id, "email": email, "group_name": group_name,
               "cost_center": cost_center, "active": True}
        if p is None:
            existing = next((o for o in self._mem["owners"] if o["owner_id"] == owner_id), None)
            if existing:
                existing.update(rec)
            else:
                self._mem["owners"].append(rec)
            return rec
        row = await p.fetchrow(
            f"""INSERT INTO {S}.owners (owner_id, email, group_name, cost_center)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (owner_id) DO UPDATE
                  SET email=EXCLUDED.email, group_name=EXCLUDED.group_name,
                      cost_center=EXCLUDED.cost_center, updated_at=now()
                RETURNING *""",
            owner_id, email, group_name, cost_center)
        return dict(row)

    async def get_owner(self, owner_id: str) -> Optional[dict]:
        p = await self.pool()
        if p is None:
            return next((o for o in self._mem["owners"] if o["owner_id"] == owner_id), None)
        row = await p.fetchrow(f"SELECT * FROM {S}.owners WHERE owner_id=$1", owner_id)
        return dict(row) if row else None

    # ---- requests -------------------------------------------------------
    async def create_request(self, req: dict) -> dict:
        p = await self.pool()
        if p is None:
            req = _flatten(dict(req))
            req.setdefault("id", f"mem-{len(self._mem['requests'])+1}")
            req.setdefault("status", "SUBMITTED")
            req["created_at"] = req["updated_at"] = time.time()
            self._mem["requests"].append(req)
            return req
        sunset = req.get("sunset_date")
        if isinstance(sunset, str) and sunset:
            import datetime as _dt
            try:
                sunset = _dt.date.fromisoformat(sunset[:10])
            except Exception:  # noqa: BLE001
                sunset = None
        row = await p.fetchrow(
            f"""INSERT INTO {S}.requests
                (project_id, project_name, requester, owner_id, owner_group, owner_email,
                 cost_center, business_domain, data_classification, environment, region,
                 compliance_scope, custom_tags, resources, description, justification,
                 gxp_relevant, contains_phi, sunset_date, metadata, status, risk_tier)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)
                RETURNING *""",
            req["project_id"], req["project_name"], req["requester"], req.get("owner_id"),
            req.get("owner_group"), req.get("owner_email"), req.get("cost_center"),
            req.get("business_domain"), req.get("data_classification"), req.get("environment"),
            req.get("region"), req.get("compliance_scope") or [],
            req.get("custom_tags") or {}, req.get("resources") or [],
            req.get("description"), req.get("justification"),
            bool(req.get("gxp_relevant")), bool(req.get("contains_phi")), sunset,
            req.get("metadata") or {},
            req.get("status", "SUBMITTED"), req.get("risk_tier"))
        return _flatten(_coerce(dict(row), ("custom_tags", "resources", "metadata")))

    async def get_request(self, request_id: str) -> Optional[dict]:
        p = await self.pool()
        if p is None:
            return next((r for r in self._mem["requests"] if str(r.get("id")) == str(request_id)), None)
        row = await p.fetchrow(f"SELECT * FROM {S}.requests WHERE id=$1", request_id)
        return _flatten(_coerce(dict(row), ("custom_tags", "resources", "metadata"))) if row else None

    async def list_requests(self, *, requester: Optional[str] = None,
                            status: Optional[str] = None, limit: int = 200) -> list[dict]:
        p = await self.pool()
        if p is None:
            rows = self._mem["requests"]
            if requester:
                rows = [r for r in rows if r.get("requester") == requester]
            if status:
                rows = [r for r in rows if r.get("status") == status]
            return rows[::-1][:limit]
        clauses, args = [], []
        if requester:
            args.append(requester); clauses.append(f"requester=${len(args)}")
        if status:
            args.append(status); clauses.append(f"status=${len(args)}")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(limit)
        rows = await p.fetch(
            f"SELECT * FROM {S}.requests{where} ORDER BY created_at DESC LIMIT ${len(args)}", *args)
        return [_flatten(_coerce(dict(r), ("custom_tags", "resources", "metadata"))) for r in rows]

    async def update_request_status(self, request_id: str, status: str) -> Optional[dict]:
        p = await self.pool()
        if p is None:
            r = await self.get_request(request_id)
            if r:
                r["status"] = status
                r["updated_at"] = time.time()
            return r
        row = await p.fetchrow(
            f"UPDATE {S}.requests SET status=$2, updated_at=now() WHERE id=$1 RETURNING *",
            request_id, status)
        return dict(row) if row else None

    async def set_request_resources(self, request_id: str, resources: list) -> Optional[dict]:
        """Replace a request's resources list (used when amending an existing project to add
        new resources). Caller is responsible for merging old + new before calling."""
        p = await self.pool()
        if p is None:
            r = await self.get_request(request_id)
            if r:
                r["resources"] = resources
                r["updated_at"] = time.time()
            return r
        row = await p.fetchrow(
            f"UPDATE {S}.requests SET resources=$2, updated_at=now() WHERE id=$1 RETURNING *",
            request_id, resources)
        return _flatten(_coerce(dict(row), ("custom_tags", "resources", "metadata"))) if row else None

    async def set_request_owner(self, request_id: str, owner_id: str):
        p = await self.pool()
        if p is None:
            r = await self.get_request(request_id)
            if r:
                r["owner_id"] = owner_id
            return
        await p.execute(f"UPDATE {S}.requests SET owner_id=$2, updated_at=now() WHERE id=$1",
                        request_id, owner_id)

    # ---- approvals ------------------------------------------------------
    async def add_approval(self, a: dict) -> dict:
        p = await self.pool()
        if p is None:
            a = dict(a); a["signed_at"] = time.time()
            self._mem["approvals"].append(a)
            return a
        row = await p.fetchrow(
            f"""INSERT INTO {S}.approvals (request_id, approver, decision, reason, esignature, gate)
                VALUES ($1,$2,$3,$4,$5,$6) RETURNING *""",
            a["request_id"], a["approver"], a["decision"], a.get("reason"),
            a.get("esignature"), a.get("gate"))
        return dict(row)

    async def list_approvals(self, request_id: str) -> list[dict]:
        p = await self.pool()
        if p is None:
            return [a for a in self._mem["approvals"] if str(a.get("request_id")) == str(request_id)]
        rows = await p.fetch(
            f"SELECT * FROM {S}.approvals WHERE request_id=$1 ORDER BY signed_at", request_id)
        return [dict(r) for r in rows]

    # ---- assets ---------------------------------------------------------
    async def add_asset(self, asset: dict) -> dict:
        p = await self.pool()
        if p is None:
            self._mem["assets"].append(dict(asset))
            return asset
        row = await p.fetchrow(
            f"""INSERT INTO {S}.assets
                (asset_id, request_id, type, names, external_id, owner_id, project_id,
                 applied_tags, mode, status, sunset_date, provenance)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (asset_id) DO UPDATE
                  SET names=EXCLUDED.names, external_id=EXCLUDED.external_id,
                      applied_tags=EXCLUDED.applied_tags, status=EXCLUDED.status,
                      provenance=EXCLUDED.provenance
                RETURNING *""",
            asset["asset_id"], asset.get("request_id"), asset["type"],
            asset.get("names") or {}, asset.get("external_id"),
            asset.get("owner_id"), asset.get("project_id"),
            asset.get("applied_tags") or {}, asset.get("mode", "simulated"),
            asset.get("status", "ACTIVE"), asset.get("sunset_date"),
            asset.get("provenance") or {})
        return _coerce(dict(row), ("names", "applied_tags", "provenance"))

    async def list_assets(self, *, owner_id: Optional[str] = None, project_id: Optional[str] = None,
                          status: Optional[str] = None, limit: int = 500) -> list[dict]:
        p = await self.pool()
        if p is None:
            rows = self._mem["assets"]
            if owner_id:
                rows = [a for a in rows if a.get("owner_id") == owner_id]
            if project_id:
                rows = [a for a in rows if a.get("project_id") == project_id]
            if status:
                rows = [a for a in rows if a.get("status") == status]
            return rows[:limit]
        clauses, args = [], []
        for col, val in (("owner_id", owner_id), ("project_id", project_id), ("status", status)):
            if val:
                args.append(val); clauses.append(f"{col}=${len(args)}")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(limit)
        rows = await p.fetch(
            f"SELECT * FROM {S}.assets{where} ORDER BY provisioned_at DESC LIMIT ${len(args)}", *args)
        return [_coerce(dict(r), ("names", "applied_tags", "provenance")) for r in rows]

    async def update_asset(self, asset_id: str, **fields: Any) -> Optional[dict]:
        if not fields:
            return None
        p = await self.pool()
        if p is None:
            a = next((x for x in self._mem["assets"] if x.get("asset_id") == asset_id), None)
            if a:
                a.update(fields)
            return a
        cols = list(fields.keys())
        sets = ", ".join(f"{c}=${i+2}" for i, c in enumerate(cols))
        vals = [fields[c] for c in cols]
        row = await p.fetchrow(
            f"UPDATE {S}.assets SET {sets} WHERE asset_id=$1 RETURNING *", asset_id, *vals)
        return _coerce(dict(row), ("names", "applied_tags", "provenance")) if row else None

    async def reassign_owner(self, *, new_owner_id: str, old_owner_id: Optional[str] = None,
                             project_id: Optional[str] = None) -> list[dict]:
        """Repoint assets to a new owner (by old owner or by project). Returns affected assets."""
        assets = await self.list_assets(owner_id=old_owner_id) if old_owner_id else \
            await self.list_assets(project_id=project_id)
        out = []
        for a in assets:
            updated = await self.update_asset(a["asset_id"], owner_id=new_owner_id)
            out.append(updated or a)
        return out


db = Database()
