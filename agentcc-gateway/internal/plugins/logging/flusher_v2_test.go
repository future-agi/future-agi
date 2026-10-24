package logging

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// examplesDir holds the golden contract examples and their schemas.
var examplesDir = filepath.Join("..", "..", "..", "..", "api_contracts", "gateway", "examples")

func readExample(t *testing.T, name string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(examplesDir, name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	var v map[string]any
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatalf("parse %s: %v", name, err)
	}
	return v
}

func toJSONMap(t *testing.T, v any) map[string]any {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

// The validator rejects what the schemas forbid, so a pass means something.
func TestSchemaValidatorRejects(t *testing.T) {
	golden := readExample(t, "log-entry-v2.json")
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", golden); len(errs) > 0 {
		t.Fatalf("the golden example fails the validator: %v", errs)
	}
	for name, mutate := range map[string]func(m map[string]any){
		"unknown key":        func(m map[string]any) { m["surprise"] = 1 },
		"kind on a log":      func(m map[string]any) { m["kind"] = "outcome" },
		"missing required":   func(m map[string]any) { delete(m, "request_id") },
		"bad enum":           func(m map[string]any) { m["credential_type"] = "shared" },
		"bad attempt id":     func(m map[string]any) { m["attempts"].([]any)[0].(map[string]any)["attempt_id"] = "req.17" },
		"bad date-time":      func(m map[string]any) { m["timestamp"] = "yesterday" },
		"fractional integer": func(m map[string]any) { m["prompt_tokens"] = 1.5 },
		"wrong type":         func(m map[string]any) { m["tags"] = "a,b" },
		"long error message": func(m map[string]any) {
			m["attempts"].([]any)[0].(map[string]any)["error_message"] = strings.Repeat("x", 513)
		},
	} {
		m := readExample(t, "log-entry-v2.json")
		mutate(m)
		if errs := schematest.Validate(t, "log-entry-v2.schema.json", m); len(errs) == 0 {
			t.Errorf("%s: the validator accepted it", name)
		}
	}
}

// --- log entry v2 ------------------------------------------------------------

func intp(n int) *int { return &n }

