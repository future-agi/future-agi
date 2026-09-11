// Package curatedwriter is the Go MIRROR of
// futureagi/tracer/services/clickhouse/v2/curated_writer.py — the collector-side
// dual-write of the CURATED dimensions `end_users` / `trace_sessions` into
// ClickHouse 25.3 (CH-derived-dimensions, DESIGN §4 / §5).
//
// WHY THIS EXISTS. The collector is the canonical CH-span writer, but Django's
// curated_writer only feeds `end_users` / `trace_sessions` on PG-ingest traffic.
// So a user / session that arrives ONLY through the collector is MISSING from
// those RMTs, and its `end_users_dict` / `trace_sessions_dict` lookup (live since
// P3c) returns NULL labels (user_id, user_id_type, external_session_id). This
// package closes that gap: alongside the span batch the collector emits a
// batched `end_users` / `trace_sessions` insert (DESIGN §4.2 / §5: "the
// collector emits a batched end_users insert alongside the span batch").
//
// CONTRACT WITH THE SPAN-ID STAMP. The EndUser.EndUserID / Session.TraceSessionID
// carried here are the SAME deterministic ids the converter stamps onto the span
// (pkg/detid via the converter's spanIdentity) — the converter computes the id
// ONCE and feeds it BOTH the span column and this row, so the curated row is
// keyed byte-identically to the span. EndUser.UserIDType is the SAME normalized
// value (the converter's normalizeUserIDType) that seeded the id's key.
//
// BEST-EFFORT, mirroring the Python posture. A curated-RMT write failure must
// NEVER drop the span batch: Write swallows every error (logs, returns it for
// the caller's awareness) and the caller ignores it. The periodic Django
// backfill (ch25_backfill_curated_dimensions) reconciles any gap; PG / the span
// store remain the systems of record.
//
// VERSIONING. Both targets are ReplacingMergeTree(version) keyed on the entity
// id, and `version` is a CH DateTime64(6,'UTC') (schema 017/018) — NOT the
// integer-ns `_version` the spans use. So, exactly like the Python live path, we
// stamp `version = now()` (UTC) per write: a later collector mirror always
// out-versions an earlier one, and it is always >= any historical `updated_at`
// a backfill re-run carries, so the backfill can never clobber a fresher live
// row (curated_writer.py L42-47). is_deleted is always 0 — a freshly-ingested
// entity is never soft-deleted (curated_writer.py L269).
//
// CROSS-BATCH DUPLICATES need no handling here: RMT collapses them by version on
// the CH side. Only WITHIN-batch dedup is done (see Batch) so one insert carries
// one row per distinct id.
package curatedwriter

import (
	"context"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
)

// Table names — the curated-RMT insert targets (schema 017/018). JSONEachRow
// maps by column name, so these plus the column lists below are the full wire
// contract.
const (
	tableEndUsers      = "end_users"
	tableTraceSessions = "trace_sessions"
	tableTraces = "traces"
)

// COLUMN CONTRACT. JSONEachRow maps by NAME, so the row maps built in
// endUserRow / sessionRow ARE the wire contract — there is no positional column
// list (unlike the Python curated_writer._END_USER_COLUMNS / _TRACE_SESSION_COLUMNS,
// which clickhouse-connect needs). For reference, the column sets they must
// match (schema 017_end_users.sql L44-61 / 018_trace_sessions.sql L38-51):
//
//	end_users:      project_id  end_user_id  organization_id  user_id
//	                user_id_type  user_id_hash  metadata  first_seen  version
//	                is_deleted
//	trace_sessions: project_id  trace_session_id  external_session_id
//	                first_seen  version  is_deleted

