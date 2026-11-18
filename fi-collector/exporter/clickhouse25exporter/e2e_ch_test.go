// Package clickhouse25exporter — integration tests against a real ClickHouse 25.3 instance.
//
// Unlike converter_test.go (which validates row shape in-memory without a CH
// dependency), these tests write rows through the full pipeline (converter →
// chwriter → CH HTTP) and query the results back to verify the end-to-end
// contract.
//
// Prerequisites:
//   - CH 25.3 running at the address in CH_TEST_HOST (default: localhost:18123)
//   - Schema 002–014 applied (run-e2e.sh handles this via Django migrate)
//   - Database: test_tfc (or override with CH_TEST_DATABASE)
//
// Run:
//
//	CH_TEST_HOST=localhost:18123 CH_TEST_DATABASE=test_tfc go test -tags integration -run TestE2E ./exporter/clickhouse25exporter/
//
//go:build integration

package clickhouse25exporter

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/curatedwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
)

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

// chTestHost reads the test CH address from env. Returns empty string if not set.
func chTestHost() string {
	return os.Getenv("CH_TEST_HOST")
}

func chTestDatabase() string {
	if db := os.Getenv("CH_TEST_DATABASE"); db != "" {
		return db
	}
	return "test_tfc"
}

// skipIfNoCH skips the test when the CH sidecar is not reachable.
func skipIfNoCH(t *testing.T) string {
	t.Helper()
	host := chTestHost()
	if host == "" {
		t.Skip("CH_TEST_HOST not set — skipping integration test")
	}
	url := fmt.Sprintf("http://%s/ping", host)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Skipf("CH not reachable at %s: %v", host, err)
	}
	resp.Body.Close()
	return host
}

// chQuery runs a read-only query against the test CH and returns the JSON body.
func chQuery(t *testing.T, host, query string) []map[string]any {
	t.Helper()
	url := fmt.Sprintf("http://%s/?database=%s&default_format=JSONEachRow", host, chTestDatabase())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, "POST", url, strings.NewReader(query))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("chQuery: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		t.Fatalf("chQuery HTTP %d: %s\nQuery: %s", resp.StatusCode, string(body), query)
	}
	var rows []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(body)), "\n") {
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("chQuery unmarshal: %v\nLine: %s", err, line)
		}
		rows = append(rows, m)
	}
	return rows
}

// chExec runs a DDL / mutation statement.
func chExec(t *testing.T, host, stmt string) {
	t.Helper()
	url := fmt.Sprintf("http://%s/?database=%s", host, chTestDatabase())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, "POST", url, strings.NewReader(stmt))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("chExec: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("chExec HTTP %d: %s\nStmt: %s", resp.StatusCode, string(body), stmt)
	}
}

// newWriter creates a chwriter.Writer pointed at the test CH.
func newWriter(t *testing.T, host string) *chwriter.Writer {
	t.Helper()
	w, err := chwriter.New(chwriter.Config{
		URL:            fmt.Sprintf("http://%s", host),
		Database:       chTestDatabase(),
		Table:          "spans",
		MaxRetries:     3,
		InitialBackoff: 50 * time.Millisecond,
		MaxBackoff:     500 * time.Millisecond,
		RequestTimeout: 10 * time.Second,
		DeadLetterFile: t.TempDir() + "/dead.jsonl",
	})
	if err != nil {
		t.Fatalf("newWriter: %v", err)
	}
	return w
}

// uniqueIDs generates deterministic but test-unique trace/span IDs using
// the test name hash so parallel tests don't collide.
func uniqueIDs(t *testing.T, suffix byte) (traceID [16]byte, spanID [8]byte) {
	t.Helper()
	name := []byte(t.Name())
	for i := range traceID {
		if i < len(name) {
			traceID[i] = name[i]
		}
		traceID[i] ^= suffix
	}
	for i := range spanID {
		if i < len(name) {
			spanID[i] = name[i]
		}
		spanID[i] ^= suffix ^ 0x42
	}
	return
}

const testProjectID = "e2e00000-e2e0-4e2e-8e2e-e2e000000001"
const testOrgID = "e2e00000-e2e0-4e2e-8e2e-e2e000000002"

// --------------------------------------------------------------------------
// Span builders
// --------------------------------------------------------------------------

