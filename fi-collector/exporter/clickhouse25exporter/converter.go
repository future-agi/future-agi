// Package clickhouse25exporter converts OTLP spans (pdata) to the row
// shape required by the CH 25.3 `spans` table (see
// futureagi/tracer/services/clickhouse/v2/schema/002_spans_v2.sql).
//
// The converter is deliberately decoupled from the wire layer. The OTLP
// receiver hands us ptrace.Traces; we produce []map[string]any rows that
// the chwriter can serialise as JSONEachRow. Keeping the converter
// stand-alone makes it directly testable from `go test` without a CH
// dependency.
package clickhouse25exporter

import (
	"bytes"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/adapter"
	"github.com/future-agi/future-agi/fi-collector/pkg/curatedwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
	"github.com/google/uuid"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// OTLP attribute keys for the CH-derived `end_users` / `trace_sessions`
// dimensions (P3b step2). These mirror the EXACT keys the Django ingest path
// reads — span attributes `user.id` / `user.id.type` / `session.id`
// (futureagi/tracer/utils/otel.py SpanAttributes; the fi_instrumentation SDK
// `using_user` / `using_session` set them as SPAN attributes) and the resource
// attribute `project_type` (the SDK sets it on the Resource;
// futureagi/tracer/utils/trace_ingestion.py `_parse_otel_request` reads it).
// Django reads these literal keys directly — NOT via AttributeRegistry
// aliasing (that path only covers span_kind/model/provider/tokens) — so the
// collector must read these exact literals and nothing else.
const (
	attrUserID       = "user.id"
	attrUserIDType   = "user.id.type"
	attrUserIDHash   = "user.id.hash"  // SpanAttributes.USER_ID_HASH (otel.py L305)
	attrUserMetadata = "user.metadata" // SpanAttributes.USER_METADATA (otel.py L306)
	attrSessionID    = "session.id"
	resAttrProjectTy = "project_type"

	// projectTypeObserve is ProjectType.OBSERVE.value from the SDK
	// (fi_instrumentation fi_types.ProjectType). The Django end_user stamp is
	// gated on `project.trace_type == "observe"`; the collector mirrors that
	// using the resource `project_type` hint, which carries this value.
	projectTypeObserve = "observe"
)

// Convert walks an OTLP Traces payload and returns one map per span. Maps
// are designed for JSONEachRow encoding: all values are JSON-natural types
// (string, float64, bool, nil, slice, map). UUID-typed CH columns receive
// canonical 36-char strings; CH parses those on insert.
//
// Returns rows or an error. We DO NOT silently drop malformed spans; the
// caller decides retry/dead-letter policy.
//
// This is the span-only entry point (the OTLP receiver's hot path historically
// called it). It delegates to ConvertWithIdentities and discards the curated
// identities — keeping a stable signature for callers that only need the span
// rows.
func Convert(traces ptrace.Traces) ([]map[string]any, error) {
	rows, _, err := ConvertWithIdentities(traces)
	return rows, err
}

// ConvertWithIdentities is Convert plus the per-batch CURATED dimension
// identities (P3b step2 HALF 2). Walking the payload ONCE, it produces both the
// span rows AND the DISTINCT end_user / trace_session identities that
// curatedwriter mirrors into the CH `end_users` / `trace_sessions` RMTs.
//
// COHESION WITH THE SPAN-ID STAMP: the identity is computed by the SAME
// per-span function that stamps the span's end_user_id / trace_session_id
// columns (spanIdentity), so the curated row's end_user_id is BYTE-IDENTICAL to
// the span's end_user_id and its user_id_type is the SAME normalized value the
// span-id gate fed into detid.EndUserID — there is no second, drifting
// derivation. The id is computed once and reused for the column AND the row.
//
// The curatedwriter.Batch collapses duplicates WITHIN the batch (one row per
// distinct end_user_id / trace_session_id); cross-batch duplicates collapse on
// the CH side via ReplacingMergeTree(version), so only within-batch dedup is
// done here.
func ConvertWithIdentities(traces ptrace.Traces) ([]map[string]any, *curatedwriter.Batch, error) {
	rows := make([]map[string]any, 0, traces.SpanCount())
	ids := curatedwriter.NewBatch()

	rss := traces.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		rs := rss.At(i)
		resourceAttrs := flattenAttrs(rs.Resource().Attributes())
		serviceName := stringAttr(rs.Resource().Attributes(), "service.name", "")
		projectID := stringAttr(rs.Resource().Attributes(), "fi.project_id", "")
		orgID := stringAttr(rs.Resource().Attributes(), "fi.org_id", "")
		// `fi.semconv` lets producers tag which semantic convention they
		// emitted (openinference / openllmetry / langfuse / fi_native /
		// otel_genai). Used downstream for filtering and debugging.
		semconv := stringAttr(rs.Resource().Attributes(), "fi.semconv", "")
		// `project_type` ("observe" / "experiment") is the SDK-set resource
		// attribute (fi_instrumentation). Used ONLY to gate the deterministic
		// end_user_id stamp, mirroring Django's `project.trace_type ==
		// "observe"` gate (create_otel_span.py). Empty when the producer
		// didn't tag it (legacy SDKs) — see the gating note in spanToRow.
		projectType := stringAttr(rs.Resource().Attributes(), resAttrProjectTy, "")

		sss := rs.ScopeSpans()
		for j := 0; j < sss.Len(); j++ {
			scope := sss.At(j)
			ss := scope.Spans()
			for k := 0; k < ss.Len(); k++ {
				span := ss.At(k)
				row, identity, err := spanToRow(span, projectID, orgID, serviceName, semconv, projectType, resourceAttrs)
				if err != nil {
					return nil, nil, fmt.Errorf("span %s: %w", span.SpanID().String(), err)
				}
				rows = append(rows, row)
				identity.addTo(ids)
			}
		}
	}
	return rows, ids, nil
}