// EndUser is one curated `end_users` identity to mirror: the deterministic
// EndUserID + the SDK-sourced curated fields the collector already parsed off
// the span (the converter's spanIdentity). Mirrors the Python CuratedEndUser.
//
// EndUserID is the deterministic id (pkg/detid) — the SAME value stamped on the
// span's end_user_id column. UserIDType is the normalized value (or "" sentinel)
// that seeded EndUserID's key. ProjectID / OrganizationID are the canonical
// lowercase-dashed UUID strings the id was keyed on. UserIDHash / Metadata are
// already coerced to their non-null String forms by the converter (empty / "{}").
type EndUser struct {
	ProjectID      string
	EndUserID      string
	OrganizationID string
	UserID         string
	UserIDType     string // "" sentinel == NULL in the RMT (see nullableType)
	UserIDHash     string
	Metadata       string
}

// Session is one curated `trace_sessions` identity to mirror: the deterministic
// TraceSessionID + the external session id it was computed from. Mirrors the
// Python CuratedSession. There is NO organization_id — the PG `trace_session`
// natural key is (project, name), so the session identity carries no org
// (schema 018 / DESIGN §5).
type Session struct {
	ProjectID         string
	TraceSessionID    string
	ExternalSessionID string
}


type Trace struct {
	ID        string
	ProjectID string
	Name      string
	Input     string
	Output    string
	SessionID string
	CreatedAt string
	Version uint64
}

// Batch collects the DISTINCT curated identities seen across ONE span batch,
// deduping WITHIN the batch (one row per distinct EndUserID / TraceSessionID).
// Cross-batch duplicates are left to the RMT's version merge — see the package
// doc — so this only needs the within-batch set.
//
// Not safe for concurrent use; the converter fills one Batch per Convert call
// on a single goroutine.
type Batch struct {
	endUsers []EndUser
	sessions []Session
	traces   []Trace
	euSeen   map[string]struct{} // dedup set keyed by EndUserID
	sessSeen map[string]struct{} // dedup set keyed by TraceSessionID
	trcSeen  map[string]struct{} // dedup set keyed by trace ID
}

// NewBatch returns an empty Batch ready to collect identities.
func NewBatch() *Batch {
	return &Batch{
		euSeen:   make(map[string]struct{}),
		sessSeen: make(map[string]struct{}),
		trcSeen:  make(map[string]struct{}),
	}
}

// AddEndUser records a distinct end_user identity. The FIRST occurrence of an
// EndUserID wins; later duplicates in the same batch are dropped (the RMT would
// collapse them anyway, but emitting one row keeps the insert minimal and makes
// the dedup invariant observable — N spans across M users → exactly M rows).
func (b *Batch) AddEndUser(eu EndUser) {
	if eu.EndUserID == "" {
		return
	}
	if _, dup := b.euSeen[eu.EndUserID]; dup {
		return
	}
	b.euSeen[eu.EndUserID] = struct{}{}
	b.endUsers = append(b.endUsers, eu)
}

// AddSession records a distinct trace_session identity (first-occurrence wins,
// same rationale as AddEndUser).
func (b *Batch) AddSession(s Session) {
	if s.TraceSessionID == "" {
		return
	}
	if _, dup := b.sessSeen[s.TraceSessionID]; dup {
		return
	}
	b.sessSeen[s.TraceSessionID] = struct{}{}
	b.sessions = append(b.sessions, s)
}

// AddTrace records a distinct trace (first-occurrence wins). One entry per
// trace_id within a batch; cross-batch duplicates collapse on the RMT by Version.
func (b *Batch) AddTrace(t Trace) {
	if t.ID == "" {
		return
	}
	if _, dup := b.trcSeen[t.ID]; dup {
		return
	}
	b.trcSeen[t.ID] = struct{}{}
	b.traces = append(b.traces, t)
}

// Merge folds another batch's distinct identities into this one (deduping
// across both by id). Used by the server to accumulate ALL payloads received
// between flushes into ONE drain-scoped batch, so each drain emits at most one
// end_users + one trace_sessions + one traces insert — bounding the best-effort
// latency and avoiding many tiny RMT parts (CH "too many parts") at 100K
// spans/s. A nil `other` is a no-op.
func (b *Batch) Merge(other *Batch) {
	if other == nil {
		return
	}
	for _, eu := range other.endUsers {
		b.AddEndUser(eu)
	}
	for _, s := range other.sessions {
		b.AddSession(s)
	}
	for _, t := range other.traces {
		b.AddTrace(t)
	}
}