func buildLLMSpan(traceID [16]byte, spanID [8]byte) ptrace.Traces {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "e2e-llm-service")
	rs.Resource().Attributes().PutStr("fi.project_id", testProjectID)
	rs.Resource().Attributes().PutStr("fi.org_id", testOrgID)
	rs.Resource().Attributes().PutStr("fi.semconv", "openinference")

	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetTraceID(traceID)
	sp.SetSpanID(spanID)
	sp.SetName("llm.chat.completion")
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-500 * time.Millisecond)))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.Status().SetCode(ptrace.StatusCodeOk)

	a := sp.Attributes()
	a.PutStr("openinference.span.kind", "LLM")
	a.PutStr("gen_ai.system", "openai")
	a.PutStr("gen_ai.request.model", "gpt-4o-mini")
	a.PutStr("gen_ai.operation.name", "chat")
	a.PutInt("gen_ai.usage.input_tokens", 120)
	a.PutInt("gen_ai.usage.output_tokens", 38)
	a.PutInt("gen_ai.usage.total_tokens", 158)
	a.PutStr("input.value", `{"role":"user","content":"Hello e2e"}`)
	a.PutStr("output.value", `{"content":"Hi from e2e test"}`)
	a.PutBool("user.is_premium", true)
	a.PutDouble("custom.score", 0.95)
	return traces
}

// buildEndUserSessionSpan builds an OBSERVE-project LLM span carrying a
// user.id (no user.id.type → "" sentinel) and a session.id, so the converter
// stamps the deterministic end_user_id / trace_session_id columns. Used by
// the CH-derived-dimensions e2e assertion.
func buildEndUserSessionSpan(traceID [16]byte, spanID [8]byte) ptrace.Traces {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "e2e-enduser-service")
	rs.Resource().Attributes().PutStr("fi.project_id", testProjectID)
	rs.Resource().Attributes().PutStr("fi.org_id", testOrgID)
	rs.Resource().Attributes().PutStr("fi.semconv", "openinference")
	rs.Resource().Attributes().PutStr("project_type", "observe")

	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetTraceID(traceID)
	sp.SetSpanID(spanID)
	sp.SetName("llm.chat.completion")
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-300 * time.Millisecond)))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.Status().SetCode(ptrace.StatusCodeOk)

	a := sp.Attributes()
	a.PutStr("openinference.span.kind", "LLM")
	a.PutStr("gen_ai.system", "openai")
	a.PutStr("user.id", "e2e-user@example.com")
	a.PutStr("session.id", "e2e-session-001")
	return traces
}

func buildErrorSpan(traceID [16]byte, spanID [8]byte) ptrace.Traces {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "e2e-error-service")
	rs.Resource().Attributes().PutStr("fi.project_id", testProjectID)
	rs.Resource().Attributes().PutStr("fi.org_id", testOrgID)

	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetTraceID(traceID)
	sp.SetSpanID(spanID)
	sp.SetName("failed.operation")
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-200 * time.Millisecond)))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.Status().SetCode(ptrace.StatusCodeError)
	sp.Status().SetMessage("rate limit exceeded")

	a := sp.Attributes()
	a.PutStr("openinference.span.kind", "LLM")
	a.PutStr("gen_ai.system", "anthropic")
	a.PutStr("gen_ai.request.model", "claude-3-5-sonnet")
	return traces
}

func buildToolSpan(traceID [16]byte, spanID [8]byte, parentID [8]byte) ptrace.Traces {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "e2e-tool-service")
	rs.Resource().Attributes().PutStr("fi.project_id", testProjectID)
	rs.Resource().Attributes().PutStr("fi.org_id", testOrgID)

	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetTraceID(traceID)
	sp.SetSpanID(spanID)
	sp.SetParentSpanID(parentID)
	sp.SetName("tool.search")
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-100 * time.Millisecond)))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.Status().SetCode(ptrace.StatusCodeOk)

	a := sp.Attributes()
	a.PutStr("openinference.span.kind", "TOOL")
	a.PutStr("tool.name", "web_search")
	a.PutStr("input.value", "search query")
	a.PutStr("output.value", "search results")
	return traces
}