// goldenRequest builds the record and the request state of the
// log-entry-v2.json example.
func goldenRequest(t *testing.T) (TraceRecord, *models.RequestContext) {
	t.Helper()
	ts := func(s string) time.Time {
		v, err := time.Parse(time.RFC3339Nano, s)
		if err != nil {
			t.Fatal(err)
		}
		return v
	}
	rec := TraceRecord{
		RequestID: "req_01JD8Z3K4M5N6P7Q8R9S", Timestamp: ts("2026-10-15T09:30:12.418Z"),
		Model: "auto", ResolvedModel: "anthropic/claude-sonnet-5.5", Provider: "openrouter",
		PromptTokens: 1820, CompletionTokens: 412, TotalTokens: 2232, StatusCode: 200, LatencyMs: 2311,
		FallbackUsed: true, AuthKeyID: "key_7f3a", UserID: "user_42", SessionID: "sess_9c1d",
		Metadata: map[string]string{"cost": "0.011815", "cost_markup_percent": "5", "outcome_class": "ok",
			"auto_requested": "auto", "team": "caller-dimension"},
		RequestBody:    &models.ChatCompletionRequest{Model: "auto"},
		RequestHeaders: map[string][]string{"X-Title": {"Deep Research"}},
	}
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.RequestID = rec.RequestID
	rc.CustomMetadataKeys = append(rc.CustomMetadataKeys, "team")
	details := json.RawMessage(`{"cached_tokens":1024,"cache_write_tokens":0}`)
	reasoning := json.RawMessage(`{"reasoning_tokens":96}`)
	rc.Response = &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 1820, CompletionTokens: 412,
		TotalTokens: 2232, PromptTokensDetails: &details, CompletionTokensDetails: &reasoning}}
	rc.Agent = models.AgentContext{
		Framework: "omega", AgentID: "researcher", AgentVersion: "3", RunID: "run_R2", ParentRunID: "run_R1",
		RootRunID: "run_R1", TaskID: "task_17", TraceID: "4bf92f3577b34da6a3ce929d0e0e4736", Workflow: "deep-research",
		Step: models.StepAct, StepSource: "header", Turn: intp(4), Env: "production", BudgetScope: "run_R1",
		AttemptID: "att_88", IdentitySource: models.IdentitySourceRuntimeToken, AliasOverridden: []string{"x-thread-id"},
		AppTitle: "Deep Research", AppReferer: "https://research.example.com", EndUser: "customer-1932",
		Tags: []string{"research", "batch-7"}, SessionID: "sess_9c1d",
	}
	rc.Billing = models.BillingState{
		Mode: models.BillingModeManaged, CredentialType: models.CredentialManaged,
		UpstreamMicros: 11252, MarkupMicros: 563, MarkupBps: 500, CostSource: models.CostSourceProviderReported,
		LandedMicros: 12249, ServedUpstreamProvider: "anthropic", UpstreamKeyID: "kh_3b9e",
	}
	rc.Evidence = models.RoutingEvidence{PolicyVersion: "v12", PolicyVariant: "stable", CatalogSnapshotID: "3f9a1c0b2d4e",
		AutoStep: models.StepAct, Affinity: "hit", Preset: "fast-act", PresetSource: "step_presets"}
	rc.Privacy = models.Privacy{ContentMode: models.ContentModeMetadata, Source: models.PrivacySourceOrg}
	rc.ToolCallGuard = true
	rc.ToolCalls = models.ToolCallGuardResult{Checked: 2}
	attempt := func(kind models.AttemptKind, reason, model, upstream string, status int, outcome models.AttemptOutcome, started string) models.Attempt {
		return models.Attempt{Kind: kind, Reason: reason, Provider: "openrouter", Model: model,
			EndpointID: "openrouter:" + upstream + ":" + model, UpstreamProvider: upstream, ServedUpstreamProvider: upstream,
			UpstreamKeyID: "kh_3b9e", CredentialType: models.CredentialManaged, StatusCode: status, Outcome: outcome,
			StartedAt: ts(started), UpstreamFeeBps: 550, ProcessingFeeBps: 320}
	}
	a1 := attempt(models.AttemptPrimary, "", "openai/gpt-6-sol", "openai", 429, models.OutcomeError, "2026-10-15T09:30:10.107Z")
	a1.ErrorClass, a1.ErrorOrigin, a1.ErrorMessage = models.ErrClassRateLimited, models.OriginUpstream, "Rate limit reached for requests"
	a1.LatencyMs, a1.CostSource = 180, models.CostSourceUnpriced
	a2 := attempt(models.AttemptAutoFallback, models.ReasonStatus429, "anthropic/claude-sonnet-5.5", "anthropic", 200, models.OutcomeSuccess, "2026-10-15T09:30:10.301Z")
	a2.LatencyMs, a2.TTFBMs, a2.PromptTokens, a2.CompletionTokens = 2117, 640, 1820, 412
	a2.CachedTokens, a2.ReasoningTokens = 1024, 96
	a2.UpstreamCostMicros, a2.BilledCostMicros, a2.LandedCostMicros = 11252, 11815, 12249
	a2.CostSource, a2.Billable = models.CostSourceProviderReported, true
	rc.AddAttempt(a1)
	rc.AddAttempt(a2)
	return rec, rc
}

// stripZeros drops the keys whose value is a JSON zero (0, false, "", [],
// {}), which the golden example spells out and the gateway omits as empty.
func stripZeros(v any) any {
	switch x := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(x))
		for k, val := range x {
			val = stripZeros(val)
			switch z := val.(type) {
			case float64:
				if z == 0 {
					continue
				}
			case bool:
				if !z {
					continue
				}
			case string:
				if z == "" {
					continue
				}
			}
			out[k] = val
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, el := range x {
			out[i] = stripZeros(el)
		}
		return out
	}
	return v
}