// spanToRow does the per-span conversion. Keeping this in one function
// makes it grep-friendly when a column is added: search for the column name
// and you find the one place it's populated.
//
// It also returns the span's CURATED dimension identity (spanIdentity) so the
// caller can collect the distinct end_users / trace_sessions for this batch.
// The identity's end_user_id / trace_session_id are the SAME values stamped
// onto the span row below — computed once, reused for both.
func spanToRow(
	span ptrace.Span,
	projectID, orgID, serviceName, semconv, projectType string,
	resourceAttrs map[string]any,
) (map[string]any, spanIdentity, error) {
	// Pre-allocate destination maps. Sizing is a heuristic — typical LLM
	// spans have 20-50 attrs, but customer-instrumented spans run smaller.
	attrsStr := make(map[string]string, 16)
	attrsNum := make(map[string]float64, 8)
	attrsBool := make(map[string]uint8, 4)
	overflow := make(map[string]any, 4)

	adapter.Split(span.Attributes(), attrsStr, attrsNum, attrsBool, overflow)
	hot := adapter.DeriveHotKeys(attrsStr, attrsNum)

	startNanos := span.StartTimestamp().AsTime()
	endNanos := span.EndTimestamp().AsTime()
	var endTime any
	var latencyMs int32
	if !endNanos.IsZero() {
		endTime = formatDateTime64(endNanos)
		// CH 25.3 stores latency_ms as Int32, capping at ~24.8 days.
		// Clamp defensively — a 25-day span is almost certainly corrupt
		// (forgot to call Finish()) and we'd rather log a max value than
		// overflow silently.
		ms := endNanos.Sub(startNanos).Milliseconds()
		if ms < 0 {
			ms = 0
		} else if ms > int64(^uint32(0)>>1) {
			ms = int64(^uint32(0) >> 1)
		}
		latencyMs = int32(ms)
	}

	// trace_id is the 16-byte OTel value, but PG `tracer_trace.id` is a UUID
	// and the migration backfill lands it as the 36-char DASHED uuid string in
	// `spans`/`traces`. We must match that exactly: live spans have to join the
	// backfilled history on trace_id, and spans.trace_name resolves the trace
	// name via toUUID(trace_id) against trace_dict (v2 schema 015) — toUUID()
	// only parses the dashed form. `span.TraceID().String()` emits 32-char hex
	// (no dashes), so we format the bytes as a dashed UUID instead.
	//
	// span_id / parent_span_id are 8-byte values stored as 16-char hex — that
	// already matches PG `tracer_observation_span.id`, so leave them as-is.
	traceID := traceIDToUUIDString(span.TraceID())
	spanID := strings.ToLower(span.SpanID().String())
	parentID := ""
	if !span.ParentSpanID().IsEmpty() {
		parentID = strings.ToLower(span.ParentSpanID().String())
	}

	// observation_type: prefer the OTel-GenAI `gen_ai.operation.name`
	// (chat / embedding / completion) when present; fall back to the
	// SDK-provided `openinference.span.kind` (LLM / CHAIN / TOOL); else
	// generic span kind. Matches the legacy adapter behaviour.
	observationType := strings.ToUpper(attrsStr["openinference.span.kind"])
	if observationType == "" {
		observationType = strings.ToUpper(attrsStr["fi.span.kind"])
	}
	if observationType == "" {
		observationType = "SPAN"
	}

	// Inputs/outputs: extracted from openinference convention if present.
	// `input.value` / `output.value` route to the overflow tier (per
	// adapter.overflowKeyPrefixes — they're often nested objects whose
	// shape varies row-to-row), so we lift them from `overflow` rather
	// than `attrsStr`. The hot string columns are populated with the
	// serialized form when the value is a plain string; nested values
	// stay in attributes_extra and dashboards query them from there.
	input := overflowAsString(overflow, "input.value")
	output := overflowAsString(overflow, "output.value")

	// CH-derived dimensions (P3b step2): stamp the DETERMINISTIC end_user_id /
	// trace_session_id so collector-written spans unify with the read-side
	// remap WITHOUT a hot-path PG get_or_create. Byte-exact mirror of the
	// Django stamp (futureagi/tracer/utils/create_otel_span.py +
	// services/clickhouse/v2/deterministic_id.py). Both columns are
	// Nullable(UUID) (schema 002_spans_v2.sql) — nil when the gating signal is
	// absent, so JSONEachRow lands a SQL NULL.
	//
	// P3b step2 HALF 2: spanIdentity computes the ids ONCE and also carries the
	// CURATED fields (user_id / normalized user_id_type / hash / metadata /
	// external_session_id) so curatedwriter can mirror the end_users /
	// trace_sessions RMTs keyed by the SAME id. The span columns below read the
	// ids straight off this identity — no second derivation.
	identity := newSpanIdentity(span.Attributes(), projectID, orgID, projectType)
	endUserID := identity.endUserColumn()
	traceSessionID := identity.sessionColumn()

	row := map[string]any{
		"project_id":        coalesceUUID(projectID),
		"observation_type":  observationType,
		"service_name":      serviceName,
		"start_time":        formatDateTime64(startNanos),
		"trace_id":          traceID,
		"id":                spanID,
		"parent_span_id":    parentID,
		"name":              span.Name(),
		"end_time":          endTime,
		"latency_ms":        latencyMs,
		"org_id":            nullableUUID(orgID),
		"end_user_id":       endUserID,      // Nullable(UUID); nil → SQL NULL
		"trace_session_id":  traceSessionID, // Nullable(UUID); nil → SQL NULL
		"status":            statusString(span.Status().Code()),
		"status_message":    span.Status().Message(),
		"model":             hot.Model,
		"provider":          hot.Provider,
		"gen_ai_system":     hot.GenAISystem,
		"gen_ai_operation":  hot.GenAIOperation,
		"operation_name":    hot.OperationName,
		"prompt_tokens":     hot.PromptTokens,
		"completion_tokens": hot.CompletionTokens,
		"total_tokens":      hot.TotalTokens,
		"cost":              hot.Cost,
		"attrs_string":      attrsStr,
		"attrs_number":      attrsNum,
		"attrs_bool":        attrsBool,
		"attributes_extra":  overflow,
		"resource_attrs":    resourceAttrs,
		"metadata":          map[string]any{}, // reserved; collectors may inject
		"input":             input,
		"output":            output,
		"tags":              "[]",
		"span_events":       spanEventsJSON(span.Events()),
		"semconv_source":    semconv,
		// _version comes from start_time nanos so newer spans always win
		// the ReplacingMergeTree dedup; matches the adapter.py convention.
		"_version":   uint64(startNanos.UnixNano()),
		"is_deleted": uint8(0),
	}
	return row, identity, nil
}