func buildVoiceSpan(traceID [16]byte, spanID [8]byte) ptrace.Traces {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "e2e-voice-service")
	rs.Resource().Attributes().PutStr("fi.project_id", testProjectID)
	rs.Resource().Attributes().PutStr("fi.org_id", testOrgID)
	rs.Resource().Attributes().PutStr("fi.semconv", "fi_native")

	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetTraceID(traceID)
	sp.SetSpanID(spanID)
	sp.SetName("voice.conversation.turn")
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-2 * time.Second)))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.Status().SetCode(ptrace.StatusCodeOk)

	a := sp.Attributes()
	a.PutStr("fi.span.kind", "CONVERSATION")
	a.PutStr("gen_ai.system", "elevenlabs")
	a.PutStr("voice.language", "en-US")
	a.PutInt("voice.duration_ms", 1850)
	return traces
}

// --------------------------------------------------------------------------
// Ingest helper: convert + write
// --------------------------------------------------------------------------

func ingestSpans(t *testing.T, w *chwriter.Writer, traces ptrace.Traces) {
	t.Helper()
	rows, err := Convert(traces)
	if err != nil {
		t.Fatalf("Convert: %v", err)
	}
	if err := w.Insert(context.Background(), rows); err != nil {
		t.Fatalf("Insert: %v", err)
	}
}

