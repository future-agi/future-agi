package models

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strconv"
	"testing"
	"time"
)

func TestAttemptCapAndIDs(t *testing.T) {
	rc := AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req_1"

	for i := 1; i <= MaxAttempts; i++ {
		got := rc.AddAttempt(Attempt{Kind: AttemptRetry, Provider: "p", Model: "m"})
		if got.Index != i {
			t.Fatalf("attempt %d: Index = %d", i, got.Index)
		}
		if want := "req_1." + strconv.Itoa(i); got.AttemptID != want {
			t.Fatalf("attempt %d: AttemptID = %q, want %q", i, got.AttemptID, want)
		}
	}
	over := rc.AddAttempt(Attempt{Kind: AttemptFailover, Provider: "p", Model: "m"})
	if over.Index != 0 || over.AttemptID != "" {
		t.Errorf("attempt past the cap = (%d, %q), want (0, \"\")", over.Index, over.AttemptID)
	}
	snap := rc.AttemptsSnapshot()
	if len(snap) != MaxAttempts || rc.AttemptCount() != MaxAttempts {
		t.Fatalf("recorded %d attempts (count %d), want %d", len(snap), rc.AttemptCount(), MaxAttempts)
	}
	if snap[0].Kind != AttemptRetry || snap[MaxAttempts-1].Kind != AttemptRetry {
		t.Errorf("an attempt past the cap replaced a recorded one")
	}

	if !rc.UpdateAttempt("req_1.3", func(a *Attempt) { a.Outcome = OutcomeSuccess }) {
		t.Fatal("UpdateAttempt did not find req_1.3")
	}
	if rc.UpdateAttempt("req_1.17", func(*Attempt) {}) {
		t.Error("UpdateAttempt found an attempt that was never recorded")
	}
	if got := rc.AttemptsSnapshot()[2].Outcome; got != OutcomeSuccess {
		t.Errorf("updated outcome = %q", got)
	}
	// The snapshot is a copy: changing it does not change rc.
	snap[0].Model = "changed"
	if rc.AttemptsSnapshot()[0].Model != "m" {
		t.Error("AttemptsSnapshot shares its slice with rc")
	}
}

func TestAttemptMarshalsLogShape(t *testing.T) {
	a := Attempt{AttemptID: "r.1", Index: 1, Kind: AttemptPrimary, Provider: "openrouter", Model: "m",
		CredentialType: CredentialManaged, Outcome: OutcomeSuccess, StartedAt: time.Unix(1, 0).UTC()}
	b, err := json.Marshal(a)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"attempt_id", "index", "kind", "provider", "model", "credential_type", "outcome", "started_at"} {
		if _, ok := got[k]; !ok {
			t.Errorf("required key %q missing from %s", k, b)
		}
	}
	for _, k := range []string{"reason", "endpoint_id", "error_class", "latency_ms", "billable", "landed_cost_micros"} {
		if _, ok := got[k]; ok {
			t.Errorf("empty key %q not omitted: %s", k, b)
		}
	}
}

// v2State fills every v2 field of rc with a value that has a slice, map or
// pointer inside, so copies and resets can be checked.
func v2State(rc *RequestContext) {
	turn := 3
	budget := 2.5
	credits := int64(900)
	zdr := true
	rc.Agent = AgentContext{RunID: "run_1", Step: StepAct, Turn: &turn, BudgetUSD: &budget,
		Tags: []string{"a"}, AliasOverridden: []string{"x-thread-id"}, Require: []string{"tools"}}
	rc.Routing = RoutingOptions{
		Provider: &ProviderPrefs{Order: []string{"anthropic"}, ZDR: &zdr, Sort: &ProviderSort{By: "price"},
			MaxPrice: &ProviderMaxPrice{Prompt: &budget}, PreferredMaxLatency: &PercentileBound{P50: &budget}},
		Gateway:   &GatewayOptions{Tags: []string{"g"}, Models: []string{"m2"}},
		Models:    []string{"m1"},
		Plugins:   json.RawMessage(`[{"id":"web"}]`),
		SessionID: "sess_1",
	}
	rc.Billing = BillingState{Mode: BillingModeManaged, EndpointSet: []EndpointRef{{ID: "e1"}},
		ReservePrice: &PriceCard{Prompt: 1}, CallCapUSD: &budget, CreditsRemainingMicros: &credits}
	rc.Evidence = RoutingEvidence{PolicyVersion: "v3", PolicyVariant: "candidate"}
	rc.Privacy = Privacy{ContentMode: ContentModeNone, Source: PrivacySourceKey}
	rc.Runtime = &RuntimeIdentity{OrgID: "org_1", RootRunID: "run_1", Scopes: []string{ScopeGatewayChat},
		BudgetUSD: &budget, KeyPolicy: &RuntimeKeyPolicy{AllowedModels: []string{"m"}}}
	rc.ToolCallGuard = true
	rc.ToolCalls = ToolCallGuardResult{Checked: 2, Blocked: []string{"call_1"}}
	rc.CustomMetadataKeys = append(rc.CustomMetadataKeys, "team")
	rc.AddAttempt(Attempt{Kind: AttemptPrimary, Provider: "p", Model: "m"})
}