// EndUsers returns the distinct end_user identities collected (insertion order).
func (b *Batch) EndUsers() []EndUser { return b.endUsers }

// Sessions returns the distinct session identities collected (insertion order).
func (b *Batch) Sessions() []Session { return b.sessions }

// Traces returns the distinct traces collected (insertion order).
func (b *Batch) Traces() []Trace { return b.traces }

// Empty reports whether the batch collected no curated identities at all — the
// caller skips the writer entirely in that (common) case.
func (b *Batch) Empty() bool {
	return len(b.endUsers) == 0 && len(b.sessions) == 0 && len(b.traces) == 0
}

// Writer mirrors a Batch's curated identities into the CH `end_users` /
// `trace_sessions` RMTs over the shared chwriter (one batched JSONEachRow insert
// per non-empty target). It writes via chwriter.InsertBestEffort — a SINGLE POST,
// NO retry, NO dead-letter — so a curated-RMT outage can neither stall the span
// flush loop nor pollute the span dead-letter (see InsertBestEffort's doc). The
// curated rows go to their OWN tables, never the pinned span table.
type Writer struct {
	w *chwriter.Writer
}

// New builds a curated Writer over an existing chwriter.Writer. The chwriter's
// pinned Table (spans) is irrelevant here — Write targets end_users /
// trace_sessions explicitly via InsertBestEffort.
func New(w *chwriter.Writer) *Writer {
	return &Writer{w: w}
}