// waitForRow polls CH until a row with the given span ID appears or timeout.
func waitForRow(t *testing.T, host, spanID string, timeout time.Duration) map[string]any {
	t.Helper()
	deadline := time.Now().Add(timeout)
	query := fmt.Sprintf(
		"SELECT * FROM spans FINAL WHERE id = '%s' FORMAT JSONEachRow",
		spanID,
	)
	for time.Now().Before(deadline) {
		rows := chQuery(t, host, query)
		if len(rows) > 0 {
			return rows[0]
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatalf("span %s not found in CH after %v", spanID, timeout)
	return nil
}

// --------------------------------------------------------------------------
// Tests
// --------------------------------------------------------------------------

func TestE2E_LLMSpan_TypedMapSplit(t *testing.T) {
	host := skipIfNoCH(t)
	w := newWriter(t, host)
	defer w.Close()

	traceID, spanID := uniqueIDs(t, 0x01)
	ingestSpans(t, w, buildLLMSpan(traceID, spanID))

	spanIDHex := strings.ToLower(fmt.Sprintf("%016x", spanID))
	row := waitForRow(t, host, spanIDHex, 5*time.Second)

	// Typed-Map split: attrs_string must have gen_ai keys
	assertMapContains(t, row, "attrs_string", "gen_ai.request.model", "gpt-4o-mini")

	// attrs_number must have custom.score
	assertMapNumeric(t, row, "attrs_number", "custom.score")

	// attrs_bool must have user.is_premium
	assertMapContains(t, row, "attrs_bool", "user.is_premium", nil)

	// Hot columns populated
	assertField(t, row, "model", "gpt-4o-mini")
	assertField(t, row, "provider", "openai")
	assertField(t, row, "gen_ai_system", "openai")
	assertField(t, row, "gen_ai_operation", "chat")
	assertField(t, row, "observation_type", "LLM")

	// Token counts
	assertFieldFloat(t, row, "prompt_tokens", 120)
	assertFieldFloat(t, row, "completion_tokens", 38)
	assertFieldFloat(t, row, "total_tokens", 158)

	// is_deleted = 0 for fresh spans
	assertFieldFloat(t, row, "is_deleted", 0)

	// semconv_source
	assertField(t, row, "semconv_source", "openinference")
}

// TestE2E_DeterministicDimensions verifies the CH-derived-dimensions stamp
// (P3b step2): an observe-project span with user.id + session.id lands the
// deterministic end_user_id / trace_session_id in the CH `spans` row, and the
// stored bytes equal the detid formula (which pkg/detid proves == Python).
func TestE2E_DeterministicDimensions(t *testing.T) {
	host := skipIfNoCH(t)
	w := newWriter(t, host)
	defer w.Close()

	traceID, spanID := uniqueIDs(t, 0x20)
	ingestSpans(t, w, buildEndUserSessionSpan(traceID, spanID))

	spanIDHex := strings.ToLower(fmt.Sprintf("%016x", spanID))
	row := waitForRow(t, host, spanIDHex, 5*time.Second)

	wantEU := detid.EndUserID(testProjectID, testOrgID, "e2e-user@example.com", "").String()
	assertField(t, row, "end_user_id", wantEU)

	wantSess := detid.TraceSessionID(testProjectID, "e2e-session-001").String()
	assertField(t, row, "trace_session_id", wantSess)
}

// curatedDDL is the schema 017/018 DDL (table half only — the dicts aren't
// needed for the write test), kept idempotent (CREATE ... IF NOT EXISTS) so the
// integration test is self-contained when run-e2e.sh hasn't applied them. The
// columns/types mirror futureagi schema 017_end_users.sql / 018_trace_sessions.sql.
const curatedDDL = `
CREATE TABLE IF NOT EXISTS end_users
(
    project_id       UUID,
    end_user_id      UUID,
    organization_id  UUID,
    user_id          String,
    user_id_type     LowCardinality(Nullable(String)),
    user_id_hash     String                  DEFAULT '',
    metadata         String                  DEFAULT '{}' CODEC(ZSTD(1)),
    first_seen       DateTime64(6, 'UTC'),
    version          DateTime64(6, 'UTC')    DEFAULT now64(6, 'UTC'),
    is_deleted       UInt8                   DEFAULT 0
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (project_id, end_user_id);
CREATE TABLE IF NOT EXISTS trace_sessions
(
    project_id          UUID,
    trace_session_id    UUID,
    external_session_id String                  DEFAULT '',
    first_seen          DateTime64(6, 'UTC'),
    version             DateTime64(6, 'UTC')    DEFAULT now64(6, 'UTC'),
    is_deleted          UInt8                   DEFAULT 0
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (project_id, trace_session_id);`

// TestE2E_CuratedDimensions_RMTWrite verifies P3b step2 HALF 2 end-to-end: an
// observe span carrying user.id + curated hash/metadata + session.id, ingested
// through ConvertWithIdentities + curatedwriter, lands ONE row in `end_users`
// and ONE in `trace_sessions` — keyed by the SAME deterministic id stamped on
// the span — with the curated fields and a DateTime64 version. Proves the
// collector closes the dict-NULL gap for collector-originated entities.
func TestE2E_CuratedDimensions_RMTWrite(t *testing.T) {
	host := skipIfNoCH(t)
	// Self-contained: ensure the curated RMTs exist (idempotent).
	for _, stmt := range strings.Split(curatedDDL, ";") {
		if strings.TrimSpace(stmt) == "" {
			continue
		}
		chExec(t, host, stmt)
	}

	w := newWriter(t, host)
	defer w.Close()
	cw := curatedwriter.New(w)

	traceID, spanID := uniqueIDs(t, 0x21)
	// Build an observe span with full curated fields + a session.
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "e2e-curated")
	rs.Resource().Attributes().PutStr("fi.project_id", testProjectID)
	rs.Resource().Attributes().PutStr("fi.org_id", testOrgID)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetTraceID(traceID)
	sp.SetSpanID(spanID)
	sp.SetName("llm.chat.completion")
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-200 * time.Millisecond)))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.Status().SetCode(ptrace.StatusCodeOk)
	a := sp.Attributes()
	a.PutStr("openinference.span.kind", "LLM")
	a.PutStr("user.id", "curated-e2e@example.com")
	a.PutStr("user.id.type", "email")
	a.PutStr("user.id.hash", "e2e-hash")
	a.PutStr("user.metadata", `{"src":"collector"}`)
	a.PutStr("session.id", "curated-e2e-session")

	rows, ids, err := ConvertWithIdentities(context.Background(), traces, nil)
	if err != nil {
		t.Fatalf("ConvertWithIdentities: %v", err)
	}
	if err := w.Insert(context.Background(), rows); err != nil {
		t.Fatalf("span Insert: %v", err)
	}
	if err := cw.Write(context.Background(), ids, time.Now().UTC()); err != nil {
		t.Fatalf("curated Write: %v", err)
	}

	wantEU := detid.EndUserID(testProjectID, testOrgID, "curated-e2e@example.com", "email").String()
	wantSess := detid.TraceSessionID(testProjectID, "curated-e2e-session").String()

	// end_users row landed, keyed by the deterministic id == the span's id.
	euRows := waitForCurated(t, host,
		fmt.Sprintf("SELECT * FROM end_users FINAL WHERE end_user_id = '%s' FORMAT JSONEachRow", wantEU),
		5*time.Second)
	eu := euRows[0]
	assertField(t, eu, "end_user_id", wantEU)
	assertField(t, eu, "project_id", testProjectID)
	assertField(t, eu, "organization_id", testOrgID)
	assertField(t, eu, "user_id", "curated-e2e@example.com")
	assertField(t, eu, "user_id_type", "email")
	assertField(t, eu, "user_id_hash", "e2e-hash")
	assertField(t, eu, "metadata", `{"src":"collector"}`)
	if _, err := time.Parse("2006-01-02 15:04:05.000000", asString(eu["version"])); err != nil {
		t.Errorf("end_users.version %q not DateTime64(6): %v", eu["version"], err)
	}
	// Consistency: the curated id equals the id on the span row in CH.
	spanIDHex := strings.ToLower(fmt.Sprintf("%016x", spanID))
	spanRow := waitForRow(t, host, spanIDHex, 5*time.Second)
	assertField(t, spanRow, "end_user_id", wantEU)

	// trace_sessions row landed, keyed by the deterministic id == the span's id.
	sRows := waitForCurated(t, host,
		fmt.Sprintf("SELECT * FROM trace_sessions FINAL WHERE trace_session_id = '%s' FORMAT JSONEachRow", wantSess),
		5*time.Second)
	s := sRows[0]
	assertField(t, s, "trace_session_id", wantSess)
	assertField(t, s, "project_id", testProjectID)
	assertField(t, s, "external_session_id", "curated-e2e-session")
	assertField(t, spanRow, "trace_session_id", wantSess)
}