// spanIdentity is the per-span CURATED dimension identity (P3b step2 HALF 2):
// the deterministic end_user_id / trace_session_id AND the curated fields that
// back the CH `end_users` / `trace_sessions` RMT rows. It is computed ONCE per
// span by newSpanIdentity and reused for BOTH the span columns (endUserColumn /
// sessionColumn) and the curated row mapping (curatedwriter) — so the id on the
// row is byte-identical to the id on the span and the user_id_type on the row
// is the SAME normalized value that seeded the id's key.
//
// Each half is independently optional (gated separately, mirroring Django): a
// span may stamp an end_user but no session, or vice versa. hasEndUser /
// hasSession say which halves are populated; the *ID fields are the canonical
// lowercase-dashed deterministic ids when present.
type spanIdentity struct {
	hasEndUser bool
	endUserID  string // deterministic end_user_id (canonical UUID string)
	// Curated end_users fields — captured from the SAME gate that produced
	// endUserID, so they describe exactly that id.
	projectID  string // canonical lowercase-dashed (the id's key project)
	orgID      string // canonical lowercase-dashed (the id's key org)
	userID     string // raw user.id, AsString()-coerced (Python f-string str())
	userIDType string // NORMALIZED type or "" sentinel — SAME value seeding endUserID
	userIDHash string // user.id.hash pass-through (empty when absent)
	metadata   string // user.metadata as JSON text ('{}' when absent)

	hasSession        bool
	sessionID         string // deterministic trace_session_id (canonical UUID string)
	externalSessionID string // session.id value the id was computed from
	sessionProjectID  string // canonical project of the session id
}