func TestReleaseClearsV2Fields(t *testing.T) {
	rc := AcquireRequestContext()
	rc.RequestID = "req_rel"
	v2State(rc)
	ran := 0
	rc.AddFinalizer(func() { ran++ })
	rc.SetResponseHeader("x-agentcc-test", "1")

	rc.Release()

	// rc is back in the pool; nothing else in this test can take it, so its
	// fields can be read here.
	if ran != 1 {
		t.Errorf("Release ran the pending finalizer %d times, want 1", ran)
	}
	if !reflect.DeepEqual(rc.Agent, AgentContext{}) || !reflect.DeepEqual(rc.Routing, RoutingOptions{}) ||
		!reflect.DeepEqual(rc.Billing, BillingState{}) || rc.Evidence != (RoutingEvidence{}) ||
		rc.Privacy != (Privacy{}) || rc.Runtime != nil || rc.ToolCallGuard ||
		!reflect.DeepEqual(rc.ToolCalls, ToolCallGuardResult{}) {
		t.Error("Release left v2 state set")
	}
	if rc.AttemptCount() != 0 || rc.ResponseHeaders() != nil {
		t.Errorf("Release left %d attempts and headers %v", rc.AttemptCount(), rc.ResponseHeaders())
	}
	// The attempts' backing array is kept for the next request, zeroed.
	if kept := rc.attempts[:cap(rc.attempts)]; len(kept) == 0 {
		t.Error("Release dropped the attempts' backing array; the next request allocates it again")
	} else if kept[0] != (Attempt{}) {
		t.Errorf("Release kept the previous request's attempt %+v", kept[0])
	}
	// The next request's finalizers run again: Release reset the once.
	rc.AddFinalizer(func() { ran++ })
	rc.RunFinalizers()
	if ran != 2 {
		t.Errorf("a finalizer after Release ran %d times in all, want 2", ran)
	}
}

func TestTraceSnapshotDeepCopiesV2(t *testing.T) {
	rc := AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req_snap"
	v2State(rc)
	snap := rc.Snapshot()
	// The same state built separately, so it shares nothing with rc.
	other := AcquireRequestContext()
	defer other.Release()
	other.RequestID = "req_snap"
	v2State(other)
	want := other.Snapshot()

	// Change everything reachable through rc's v2 fields.
	*rc.Agent.Turn = 99
	*rc.Agent.BudgetUSD = 99
	rc.Agent.Tags[0] = "changed"
	rc.Agent.AliasOverridden[0] = "changed"
	rc.Agent.Require[0] = "changed"
	rc.Routing.Provider.Order[0] = "changed"
	*rc.Routing.Provider.ZDR = false
	rc.Routing.Provider.Sort.By = "changed"
	*rc.Routing.Provider.PreferredMaxLatency.P50 = 99
	rc.Routing.Gateway.Tags[0] = "changed"
	rc.Routing.Models[0] = "changed"
	rc.Routing.Plugins[2] = 'X'
	rc.Billing.EndpointSet[0].ID = "changed"
	rc.Billing.ReservePrice.Prompt = 99
	*rc.Billing.CreditsRemainingMicros = 1
	rc.Runtime.Scopes[0] = "changed"
	rc.Runtime.KeyPolicy.AllowedModels[0] = "changed"
	rc.Runtime.OrgID = "changed"
	rc.ToolCalls.Blocked[0] = "changed"
	rc.CustomMetadataKeys[0] = "changed"
	rc.UpdateAttempt("req_snap.1", func(a *Attempt) { a.Model = "changed" })
	rc.AddAttempt(Attempt{Kind: AttemptRetry})

	if !reflect.DeepEqual(v2Of(snap), v2Of(want)) {
		t.Errorf("snapshot changed with rc:\n got %+v\nwant %+v", v2Of(snap), v2Of(want))
	}
	if len(snap.Attempts) != 1 || snap.Attempts[0].Model != "m" {
		t.Errorf("snapshot attempts = %+v", snap.Attempts)
	}
	if snap.Agent.RunID != "run_1" || snap.Privacy.ContentMode != ContentModeNone || !snap.ToolCallGuard {
		t.Errorf("snapshot lost v2 values: %+v", snap)
	}
}