func TestLogEntryV2Golden(t *testing.T) {
	rec, rc := goldenRequest(t)
	st := rc.SnapshotV2()
	entry := buildLogEntry(rec, &st, rc.Response.Usage, DefaultLogEntryOptions())
	got := toJSONMap(t, entry)
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", got); len(errs) > 0 {
		t.Fatalf("the entry fails log-entry-v2.schema.json:\n%s", strings.Join(errs, "\n"))
	}
	b, _ := json.Marshal(entry)
	t.Logf("log entry v2: %s", b)

	want := readExample(t, "log-entry-v2.json")
	// v1 keeps the caller's metadata and promotes metadata.cost; under
	// metadata mode the caller's keys stay (none drops them).
	want["metadata"].(map[string]any)["cost"] = "0.011815"
	want["metadata"].(map[string]any)["auto_requested"] = "auto"
	want["metadata"].(map[string]any)["team"] = "caller-dimension"
	// Compared without zero values: the golden example spells some out, the gateway
	// omits the empty v2 ones, and the schema checked the required v1 ones.
	if g, w := stripZeros(got), stripZeros(want); !reflect.DeepEqual(g, w) {
		gb, _ := json.MarshalIndent(g, "", " ")
		wb, _ := json.MarshalIndent(w, "", " ")
		t.Errorf("entry differs from the golden example (zero values left out):\n got %s\nwant %s", gb, wb)
	}
	if _, ok := got["request_body"]; ok {
		t.Error("metadata content mode kept the request body")
	}
}

func TestLogEntryV2OmitsEmpty(t *testing.T) {
	rec := TraceRecord{RequestID: "req_1", Timestamp: time.Unix(1700000000, 0).UTC(), Model: "gpt-4o",
		Provider: "openai", StatusCode: 200, Metadata: map[string]string{"cost": "0.1"}}
	v1 := toJSONMap(t, buildLogEntryV1(rec))

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = rec.RequestID
	st := rc.SnapshotV2()
	v2 := toJSONMap(t, buildLogEntry(rec, &st, nil, DefaultLogEntryOptions()))
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", v2); len(errs) > 0 {
		t.Fatalf("empty v2 entry fails the schema: %v", errs)
	}
	var extra []string
	for k := range v2 {
		if _, ok := v1[k]; !ok {
			extra = append(extra, k)
		}
	}
	if !reflect.DeepEqual(extra, []string{"generation_id"}) || v2["generation_id"] != "req_1" {
		t.Errorf("an empty v2 state added %v (generation_id=%v); want only generation_id=req_1", extra, v2["generation_id"])
	}
	for _, k := range []string{"attempts", "agent", "routing", "billing_mode", "guardrail", "content_mode", "app", "tags"} {
		if _, ok := v1[k]; ok {
			t.Errorf("a v1 entry carries %q", k)
		}
	}

	// One set field brings its object, and only its object.
	rc.Agent.Turn = intp(0)
	rc.Evidence.PolicyVersion = "unversioned"
	st = rc.SnapshotV2()
	m := toJSONMap(t, buildLogEntry(rec, &st, nil, DefaultLogEntryOptions()))
	if !reflect.DeepEqual(m["agent"], map[string]any{"turn": 0.0}) || !reflect.DeepEqual(m["routing"], map[string]any{"policy_version": "unversioned"}) {
		t.Errorf("agent = %v, routing = %v", m["agent"], m["routing"])
	}
}