// newSpanIdentity computes a span's CURATED identity: the deterministic ids +
// curated fields, gated EXACTLY like the Django stamp
// (futureagi/tracer/utils/create_otel_span.py + .../otel.py end_user block).
//
// END_USER gate (byte-exact mirror of create_otel_span.py L78-99 composed with
// otel.py L846-854):
//
//	if attributes.get(USER_ID):                              # truthy user.id
//	    end_user = {"user_id": ..., "user_id_type": get_user_id_type(...),
//	                "user_id_hash": ..., "metadata": ...}
//	if end_user["user_id"] is not None and project.trace_type == "observe":
//	    eu_id = deterministic_end_user_id(project.id, org_id, user_id, user_id_type)
//
//	  - project_type must be "observe" (mirrors `project.trace_type ==
//	    "observe"`). The collector has no PG, so it uses the SDK-set resource
//	    `project_type` hint. fi_instrumentation always sets it; a non-FI
//	    producer that omits it conservatively does NOT stamp.
//	  - project_id and org_id must parse (the converter's coalesceUUID random
//	    fallback must NOT seed an id; we check the raw projectID/orgID here).
//	  - user.id must be present AND truthy (Python's `if attributes.get(USER_ID):`
//	    — empty-string / numeric-zero are falsy and produce no end_user).
//
// SESSION gate (mirror of create_otel_span.py L170 / otel.py L820):
//
//	session_name = attributes.get(SESSION_ID)
//	if session_name is not None:
//	    ts_id = deterministic_trace_session_id(project.id, session_name)
//
//	  - PRESENT session.id (even empty-string "") stamps; ABSENT does not — so
//	    presence (comma-ok), NOT truthiness. No project_type gate (Django stamps
//	    the session for any project type; only end_user is observe-gated).
//
// The user.id / session.id values are coerced with AsString() so a numeric id
// matches Python's f-string str() (and never loses precision via the float64
// attrs_number tier). user.id.type is normalized + sentinel-mapped exactly like
// Python — see normalizeUserIDType, the SINGLE normalization shared between the
// id key and the curated row.
func newSpanIdentity(attrs pcommon.Map, projectID, orgID, projectType string) spanIdentity {
	var id spanIdentity

	// ── end_user half ──────────────────────────────────────────────────────
	// Canonicalize project_id / org_id to the lowercase-dashed form Python's
	// str(uuid.UUID) / PG's UUID text produced when the FROZEN ids were
	// derived. fi.project_id / fi.org_id are already that shape in practice
	// (the gateway sets them from the PG UUID), so this is a no-op there — but
	// it makes the key byte-match the contract even if a producer sent an
	// uppercase / braced form, and refuses to stamp a malformed-key id.
	if projectType == projectTypeObserve {
		if pid, ok := canonicalUUID(projectID); ok {
			if oid, ok := canonicalUUID(orgID); ok {
				if v, ok := attrs.Get(attrUserID); ok && isTruthyAttr(v) {
					userID := v.AsString()
					userIDType := normalizeUserIDType(attrs)
					id.hasEndUser = true
					id.endUserID = detid.EndUserID(pid, oid, userID, userIDType).String()
					id.projectID = pid
					id.orgID = oid
					id.userID = userID
					id.userIDType = userIDType
					id.userIDHash = userIDHashAttr(attrs)
					id.metadata = userMetadataText(attrs)
				}
			}
		}
	}

	// ── session half (independent gate; no project_type requirement) ────────
	if pid, ok := canonicalUUID(projectID); ok {
		if v, ok := attrs.Get(attrSessionID); ok {
			name := v.AsString()
			id.hasSession = true
			id.sessionID = detid.TraceSessionID(pid, name).String()
			id.externalSessionID = name
			id.sessionProjectID = pid
		}
	}

	return id
}