// v2Of is the v2 part of a snapshot.
func v2Of(s *TraceSnapshot) []any {
	return []any{s.Agent, s.Routing, s.Billing, s.Evidence, s.Privacy, s.Runtime, s.ToolCallGuard, s.ToolCalls, s.Attempts, s.CustomMetadataKeys}
}

func TestFinalizersLIFOAndSurvivePanic(t *testing.T) {
	rc := AcquireRequestContext()
	defer rc.Release()
	var order []int
	rc.AddFinalizer(func() { order = append(order, 1) })
	rc.AddFinalizer(func() { panic("boom") })
	rc.AddFinalizer(func() { order = append(order, 3) })
	rc.RunFinalizers()
	rc.RunFinalizers()
	if !reflect.DeepEqual(order, []int{3, 1}) {
		t.Errorf("finalizers ran %v, want [3 1] once each", order)
	}
	// One added after they ran runs at once.
	rc.AddFinalizer(func() { order = append(order, 4) })
	if !reflect.DeepEqual(order, []int{3, 1, 4}) {
		t.Errorf("late finalizer: ran %v", order)
	}
}

func TestResponseHeaderBufferModel(t *testing.T) {
	rc := AcquireRequestContext()
	defer rc.Release()
	rc.SetResponseHeader("x-agentcc-a", "1")
	rc.SetResponseHeader("x-agentcc-b", "2")
	rc.SetResponseHeader("X-Agentcc-A", "3")
	got := rc.ResponseHeaders()
	want := []ResponseHeader{{"x-agentcc-a", "3"}, {"x-agentcc-b", "2"}}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("headers = %v, want %v", got, want)
	}
	h := http.Header{}
	rc.ApplyResponseHeaders(h)
	if h.Get("x-agentcc-a") != "3" || h.Get("x-agentcc-b") != "2" {
		t.Errorf("applied headers = %v", h)
	}
}

const rawPromptDetails = `{"cached_tokens":5,"audio_tokens":0,"future_field":{"x":[1,2]},"zeta":"keep"}`

func usageWithRaw() *Usage {
	p := json.RawMessage(rawPromptDetails)
	c := json.RawMessage(`{"reasoning_tokens":7,"accepted_prediction_tokens":2}`)
	return &Usage{PromptTokens: 10, CompletionTokens: 20, TotalTokens: 30, PromptTokensDetails: &p, CompletionTokensDetails: &c}
}

func TestUsageDetailsRawPreserved(t *testing.T) {
	u := usageWithRaw()
	u.ParseDetails()
	if u.PromptDetails.CachedTokens != 5 || u.CompletionDetails.ReasoningTokens != 7 {
		t.Fatalf("parsed details = %+v %+v", u.PromptDetails, u.CompletionDetails)
	}
	u.MergeDetails()
	if string(*u.PromptTokensDetails) != rawPromptDetails {
		t.Errorf("unchanged details were rewritten: %s", *u.PromptTokensDetails)
	}
	b, err := json.Marshal(u)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(b, []byte(`"prompt_tokens_details":`+rawPromptDetails)) {
		t.Errorf("marshalled usage lost the raw details: %s", b)
	}
	if bytes.Contains(b, []byte("PromptDetails")) || bytes.Contains(b, []byte("cache_write_tokens")) {
		t.Errorf("sidecars leaked into the JSON: %s", b)
	}
}