func TestLogEntryContentModes(t *testing.T) {
	rec, rc := goldenRequest(t)
	rec.ErrorMessage = "upstream said: <prompt text>"
	rec.ResponseBodyJSON = json.RawMessage(`{"x":1}`)

	rc.Privacy = models.Privacy{}
	st := rc.SnapshotV2()
	full := toJSONMap(t, buildLogEntry(rec, &st, nil, DefaultLogEntryOptions()))
	for _, k := range []string{"request_body", "response_body", "request_headers", "error_message"} {
		if _, ok := full[k]; !ok {
			t.Errorf("full mode dropped %s", k)
		}
	}
	if _, ok := full["content_mode"]; ok {
		t.Error("an unresolved content mode was stamped")
	}
	noBodies := toJSONMap(t, buildLogEntry(rec, &st, nil, LogEntryOptions{IncludeBodies: false}))
	if _, ok := noBodies["request_body"]; ok {
		t.Error("IncludeBodies=false kept the request body")
	}

	none := toJSONMap(t, buildLogEntry(rec, &st, nil, LogEntryOptions{IncludeBodies: true, ContentMode: models.ContentModeNone}))
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", none); len(errs) > 0 {
		t.Fatalf("none entry fails the schema: %v", errs)
	}
	for _, k := range []string{"request_body", "response_body", "request_headers", "error_message"} {
		if _, ok := none[k]; ok {
			t.Errorf("none mode kept %s", k)
		}
	}
	meta := none["metadata"].(map[string]any)
	if _, ok := meta["team"]; ok || meta["outcome_class"] != "ok" {
		t.Errorf("none mode metadata = %v; want the caller's key gone and the gateway's kept", meta)
	}
	a0 := none["attempts"].([]any)[0].(map[string]any)
	if _, ok := a0["error_message"]; ok || a0["error_class"] != "rate_limited" || a0["status_code"] != 429.0 {
		t.Errorf("none mode attempt = %v; want class and status only", a0)
	}
	if none["content_mode"] != "none" {
		t.Errorf("content_mode = %v", none["content_mode"])
	}
	// The record itself is untouched: other sinks still see it whole.
	if rec.ErrorMessage == "" || rec.Metadata["team"] == "" || rc.AttemptsSnapshot()[0].ErrorMessage == "" {
		t.Error("buildLogEntry changed the record or the request state")
	}
}

// --- events ------------------------------------------------------------------

// batchRecorder is a log webhook that keeps every batch it receives.
type batchRecorder struct {
	*httptest.Server
	mu      sync.Mutex
	batches [][]map[string]any
	status  int
}

func newBatchRecorder(t *testing.T) *batchRecorder {
	t.Helper()
	b := &batchRecorder{status: http.StatusOK}
	b.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var payload struct {
			Logs []map[string]any `json:"logs"`
		}
		_ = json.Unmarshal(raw, &payload)
		b.mu.Lock()
		b.batches = append(b.batches, payload.Logs)
		status := b.status
		b.mu.Unlock()
		w.WriteHeader(status)
	}))
	t.Cleanup(b.Close)
	return b
}

func (b *batchRecorder) all() [][]map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([][]map[string]any(nil), b.batches...)
}

func outcomeEvent() models.OutcomeEvent {
	score := 0.2
	return models.OutcomeEvent{EventID: "evt_1", OrgID: "org_1", GenerationID: "req_01JD8Z3K4M5N6P7Q8R9S",
		Outcome: "verify_failed", Source: "prev_outcome", Score: &score, AgentRunID: "run_R2", AgentStep: models.StepAct, TsMs: 1792056614201}
}

func TestEnqueueEventSameBatch(t *testing.T) {
	wh := newBatchRecorder(t)
	f := NewLogFlusher(wh.URL, "", time.Hour, 100)
	rec, rc := goldenRequest(t)
	f.EnqueueRequest(rec, rc, DefaultLogEntryOptions())
	f.EnqueueEvent(models.EventKindOutcome, outcomeEvent())
	f.EnqueueEvent(models.EventKindOutcome, "not an object") // dropped
	f.flush()

	batches := wh.all()
	if len(batches) != 1 || len(batches[0]) != 2 {
		t.Fatalf("webhook got %d batches %v; want one with the entry and the event", len(batches), batches)
	}
	entry, event := batches[0][0], batches[0][1]
	if _, ok := entry["kind"]; ok {
		t.Errorf("the log entry carries a kind: %v", entry)
	}
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", entry); len(errs) > 0 {
		t.Errorf("entry fails log-entry-v2: %v", errs)
	}
	if event["kind"] != "outcome" {
		t.Errorf("event kind = %v", event["kind"])
	}
	if errs := schematest.Validate(t, "outcome-event-v1.schema.json", event); len(errs) > 0 {
		t.Errorf("event fails outcome-event-v1: %v", errs)
	}
	if len(f.states) != 0 {
		t.Errorf("a delivered record's state is still held: %v", f.states)
	}

	// The schema is not trivially satisfied.
	bad := map[string]any{}
	for k, v := range event {
		bad[k] = v
	}
	delete(bad, "event_id")
	if errs := schematest.Validate(t, "outcome-event-v1.schema.json", bad); len(errs) == 0 {
		t.Error("an outcome event without event_id passed the schema")
	}
}