// endUserColumn returns the value for the span's Nullable(UUID) `end_user_id`
// column: the deterministic id string when an end_user was stamped, else nil
// (→ SQL NULL via JSONEachRow). Reads the id computed in newSpanIdentity — the
// column and the curated row share one derivation.
func (id spanIdentity) endUserColumn() any {
	if !id.hasEndUser {
		return nil
	}
	return id.endUserID
}

// sessionColumn returns the value for the span's Nullable(UUID)
// `trace_session_id` column: the deterministic id string when a session was
// stamped, else nil (→ SQL NULL).
func (id spanIdentity) sessionColumn() any {
	if !id.hasSession {
		return nil
	}
	return id.sessionID
}

// addTo pushes this span's stamped curated identities into the per-batch
// collector. Each half is added only when it was stamped (same gate as the
// span columns), so a row contributes an end_user iff its span got a non-NULL
// end_user_id, and a session iff its span got a non-NULL trace_session_id — the
// EXACT gate the task requires. The Batch dedups within the batch by id.
//
// The curatedwriter.EndUser / Session are filled straight from the identity's
// already-computed fields: EndUserID is the SAME id stamped on the span column,
// and UserIDType is the SAME normalized value (the converter's
// normalizeUserIDType) that seeded that id — there is no re-derivation here.
func (id spanIdentity) addTo(b *curatedwriter.Batch) {
	if id.hasEndUser {
		b.AddEndUser(curatedwriter.EndUser{
			ProjectID:      id.projectID,
			EndUserID:      id.endUserID,
			OrganizationID: id.orgID,
			UserID:         id.userID,
			UserIDType:     id.userIDType,
			UserIDHash:     id.userIDHash,
			Metadata:       id.metadata,
		})
	}
	if id.hasSession {
		b.AddSession(curatedwriter.Session{
			ProjectID:         id.sessionProjectID,
			TraceSessionID:    id.sessionID,
			ExternalSessionID: id.externalSessionID,
		})
	}
}

// userIDHashAttr reads `user.id.hash` (SpanAttributes.USER_ID_HASH) as a
// pass-through String, coercing absent to the empty string (the CH
// `end_users.user_id_hash` column is a non-null String DEFAULT empty). Mirrors
// Django otel.py L852 `attributes.get(USER_ID_HASH)` composed with
// curated_writer's `or ""`.
func userIDHashAttr(attrs pcommon.Map) string {
	v, ok := attrs.Get(attrUserIDHash)
	if !ok {
		return ""
	}
	return v.AsString()
}

