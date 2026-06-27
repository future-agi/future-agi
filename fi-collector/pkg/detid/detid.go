// Package detid is the GO MIRROR of
// futureagi/tracer/services/clickhouse/v2/deterministic_id.py — the pure
// UUIDv5 surrogate-id functions for the CURATED `end_users` / `trace_sessions`
// dimensions (CH-derived-dimensions, DESIGN §3).
//
// THE BYTES ARE FROZEN. These functions MUST produce the SAME id, byte for
// byte, as the Python module for every input — the historical remap
// (`ch25_build_id_remap`) and BOTH ingest paths (Django `create_otel_span` /
// this Go collector) only consolidate onto one id if the namespace seeds and
// the key string are byte-identical on every side. So the namespace seeds
// ("futureagi.enduser.v1" / "futureagi.session.v1"), the key layout
// (`{project}|{org}|{user}|{type}` / `{project}|{name}`), the `|` separator,
// the field order, and the NULL→"" sentinel are LOAD-BEARING. Do NOT change
// any of them without a lockstep re-key of every historical row.
//
// PARITY MECHANICS (why Go == Python here):
//   - google/uuid's NewSHA1(space, name) is RFC-4122 §4.3 v5: SHA-1 over
//     space[:]++name, first 16 bytes, version nibble 5, RFC-4122 variant —
//     exactly what CPython's uuid.uuid5 computes. uuid.NameSpaceDNS is the
//     same 6ba7b810-… literal as Python's NAMESPACE_DNS.
//   - The key string is built from the canonical lowercase-dashed UUID form
//     of project_id/org_id (matching Python's str(uuid.UUID) / PG's text
//     form) and the raw user_id / name string, UTF-8 — the same bytes the
//     Python f-string produces.
//
// THE NULL SENTINEL. user_id_type is NULL/absent on ~85% of rows. The key
// renders an absent/empty type as the "" sentinel (NOT the literal "None"),
// so a NULL row and a later ""-typed row for the same (project, org, user)
// collapse onto ONE id (the 879→544 consolidation). Mirrors Python's
// `user_id_type or ""`. See EndUserID below.
//
// NO I/O. Pure functions — safe on the ingest hot path.
package detid

import "github.com/google/uuid"

// Namespace seeds — the frozen contract. Mirrors deterministic_id.py L48-49.
const (
	nsEndUserSeed = "futureagi.enduser.v1"
	nsSessionSeed = "futureagi.session.v1"
)

// Namespace UUIDs, seeded off NAMESPACE_DNS so they are themselves stable
// reproducible v5 values (no hand-picked literal to transcribe). Pinned
// golden values (asserted in detid_test.go — these must never drift):
//
//	NSEndUser = 97daafcc-ae7b-5a44-a76b-c85e63059e1c
//	NSSession = 1c4977df-2af9-5330-b34f-7969ffdabf25
var (
	// NSEndUser == Python uuid5(NAMESPACE_DNS, "futureagi.enduser.v1").
	NSEndUser = uuid.NewSHA1(uuid.NameSpaceDNS, []byte(nsEndUserSeed))
	// NSSession == Python uuid5(NAMESPACE_DNS, "futureagi.session.v1").
	NSSession = uuid.NewSHA1(uuid.NameSpaceDNS, []byte(nsSessionSeed))
)

// EndUserID is the deterministic end_user_id for an EndUser identity
// (DESIGN §3 / §3.1). Mirrors deterministic_id.deterministic_end_user_id.
//
// Natural key: (projectID, organizationID, userID, userIDType). projectID /
// organizationID MUST already be the canonical lowercase-dashed UUID string
// (the converter passes the SDK/gateway-supplied fi.project_id / fi.org_id,
// which are exactly that). userID is the raw user.id value coerced to its
// string form (the same coercion Python's f-string applies). userIDType is
// the NORMALIZED type (email/phone/uuid/custom) or "" — see the sentinel
// note in the package doc; an empty userIDType renders as "" (NOT "None"),
// which is how a NULL-type and an ""-type identity consolidate.
//
// Pure: no I/O.
func EndUserID(projectID, organizationID, userID, userIDType string) uuid.UUID {
	// userIDType is already the "" sentinel for the NULL/absent case (the
	// caller applies Python's `user_id_type or ""` semantics — see
	// converter.go). Keep the key construction byte-identical to the
	// Python f-string: f"{project}|{org}|{user}|{type}".
	key := projectID + "|" + organizationID + "|" + userID + "|" + userIDType
	return uuid.NewSHA1(NSEndUser, []byte(key))
}

// TraceSessionID is the deterministic trace_session_id for a TraceSession
// identity (DESIGN §3). Mirrors deterministic_id.deterministic_trace_session_id.
//
// Natural key: (projectID, name) — name is the external session id
// (session.id span attribute). projectID MUST be the canonical
// lowercase-dashed UUID string. Key: f"{project}|{name}".
//
// Note the asymmetry with EndUserID: Python applies NO sentinel here, so a
// NULL name would render as the literal "None". The Go collector never
// reaches this with an absent name — the caller gates on session-name
// presence (mirroring Python's `session_name is not None`) and only stamps
// trace_session_id when a name is present, so `name` is always the real
// external session id here.
//
// Pure: no I/O.
func TraceSessionID(projectID, name string) uuid.UUID {
	key := projectID + "|" + name
	return uuid.NewSHA1(NSSession, []byte(key))
}