func TestEventsRetriedWithTheirBatch(t *testing.T) {
	wh := newBatchRecorder(t)
	wh.status = http.StatusServiceUnavailable
	f := NewLogFlusher(wh.URL, "", time.Hour, 100)
	rec, rc := goldenRequest(t)
	f.EnqueueRequest(rec, rc, DefaultLogEntryOptions())
	f.EnqueueEvent(models.EventKindOutcome, outcomeEvent())
	f.flush() // 503: both go back

	wh.mu.Lock()
	wh.status = http.StatusOK
	wh.mu.Unlock()
	f.flush()
	batches := wh.all()
	if len(batches) != 2 || len(batches[1]) != 2 {
		t.Fatalf("batches = %v; want the retry to carry the entry and the event", batches)
	}
	if batches[1][0]["agent"] == nil || batches[1][1]["kind"] != "outcome" {
		t.Errorf("the retried batch lost the v2 state or the event: %v", batches[1])
	}

	// An event alone is sent too, and Close delivers what is left.
	f.EnqueueEvent(models.EventKindOutcome, outcomeEvent())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	f.Close(ctx)
	batches = wh.all()
	if len(batches) != 3 || len(batches[2]) != 1 || batches[2][0]["kind"] != "outcome" {
		t.Errorf("Close sent %v; want one batch with the waiting event", batches[len(batches)-1])
	}
	f.EnqueueEvent(models.EventKindOutcome, outcomeEvent()) // refused once closed
	if f.hasEvents() {
		t.Error("a closed flusher took an event")
	}
}

func TestDroppedRecordForgetsItsState(t *testing.T) {
	wh := newBatchRecorder(t)
	wh.status = http.StatusBadRequest // a client error drops the batch
	f := NewLogFlusher(wh.URL, "", time.Hour, 100)
	rec, rc := goldenRequest(t)
	f.EnqueueRequest(rec, rc, DefaultLogEntryOptions())
	f.flush()
	if len(f.states) != 0 {
		t.Errorf("a dropped record's state is still held: %v", f.states)
	}
}

// The logging plugin hands the flusher the request's v2 state, so the
// webhook entry carries the attempts and agent context the request recorded,
// and keeps the bodies when request logging is configured to include them.
func TestPluginSendsLogEntryV2(t *testing.T) {
	wh := newBatchRecorder(t)
	f := NewLogFlusher(wh.URL, "", time.Hour, 100)
	p := New(config.RequestLoggingConfig{Enabled: true, IncludeBodies: true, BufferSize: 16, Workers: 1}, nil)
	defer p.Close()
	p.SetFlusher(f)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req_plugin"
	rc.Model = "gpt-4o"
	rc.Provider = "openai"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	rc.Response = &models.ChatCompletionResponse{Model: "gpt-4o"}
	rc.Agent.RunID = "run_1"
	rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "openai", Model: "gpt-4o",
		CredentialType: models.CredentialBYOK, Outcome: models.OutcomeSuccess, StartedAt: time.Now()})
	p.ProcessResponse(context.Background(), rc)
	f.flush()

	batches := wh.all()
	if len(batches) != 1 || len(batches[0]) != 1 {
		t.Fatalf("batches = %v", batches)
	}
	entry := batches[0][0]
	attempts, _ := entry["attempts"].([]any)
	if len(attempts) != 1 || attempts[0].(map[string]any)["kind"] != "primary" || entry["attempt_count"] != 1.0 {
		t.Errorf("the plugin's entry lost the attempts: %v", entry)
	}
	agent, _ := entry["agent"].(map[string]any)
	if agent["run_id"] != "run_1" || entry["generation_id"] != "req_plugin" {
		t.Errorf("the plugin's entry lost the agent context or generation id: %v", entry)
	}
	if entry["request_body"] == nil {
		t.Errorf("include_bodies is on, but the entry has no request body: %v", entry)
	}
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", entry); len(errs) > 0 {
		t.Errorf("entry fails log-entry-v2: %v", errs)
	}
}