// Write mirrors the batch's distinct curated identities into CH. BEST-EFFORT:
// it returns the first insert error (for the caller's awareness / metrics) but
// the CALLER MUST NOT let that error drop the span batch — the span insert is a
// separate, already-completed operation. A nil batch or an empty one is a no-op.
//
// `now` is the version/first_seen timestamp (DateTime64(6,'UTC')); the caller
// passes time.Now().UTC() in production. Both targets get the same `now` so a
// row's version and first_seen agree, matching the Python live path
// (curated_writer.py _curated_*_to_row, where one `now` fills both).
//
// Both inserts are attempted even if the first fails, so an end_users hiccup
// doesn't suppress the trace_sessions write (and the returned error is the
// first one seen — enough to flag "something failed" without masking either).
func (cw *Writer) Write(ctx context.Context, b *Batch, now time.Time) error {
	if b == nil || b.Empty() {
		return nil
	}
	var firstErr error
	if eus := b.EndUsers(); len(eus) > 0 {
		rows := make([]map[string]any, 0, len(eus))
		for _, eu := range eus {
			rows = append(rows, endUserRow(eu, now))
		}
		if err := cw.w.InsertBestEffort(ctx, tableEndUsers, rows); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	if ss := b.Sessions(); len(ss) > 0 {
		rows := make([]map[string]any, 0, len(ss))
		for _, s := range ss {
			rows = append(rows, sessionRow(s, now))
		}
		if err := cw.w.InsertBestEffort(ctx, tableTraceSessions, rows); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	if ts := b.Traces(); len(ts) > 0 {
		rows := make([]map[string]any, 0, len(ts))
		for _, t := range ts {
			rows = append(rows, traceRow(t, now))
		}
		if err := cw.w.InsertBestEffort(ctx, tableTraces, rows); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}

// endUserRow maps an EndUser → a CH `end_users` JSONEachRow row, matching schema
// 017 column-for-column (the Python curated_writer._curated_end_user_to_row):
//
//   - project_id / end_user_id / organization_id : UUID columns, canonical
//     lowercase-dashed strings (CH parses them on insert).
//   - user_id           : non-null String.
//   - user_id_type      : LowCardinality(Nullable(String)). The "" sentinel
//     means "no type" and MUST land as SQL NULL (the dict's single source of
//     truth round-trips NULL for the ~85% untyped rows — schema 017 L42-43 /
//     L89-95), so "" → nil here; a real type ("email"/.../"custom") passes
//     through.
//   - user_id_hash      : non-null String DEFAULT empty (already empty when absent).
//   - metadata          : non-null String DEFAULT "{}" (already "{}" / JSON).
//   - first_seen        : DateTime64(6,'UTC') — `now` (the entity's first
//     observed activity in this stream; no PG source post-flip).
//   - version           : DateTime64(6,'UTC') — `now` (latest-wins; see pkg doc).
//   - is_deleted        : UInt8 0 (freshly ingested → never soft-deleted).
func endUserRow(eu EndUser, now time.Time) map[string]any {
	return map[string]any{
		"project_id":      eu.ProjectID,
		"end_user_id":     eu.EndUserID,
		"organization_id": eu.OrganizationID,
		"user_id":         eu.UserID,
		"user_id_type":    nullableType(eu.UserIDType),
		"user_id_hash":    eu.UserIDHash,
		"metadata":        eu.Metadata,
		"first_seen":      formatDateTime64(now),
		"version":         formatDateTime64(now),
		"is_deleted":      uint8(0),
	}
}

// sessionRow maps a Session → a CH `trace_sessions` JSONEachRow row, matching
// schema 018 column-for-column (the Python _curated_session_to_row):
//
//	project_id  trace_session_id : UUID columns (canonical strings).
//	external_session_id          : non-null String DEFAULT empty (== session name).
//	first_seen / version         : DateTime64(6,'UTC') — `now`.
//	is_deleted                   : UInt8 0.
func sessionRow(s Session, now time.Time) map[string]any {
	return map[string]any{
		"project_id":          s.ProjectID,
		"trace_session_id":    s.TraceSessionID,
		"external_session_id": s.ExternalSessionID,
		"first_seen":          formatDateTime64(now),
		"version":             formatDateTime64(now),
		"is_deleted":          uint8(0),
	}
}

// traceRow maps a Trace → a CH `traces` JSONEachRow row (schema 015, the app's
// trace_writer._trace_to_row column set). JSONEachRow maps by name, so columns
// OMITTED here take their schema DEFAULT — but created_at has NO default and MUST
// be provided. is_deleted=0 (an app soft-delete writes its own higher-_version
// tombstone). name/session_id are OMITTED when empty so the Nullable columns are
// SQL NULL (round-tripped by dictGet); tags / metadata / error /
// error_analysis_status / project_version_id are left to their defaults — the
// collector has no PG source and the app mirror fills them if it owns the trace.
func traceRow(t Trace, now time.Time) map[string]any {
	row := map[string]any{
		"id":         t.ID,
		"project_id": t.ProjectID,
		"input":      t.Input,
		"output":     t.Output,
		"created_at": t.CreatedAt,
		"updated_at": formatDateTime64(now),
		"is_deleted": uint8(0),
		"_version":   t.Version,
	}
	if t.Name != "" {
		row["name"] = t.Name
	}
	if t.SessionID != "" {
		row["session_id"] = t.SessionID
	}
	return row
}

// nullableType maps the user_id_type sentinel to its CH wire value: the ""
// sentinel (absent/NULL-typed identity) → nil → SQL NULL in the
// LowCardinality(Nullable(String)) column; any real normalized type passes
// through unchanged. This is the collector half of the §11.1a NULL-to-empty
// sentinel: the id KEY uses "" (so NULL-typed and ""-typed identities
// consolidate onto one id), but the stored COLUMN must be NULL so the dict
// round-trips it, matching the Python row mapper (which keeps user_id_type=None
// as-is).
func nullableType(t string) any {
	if t == "" {
		return nil
	}
	return t
}

// formatDateTime64 emits CH's DateTime64(6) text form ("YYYY-MM-DD HH:MM:SS.ffffff")
// in UTC — the shape JSONEachRow accepts for a DateTime64(6,'UTC') column.
// Mirrors the converter's formatDateTime64 (kept local so curatedwriter doesn't
// import the exporter package, preserving the converter→curatedwriter dep
// direction).
func formatDateTime64(t time.Time) string {
	return t.UTC().Format("2006-01-02 15:04:05.000000")
}