// userMetadataText reads `user.metadata` (SpanAttributes.USER_METADATA) and
// renders it as the JSON text the CH `end_users.metadata` String column holds.
// Mirrors Django otel.py L853 `attributes.get(USER_METADATA, {})` composed with
// curated_writer._metadata_to_text: absent → "{}", a string is trusted as-is
// (the SDK serializes the dict before putting it on the span attribute), any
// other value is JSON-encoded.
func userMetadataText(attrs pcommon.Map) string {
	v, ok := attrs.Get(attrUserMetadata)
	if !ok {
		return "{}"
	}
	if v.Type() == pcommon.ValueTypeStr {
		return v.Str()
	}
	// Non-string (map/slice/number/bool) → JSON. AsRaw yields a Go-native
	// value; encode with HTML-escaping OFF so `<`/`>`/`&` stay literal —
	// byte-parity with Python's json.dumps(ensure_ascii=False) in
	// curated_writer._metadata_to_text and with chwriter's own encoder.
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v.AsRaw()); err != nil {
		return "{}"
	}
	// json.Encoder appends a trailing newline; trim it for a clean JSON scalar.
	return strings.TrimRight(buf.String(), "\n")
}

// canonicalUUID parses s and re-emits the canonical lowercase-dashed UUID
// string — the exact form Python's str(uuid.UUID) and PG's UUID text column
// produced when the frozen deterministic ids were derived. Returns ok=false
// for an empty or unparseable value so the caller declines to stamp (a NULL
// dimension is backfillable; a malformed-key id is corruption). A
// already-canonical input round-trips unchanged.
func canonicalUUID(s string) (string, bool) {
	if s == "" {
		return "", false
	}
	u, err := uuid.Parse(s)
	if err != nil {
		return "", false
	}
	return u.String(), true
}

// normalizeUserIDType mirrors futureagi/tracer/utils/otel.py get_user_id_type
// COMPOSED WITH deterministic_id.py's `user_id_type or ""` sentinel, returning
// the EXACT token that goes into the end_user_id key string:
//
//	get_user_id_type(None)        -> None  ; then `None or ""`  -> ""
//	get_user_id_type("")          -> "custom" (""!=None → case _) ; stays "custom"
//	get_user_id_type("email")     -> "email"
//	get_user_id_type("phone")     -> "phone"
//	get_user_id_type("uuid")      -> "uuid"
//	get_user_id_type("anything")  -> "custom"
//
// The ABSENT-vs-PRESENT-empty distinction is load-bearing: an ABSENT
// user.id.type → "" sentinel (consolidates with NULL-type history), but a
// PRESENT empty-string user.id.type → "custom". We therefore branch on the
// comma-ok presence flag, never on Go's zero-value "".
func normalizeUserIDType(attrs pcommon.Map) string {
	v, ok := attrs.Get(attrUserIDType)
	if !ok {
		// Absent → Python None → `None or ""` sentinel.
		return ""
	}
	raw := v.AsString()
	switch raw {
	case "email":
		return "email"
	case "phone":
		return "phone"
	case "uuid":
		return "uuid"
	default:
		// Present but not a known type (INCLUDING the empty string) → "custom".
		// "custom" is truthy so Python's `or ""` leaves it unchanged.
		return "custom"
	}
}

// isTruthyAttr mirrors Python truthiness of `attributes.get(USER_ID)` for the
// end_user extraction gate (`if attributes.get(USER_ID):`). A string is truthy
// iff non-empty; a number iff non-zero; a bool iff true; everything else
// (maps/slices/bytes) iff its string form is non-empty. Empty / zero / unset
// values are falsy and must NOT seed an end_user.
func isTruthyAttr(v pcommon.Value) bool {
	switch v.Type() {
	case pcommon.ValueTypeStr:
		return v.Str() != ""
	case pcommon.ValueTypeInt:
		return v.Int() != 0
	case pcommon.ValueTypeDouble:
		return v.Double() != 0
	case pcommon.ValueTypeBool:
		return v.Bool()
	case pcommon.ValueTypeEmpty:
		return false
	default:
		return v.AsString() != ""
	}
}