// waitForCurated polls CH until the query returns at least one row or timeout.
func waitForCurated(t *testing.T, host, query string, timeout time.Duration) []map[string]any {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		rows := chQuery(t, host, query)
		if len(rows) > 0 {
			return rows
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatalf("no rows for query after %v: %s", timeout, query)
	return nil
}

func asString(v any) string {
	s, _ := v.(string)
	return s
}

func TestE2E_JSONColumns_Parse(t *testing.T) {
	host := skipIfNoCH(t)
	w := newWriter(t, host)
	defer w.Close()

	traceID, spanID := uniqueIDs(t, 0x02)
	ingestSpans(t, w, buildLLMSpan(traceID, spanID))

	spanIDHex := strings.ToLower(fmt.Sprintf("%016x", spanID))
	row := waitForRow(t, host, spanIDHex, 5*time.Second)

	// resource_attrs must be queryable JSON — verify fi.project_id is in it
	ra := row["resource_attrs"]
	if ra == nil {
		t.Fatal("resource_attrs is nil")
	}
	// CH returns JSON columns as objects in JSONEachRow format
	raMap, ok := ra.(map[string]any)
	if !ok {
		// Could also be string — try parsing
		raStr, sok := ra.(string)
		if !sok {
			t.Fatalf("resource_attrs unexpected type: %T", ra)
		}
		if err := json.Unmarshal([]byte(raStr), &raMap); err != nil {
			t.Fatalf("resource_attrs not valid JSON: %v — raw: %q", err, raStr)
		}
	}

	// metadata must parse (empty object is fine for fresh spans)
	md := row["metadata"]
	if md == nil {
		t.Fatal("metadata is nil")
	}

	// attributes_extra: verify it's queryable (may contain overflow keys)
	ae := row["attributes_extra"]
	if ae == nil {
		t.Fatal("attributes_extra is nil")
	}
}

func TestE2E_ErrorSpan(t *testing.T) {
	host := skipIfNoCH(t)
	w := newWriter(t, host)
	defer w.Close()

	traceID, spanID := uniqueIDs(t, 0x03)
	ingestSpans(t, w, buildErrorSpan(traceID, spanID))

	spanIDHex := strings.ToLower(fmt.Sprintf("%016x", spanID))
	row := waitForRow(t, host, spanIDHex, 5*time.Second)

	assertField(t, row, "status", "ERROR")
	assertField(t, row, "status_message", "rate limit exceeded")
	assertField(t, row, "model", "claude-3-5-sonnet")
	assertField(t, row, "provider", "anthropic")
	assertFieldFloat(t, row, "is_deleted", 0)
}

func TestE2E_ToolSpan_ParentLink(t *testing.T) {
	host := skipIfNoCH(t)
	w := newWriter(t, host)
	defer w.Close()

	traceID, parentSpanID := uniqueIDs(t, 0x04)
	_, childSpanID := uniqueIDs(t, 0x05)

	ingestSpans(t, w, buildLLMSpan(traceID, parentSpanID))
	ingestSpans(t, w, buildToolSpan(traceID, childSpanID, parentSpanID))

	childHex := strings.ToLower(fmt.Sprintf("%016x", childSpanID))
	parentHex := strings.ToLower(fmt.Sprintf("%016x", parentSpanID))

	row := waitForRow(t, host, childHex, 5*time.Second)

	assertField(t, row, "observation_type", "TOOL")
	assertField(t, row, "parent_span_id", parentHex)
	assertField(t, row, "name", "tool.search")
}

func TestE2E_VoiceConversationSpan(t *testing.T) {
	host := skipIfNoCH(t)
	w := newWriter(t, host)
	defer w.Close()

	traceID, spanID := uniqueIDs(t, 0x06)
	ingestSpans(t, w, buildVoiceSpan(traceID, spanID))

	spanIDHex := strings.ToLower(fmt.Sprintf("%016x", spanID))
	row := waitForRow(t, host, spanIDHex, 5*time.Second)

	assertField(t, row, "observation_type", "CONVERSATION")
	assertField(t, row, "gen_ai_system", "elevenlabs")
	assertField(t, row, "semconv_source", "fi_native")
}

func TestE2E_MultiSpanTrace(t *testing.T) {
	host := skipIfNoCH(t)
	w := newWriter(t, host)
	defer w.Close()

	traceID, rootSpanID := uniqueIDs(t, 0x10)
	_, child1SpanID := uniqueIDs(t, 0x11)
	_, child2SpanID := uniqueIDs(t, 0x12)

	ingestSpans(t, w, buildLLMSpan(traceID, rootSpanID))
	ingestSpans(t, w, buildToolSpan(traceID, child1SpanID, rootSpanID))
	ingestSpans(t, w, buildToolSpan(traceID, child2SpanID, rootSpanID))

	traceIDHex := traceIDToUUIDString(pcommon.TraceID(traceID))
	query := fmt.Sprintf(
		"SELECT id FROM spans FINAL WHERE trace_id = '%s' AND project_id = '%s' ORDER BY id",
		traceIDHex, testProjectID,
	)

	// Wait for all 3 spans to land.
	deadline := time.Now().Add(5 * time.Second)
	var rows []map[string]any
	for time.Now().Before(deadline) {
		rows = chQuery(t, host, query)
		if len(rows) >= 3 {
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	if len(rows) < 3 {
		t.Fatalf("expected 3 spans in trace, got %d", len(rows))
	}
}

// --------------------------------------------------------------------------
// Assertion helpers
// --------------------------------------------------------------------------

func assertField(t *testing.T, row map[string]any, key, want string) {
	t.Helper()
	got, ok := row[key]
	if !ok {
		t.Errorf("row missing key %q", key)
		return
	}
	gotStr, _ := got.(string)
	if gotStr != want {
		t.Errorf("%s: got %q want %q", key, gotStr, want)
	}
}

func assertFieldFloat(t *testing.T, row map[string]any, key string, want float64) {
	t.Helper()
	got, ok := row[key]
	if !ok {
		t.Errorf("row missing key %q", key)
		return
	}
	// JSONEachRow decodes numbers as float64.
	gotF, ok := got.(float64)
	if !ok {
		t.Errorf("%s: unexpected type %T", key, got)
		return
	}
	if gotF != want {
		t.Errorf("%s: got %v want %v", key, gotF, want)
	}
}

func assertMapContains(t *testing.T, row map[string]any, mapCol, key string, wantVal any) {
	t.Helper()
	m, ok := row[mapCol]
	if !ok {
		t.Errorf("row missing column %q", mapCol)
		return
	}
	mm, ok := m.(map[string]any)
	if !ok {
		t.Errorf("%s: unexpected type %T (expected map)", mapCol, m)
		return
	}
	if _, present := mm[key]; !present {
		t.Errorf("%s missing key %q", mapCol, key)
		return
	}
	if wantVal != nil {
		if mm[key] != wantVal {
			t.Errorf("%s[%s]: got %v want %v", mapCol, key, mm[key], wantVal)
		}
	}
}

func assertMapNumeric(t *testing.T, row map[string]any, mapCol, key string) {
	t.Helper()
	m, ok := row[mapCol]
	if !ok {
		t.Errorf("row missing column %q", mapCol)
		return
	}
	mm, ok := m.(map[string]any)
	if !ok {
		t.Errorf("%s: unexpected type %T (expected map)", mapCol, m)
		return
	}
	if _, present := mm[key]; !present {
		t.Errorf("%s missing key %q", mapCol, key)
	}
}