func TestUsageMergeDetailsKeepsUnknownSubfields(t *testing.T) {
	u := usageWithRaw()
	u.ParseDetails()
	u.PromptDetails.CacheWriteTokens = 64
	u.CompletionDetails.ReasoningTokens = 9
	u.PromptDetails.AudioTokens = 0 // zero never clears a raw value
	u.MergeDetails()

	var p map[string]any
	if err := json.Unmarshal(*u.PromptTokensDetails, &p); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"cached_tokens": 5.0, "cache_write_tokens": 64.0, "audio_tokens": 0.0,
		"future_field": map[string]any{"x": []any{1.0, 2.0}}, "zeta": "keep"}
	if !reflect.DeepEqual(p, want) {
		t.Errorf("prompt details = %v, want %v", p, want)
	}
	var c map[string]any
	if err := json.Unmarshal(*u.CompletionTokensDetails, &c); err != nil {
		t.Fatal(err)
	}
	if c["reasoning_tokens"] != 9.0 || c["accepted_prediction_tokens"] != 2.0 {
		t.Errorf("completion details = %v", c)
	}

	// No raw details yet: a sidecar value creates them.
	fresh := &Usage{PromptDetails: PromptTokensDetails{CachedTokens: 3}}
	fresh.MergeDetails()
	if fresh.PromptTokensDetails == nil || string(*fresh.PromptTokensDetails) != `{"cached_tokens":3}` {
		t.Errorf("created details = %v", fresh.PromptTokensDetails)
	}
	if fresh.CompletionTokensDetails != nil {
		t.Errorf("zero sidecars created completion details: %s", *fresh.CompletionTokensDetails)
	}
}

func TestUpstreamProviderDecoded(t *testing.T) {
	var resp ChatCompletionResponse
	body := `{"id":"gen-1","object":"chat.completion","model":"anthropic/claude","provider":"Anthropic","choices":[],` +
		`"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3,"cost":0.01,"is_byok":false,` +
		`"cost_details":{"upstream_inference_cost":0.009}}}`
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.UpstreamProvider != "Anthropic" {
		t.Errorf("response UpstreamProvider = %q", resp.UpstreamProvider)
	}
	if resp.Usage.IsByok == nil || *resp.Usage.IsByok || resp.Usage.CostDetails == nil ||
		*resp.Usage.CostDetails.UpstreamInferenceCost != 0.009 {
		t.Errorf("usage OpenRouter fields = %+v", resp.Usage)
	}
	var chunk StreamChunk
	if err := json.Unmarshal([]byte(`{"id":"gen-1","provider":"Google","choices":[]}`), &chunk); err != nil {
		t.Fatal(err)
	}
	if chunk.UpstreamProvider != "Google" {
		t.Errorf("chunk UpstreamProvider = %q", chunk.UpstreamProvider)
	}
	// A response without the field marshals without it.
	b, _ := json.Marshal(ChatCompletionResponse{ID: "x"})
	if bytes.Contains(b, []byte(`"provider"`)) {
		t.Errorf("empty provider marshalled: %s", b)
	}
}

func TestStreamMetadataNewFieldsOmitted(t *testing.T) {
	b, err := json.Marshal(AgentccStreamMetadata{Cost: 0.5, LatencyMs: 10})
	if err != nil {
		t.Fatal(err)
	}
	if string(b) != `{"cost":0.5,"latency_ms":10}` {
		t.Errorf("v1 stream metadata changed shape: %s", b)
	}
}

func TestPaymentMetaScopeAndReset(t *testing.T) {
	required, available := 0.04, 0.0
	resets := time.Date(2026, 10, 15, 10, 0, 0, 0, time.FixedZone("x", 2*3600))
	for _, tc := range []struct {
		name string
		meta PaymentMeta
		want string
	}{
		{"credits: no reset", PaymentMeta{LimitSource: LimitSourceCredits, RemedyHint: "add_credits",
			RequiredUSD: &required, AvailableUSD: &available, Scope: ScopeOrg, RetryAfterSec: 2},
			`{"limit_source":"agentcc_credits","remedy_hint":"add_credits","required_usd":0.04,"available_usd":0,"scope":"org","resets_at":null}`},
		{"key window resets", PaymentMeta{LimitSource: LimitSourceKeyLimit, RemedyHint: "wait", Scope: ScopeKey("k1"), ResetsAt: &resets},
			`{"limit_source":"agentcc_key_limit","remedy_hint":"wait","scope":"key:k1","resets_at":"2026-10-15T08:00:00Z"}`},
		{"run budget", PaymentMeta{LimitSource: LimitSourceAgentBudget, Scope: ScopeRun("run_R1")},
			`{"limit_source":"agentcc_agent_budget","scope":"run:run_R1","resets_at":null}`},
		{"billing unavailable: no limit", PaymentMeta{Reason: ReasonAccountStateMissing, RetryAfterSec: 30},
			`{"reason":"account_state_missing"}`},
	} {
		b, err := json.Marshal(tc.meta)
		if err != nil {
			t.Fatal(err)
		}
		if string(b) != tc.want {
			t.Errorf("%s:\n got %s\nwant %s", tc.name, b, tc.want)
		}
	}
	for _, s := range []string{ScopeOrg, ScopeKey("k"), ScopeKeyParallel("k"), ScopeTeam("t"), ScopeUser("u"), ScopeRun("r")} {
		if !regexp.MustCompile(`^(org|key:[^:]+(:parallel)?|team:.+|user:.+|run:.+)$`).MatchString(s) {
			t.Errorf("scope %q breaks the error-402-v1 pattern", s)
		}
	}
}