// flattenAttrs converts a pcommon.Map into a plain map[string]any. Resource
// attribute values are simple — strings, ints, bools — so this isn't the
// hot path that adapter.Split is.
func flattenAttrs(m pcommon.Map) map[string]any {
	out := make(map[string]any, m.Len())
	m.Range(func(k string, v pcommon.Value) bool {
		switch v.Type() {
		case pcommon.ValueTypeStr:
			out[k] = v.Str()
		case pcommon.ValueTypeBool:
			out[k] = v.Bool()
		case pcommon.ValueTypeInt:
			out[k] = v.Int()
		case pcommon.ValueTypeDouble:
			out[k] = v.Double()
		default:
			out[k] = v.AsString()
		}
		return true
	})
	return out
}

func stringAttr(m pcommon.Map, key, def string) string {
	v, ok := m.Get(key)
	if !ok {
		return def
	}
	if v.Type() == pcommon.ValueTypeStr {
		return v.Str()
	}
	return v.AsString()
}

func stringFromMap(m map[string]string, key string) string {
	if v, ok := m[key]; ok {
		return v
	}
	return ""
}

// overflowAsString lifts the value at `key` from the overflow map and
// returns its string form. Plain strings pass through; nested objects get
// JSON-encoded so the hot column still holds something useful. Missing
// key → empty string (CH `input` is `String DEFAULT ”`).
func overflowAsString(overflow map[string]any, key string) string {
	v, ok := overflow[key]
	if !ok || v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	b, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	return string(b)
}

// statusString maps OTel StatusCode → CH `status` LowCardinality strings.
// Keeps the existing PG enum values so dashboards continue to work.
func statusString(c ptrace.StatusCode) string {
	switch c {
	case ptrace.StatusCodeOk:
		return "OK"
	case ptrace.StatusCodeError:
		return "ERROR"
	default:
		return "UNSET"
	}
}

// spanEventsJSON serialises events as a JSON array. We hand back a JSON
// string (not []any) because the CH `span_events String` column stores it
// verbatim — saves a re-serialise on the writer side.
func spanEventsJSON(events ptrace.SpanEventSlice) string {
	if events.Len() == 0 {
		return "[]"
	}
	var b strings.Builder
	b.WriteByte('[')
	for i := 0; i < events.Len(); i++ {
		if i > 0 {
			b.WriteByte(',')
		}
		ev := events.At(i)
		b.WriteByte('{')
		fmt.Fprintf(&b, `"name":%q,"timestamp":%q`,
			ev.Name(), formatDateTime64(ev.Timestamp().AsTime()))
		b.WriteByte('}')
	}
	b.WriteByte(']')
	return b.String()
}

// formatDateTime64 emits CH's DateTime64(6) text form: "YYYY-MM-DD HH:MM:SS.ffffff".
// JSONEachRow accepts this verbatim for DateTime64 columns; we avoid the
// nanosecond suffix (CH rejects 9-digit fractional seconds for (6)).
func formatDateTime64(t time.Time) string {
	return t.UTC().Format("2006-01-02 15:04:05.000000")
}

// coalesceUUID returns a valid UUID. If `s` is empty we emit a random one
// because `project_id UUID` is non-nullable. In production every span must
// have a tagged project; this fallback exists for SDK-misconfigured cases
// so the writer doesn't drop the row entirely.
func coalesceUUID(s string) string {
	if s == "" {
		return randomUUID()
	}
	return s
}

// nullableUUID returns nil when empty so CH's JSONEachRow parser handles
// the Nullable column correctly. (An empty string would fail parsing.)
func nullableUUID(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// traceIDToUUIDString formats an OTel 16-byte trace id as the canonical
// 36-char dashed UUID (8-4-4-4-12), matching PG `tracer_trace.id` and the
// migration backfill. Uses the same byte-formatting idiom as randomUUID.
// An empty/zero trace id yields the all-zero UUID string; the caller's
// upstream validation rejects spans without a trace before we get here.
func traceIDToUUIDString(t pcommon.TraceID) string {
	b := t // pcommon.TraceID is [16]byte
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

func randomUUID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40 // v4
	b[8] = (b[8] & 0x3f) | 0x80 // RFC 4122
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}