func TestErrorBuilders(t *testing.T) {
	if e := ErrIdempotencyReused("different body"); e.Status != http.StatusUnprocessableEntity || e.Code != "idempotency_key_reused" {
		t.Errorf("ErrIdempotencyReused = %+v", e)
	}
	e := ErrRoutingField("invalid_provider_preference", "provider.sortt")
	if e.Status != http.StatusBadRequest || e.Code != "invalid_provider_preference" || e.Param == nil || *e.Param != "provider.sortt" {
		t.Errorf("ErrRoutingField = %+v", e)
	}
}

// writeErr writes err the way every handler does, through
// WriteErrorFromError.
func writeErr(err error) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	WriteErrorFromError(w, err)
	return w
}

// errorMetadata is the error.metadata object of a written error body.
func errorMetadata(t *testing.T, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var body struct {
		Error map[string]any `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("body %q: %v", w.Body.String(), err)
	}
	meta, _ := body.Error["metadata"].(map[string]any)
	return meta
}

func TestErrPaymentRequiredWrites402Shape(t *testing.T) {
	// The golden error-402-v1: credits exhausted, no reset, no Retry-After.
	required, available := 0.04, 0.0
	w := writeErr(ErrPaymentRequired(ErrTypePaymentRequired, "managed_credits_exhausted",
		"Your organization has no managed credits left. Add credits or use your own provider keys.",
		PaymentMeta{LimitSource: LimitSourceCredits, RemedyHint: "add_credits", RequiredUSD: &required,
			AvailableUSD: &available, Scope: ScopeOrg}))
	if w.Code != http.StatusPaymentRequired || w.Header().Get("Retry-After") != "" {
		t.Errorf("credits 402: status %d, Retry-After %q", w.Code, w.Header().Get("Retry-After"))
	}
	golden, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples", "error-402-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	var got, want any
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(golden, &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("402 body:\n got %s\nwant %s", w.Body.Bytes(), golden)
	}

	// The in-flight 402 is the one that names Retry-After.
	w = writeErr(ErrPaymentRequired(ErrTypePaymentRequired, "managed_credits_insufficient", "credits are held by requests in flight",
		PaymentMeta{LimitSource: LimitSourceInFlight, RemedyHint: "retry", Scope: ScopeOrg, RetryAfterSec: 2}))
	meta := errorMetadata(t, w)
	if w.Code != http.StatusPaymentRequired || w.Header().Get("Retry-After") != "2" ||
		meta["limit_source"] != LimitSourceInFlight || meta["scope"] != "org" {
		t.Errorf("in-flight 402: status %d, Retry-After %q, metadata %v", w.Code, w.Header().Get("Retry-After"), meta)
	}
	if v, ok := meta["resets_at"]; !ok || v != nil {
		t.Errorf("in-flight 402: resets_at must be present and null, metadata %v", meta)
	}
	if _, inBody := meta["retry_after_sec"]; inBody {
		t.Errorf("Retry-After leaked into the body: %v", meta)
	}

	// A windowed limit writes its reset time and scope.
	resets := time.Date(2026, 10, 15, 10, 0, 0, 0, time.UTC)
	w = writeErr(ErrPaymentRequired(ErrTypePaymentRequired, "key_limit_exceeded", "key budget reached",
		PaymentMeta{LimitSource: LimitSourceKeyLimit, RemedyHint: "wait", Scope: ScopeKey("k1"), ResetsAt: &resets}))
	meta = errorMetadata(t, w)
	if meta["scope"] != "key:k1" || meta["resets_at"] != "2026-10-15T10:00:00Z" || w.Header().Get("Retry-After") != "" {
		t.Errorf("key-limit 402: Retry-After %q, metadata %v", w.Header().Get("Retry-After"), meta)
	}

	// The 429 and 503 rows carry the same metadata.
	w = writeErr(ErrLimit(http.StatusTooManyRequests, ErrTypeRateLimit, "managed_spend_velocity", "spend velocity cap",
		PaymentMeta{LimitSource: LimitSourceSpendGuard, Scope: ScopeOrg, ResetsAt: &resets}))
	if meta = errorMetadata(t, w); w.Code != http.StatusTooManyRequests || meta["limit_source"] != LimitSourceSpendGuard {
		t.Errorf("429: status %d, metadata %v", w.Code, meta)
	}
	w = writeErr(ErrLimit(http.StatusServiceUnavailable, ErrTypeServer, "managed_billing_unavailable", "billing is unavailable",
		PaymentMeta{Reason: ReasonAccountStateMissing, RetryAfterSec: 30}))
	if meta = errorMetadata(t, w); w.Code != http.StatusServiceUnavailable || w.Header().Get("Retry-After") != "30" ||
		!reflect.DeepEqual(meta, map[string]any{"reason": ReasonAccountStateMissing}) {
		t.Errorf("503: status %d, Retry-After %q, metadata %v", w.Code, w.Header().Get("Retry-After"), meta)
	}

	w = writeErr(ErrIdempotencyConflict("the first request with this key is still running"))
	if w.Code != http.StatusConflict || w.Header().Get("Retry-After") != "1" || errorMetadata(t, w) != nil {
		t.Errorf("409: status %d, Retry-After %q, body %s", w.Code, w.Header().Get("Retry-After"), w.Body.String())
	}
}

// Errors without metadata carry no metadata key and no Retry-After header.
func TestPlainErrorShape(t *testing.T) {
	param := "messages"
	for _, tc := range []struct {
		err  *APIError
		want string
	}{
		{ErrBadRequest("invalid_request", "bad input"),
			`{"error":{"message":"bad input","type":"invalid_request_error","param":null,"code":"invalid_request"}}` + "\n"},
		{&APIError{Status: http.StatusBadRequest, Type: ErrTypeInvalidRequest, Code: "missing", Message: "m", Param: &param},
			`{"error":{"message":"m","type":"invalid_request_error","param":"messages","code":"missing"}}` + "\n"},
		{ErrTooManyRequests("slow down"),
			`{"error":{"message":"slow down","type":"rate_limit_error","param":null,"code":"rate_limit_exceeded"}}` + "\n"},
	} {
		w := writeErr(tc.err)
		if w.Body.String() != tc.want || w.Header().Get("Retry-After") != "" || w.Code != tc.err.Status {
			t.Errorf("%s:\n got %d %q Retry-After %q\nwant %d %q", tc.err.Code, w.Code, w.Body.String(),
				w.Header().Get("Retry-After"), tc.err.Status, tc.want)
		}
	}
}

// SetLogEvidence stores JSON by name, the v2
// snapshot copies it, and Release clears it so a pooled context never carries
// a previous request's evidence.
func TestLogEvidenceSnapshotAndRelease(t *testing.T) {
	rc := AcquireRequestContext()
	if snap := rc.SnapshotV2(); snap.LogEvidence != nil {
		t.Fatalf("fresh context has evidence %v", snap.LogEvidence)
	}
	rc.SetLogEvidence("eval_export", "forbidden")
	snap := rc.SnapshotV2()
	rc.SetLogEvidence("cache", map[string]int{"cache_read_tokens": 1})
	if string(snap.LogEvidence["eval_export"]) != `"forbidden"` || len(snap.LogEvidence) != 1 {
		t.Fatalf("snapshot = %v, want only eval_export (a copy)", snap.LogEvidence)
	}
	rc.Release()
	rc = AcquireRequestContext()
	defer rc.Release()
	if rc.LogEvidence != nil {
		t.Fatalf("released context kept evidence %v", rc.LogEvidence)
	}
}
