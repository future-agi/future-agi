package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	outcomesplugin "github.com/futureagi/agentcc-gateway/internal/plugins/outcomes"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	feedbackKeyA = "sk-feedback-org-a"
	feedbackKeyB = "sk-feedback-org-b"
	feedbackOrgA = "org-feedback-a"
	feedbackOrgB = "org-feedback-b"
)

// Upstream bodies the signals gateway's upstream can answer with.
const (
	upstreamAnswered = `{"id":"c1","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1500,"completion_tokens":2,"total_tokens":1502}}`
	upstreamEmpty    = `{"id":"c2","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":""},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":0,"total_tokens":10}}`
	upstreamFiltered = `{"id":"c3","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":""},"finish_reason":"content_filter"}],"usage":{"prompt_tokens":10,"completion_tokens":0,"total_tokens":10}}`
)

// signalsNow is the signals gateway's outcome clock, stopped so counters
// compare exactly.
var signalsNow = time.Date(2026, 10, 16, 9, 0, 0, 0, time.UTC)

// outcomeEvents keeps what the gateway sent to its event sink.
type outcomeEvents struct {
	mu     sync.Mutex
	events []models.OutcomeEvent
}

func (e *outcomeEvents) sink(kind string, payload any) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if ev, ok := payload.(models.OutcomeEvent); ok && kind == models.EventKindOutcome {
		e.events = append(e.events, ev)
	}
}

func (e *outcomeEvents) all() []models.OutcomeEvent {
	e.mu.Lock()
	defer e.mu.Unlock()
	return slices.Clone(e.events)
}

// signalsCapture is a post-plugin after outcomes that keeps what the request
// log would read: the outcome class and the agent context.
type signalsCapture struct {
	mu    sync.Mutex
	class map[string]string // request id -> outcome_class, "" when none
	agent map[string]models.AgentContext
}

func (c *signalsCapture) Name() string  { return "signals-capture" }
func (c *signalsCapture) Priority() int { return 950 }
func (c *signalsCapture) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (c *signalsCapture) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.class[rc.RequestID] = rc.Metadata[outcomesplugin.MetadataOutcomeClass]
	c.agent[rc.RequestID] = rc.Agent.Clone()
	return pipeline.ResultContinue()
}

func (c *signalsCapture) classOf(id string) string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.class[id]
}

func (c *signalsCapture) agentOf(id string) models.AgentContext {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.agent[id]
}

// signalsGateway is a real server with the outcomes plugin wired the way
// cmd/agentcc wires it, over in-memory stores, and two orgs' keys.
type signalsGateway struct {
	srv     *Server
	out     *outcomes.Store
	aff     *affinity.Store
	events  *outcomeEvents
	capture *signalsCapture
	reply   atomic.Value // string: the upstream's next body
}

func newSignalsGateway(t *testing.T) *signalsGateway {
	t.Helper()
	g := &signalsGateway{
		out:     outcomes.NewStore(nil, 1000, func() time.Time { return signalsNow }), // no decay between calls
		aff:     affinity.NewStore(nil, 1000, nil),
		events:  &outcomeEvents{},
		capture: &signalsCapture{class: map[string]string{}, agent: map[string]models.AgentContext{}},
	}
	g.reply.Store(upstreamAnswered)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path != "/v1/chat/completions" {
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		_, _ = w.Write([]byte(g.reply.Load().(string)))
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "a", Key: feedbackKeyA, Owner: "t", KeyType: "internal", Metadata: map[string]string{"org_id": feedbackOrgA}},
		{Name: "b", Key: feedbackKeyB, Owner: "t", KeyType: "internal", Metadata: map[string]string{"org_id": feedbackOrgB}},
	}
	cfg.Providers["up"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	var srv *Server
	plugin := outcomesplugin.New(outcomesplugin.Deps{
		Affinity:  g.aff,
		Outcomes:  g.out,
		Health:    func() routing.ModelHealthReader { return srv.Handlers().Deps().ModelHealth },
		Events:    func() models.EventSink { return srv.Handlers().Deps().Events },
		OrgConfig: tenants.Get,
	})
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), plugin, g.capture)
	srv = New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	srv.Handlers().SetInjectedDeps(InjectedDeps{Events: g.events.sink})
	srv.SetOutcomeStore(g.out)
	g.srv = srv
	return g
}

// chat sends a chat request with key and headers (name, value pairs) and
// returns the response and its request id.
func (g *signalsGateway) chat(t *testing.T, key string, headers ...string) (*httptest.ResponseRecorder, string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("chat: HTTP %d %s", w.Code, w.Body.String())
	}
	return w, w.Header().Get("x-agentcc-request-id")
}

func (g *signalsGateway) feedback(t *testing.T, key, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/feedback", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func wantErrorCode(t *testing.T, w *httptest.ResponseRecorder, status int, code string) {
	t.Helper()
	var body models.ErrorResponse
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if w.Code != status || body.Error.Code != code {
		t.Errorf("HTTP %d %q, want %d %q: %s", w.Code, body.Error.Code, status, code, w.Body.String())
	}
}

func TestFeedbackOrgScoped(t *testing.T) {
	g := newSignalsGateway(t)
	_, genA := g.chat(t, feedbackKeyA, "x-agentcc-agent-id", "researcher", "x-agentcc-step", "act")
	if genA == "" {
		t.Fatal("no x-agentcc-request-id on the chat response")
	}
	verdict := fmt.Sprintf(`{"generation_id":%q,"outcome":"verify_failed"}`, genA)

	wantErrorCode(t, g.feedback(t, feedbackKeyB, verdict), http.StatusNotFound, "generation_not_found")
	wantErrorCode(t, g.feedback(t, feedbackKeyA, `{"generation_id":"req-unknown","outcome":"ok"}`), http.StatusNotFound, "generation_not_found")
	if n := len(g.events.all()); n != 0 {
		t.Fatalf("refused feedback emitted %d events", n)
	}
	key := outcomes.Key{Org: feedbackOrgA, Agent: "researcher", Step: models.StepAct, Model: "gpt-4o"}
	if c := g.out.Counters(context.Background(), key, outcomes.HalfLife(86400)); c.VerifyFailed != 0 {
		t.Fatalf("another org's feedback counted: %+v", c)
	}

	w := g.feedback(t, feedbackKeyA, verdict)
	if w.Code != http.StatusOK {
		t.Fatalf("own generation: HTTP %d %s", w.Code, w.Body.String())
	}
	var got feedbackResponse
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil || got.Data.GenerationID != genA || got.Data.Outcome != "verify_failed" || got.Data.EventID == "" {
		t.Errorf("response %s (%v)", w.Body.String(), err)
	}
	if c := g.out.Counters(context.Background(), key, outcomes.HalfLife(86400)); c.VerifyFailed != 1 {
		t.Errorf("verify_failed = %v, want 1", c.VerifyFailed)
	}
}

func TestFeedbackRejectsMalformedBodies(t *testing.T) {
	g := newSignalsGateway(t)
	for body, param := range map[string]string{
		`{"generation_id":"g","outcome":"great"}`:             "outcome",
		`{"generation_id":"g","outcome":"empty"}`:             "outcome",
		`{"outcome":"ok"}`:                                    "generation_id",
		`{"generation_id":"g","outcome":"ok","extra":1}`:      "",
		`{"generation_id":"g","outcome":"ok"} {"again":true}`: "",
		`[]`: "",
	} {
		w := g.feedback(t, feedbackKeyA, body)
		var e models.ErrorResponse
		_ = json.Unmarshal(w.Body.Bytes(), &e)
		gotParam := ""
		if e.Error.Param != nil {
			gotParam = *e.Error.Param
		}
		if w.Code != http.StatusBadRequest || e.Error.Code != "invalid_feedback" || gotParam != param {
			t.Errorf("%s: HTTP %d %q param %q, want 400 invalid_feedback param %q", body, w.Code, e.Error.Code, gotParam, param)
		}
	}
}

func TestFeedbackEmitsOutcomeEvent(t *testing.T) {
	g := newSignalsGateway(t)
	_, gen := g.chat(t, feedbackKeyA, "x-agentcc-agent-id", "researcher", "x-agentcc-run-id", "R2", "x-agentcc-step", "act")
	w := g.feedback(t, feedbackKeyA, fmt.Sprintf(`{"generation_id":%q,"outcome":"verify_failed","score":0.2}`, gen))
	if w.Code != http.StatusOK {
		t.Fatalf("HTTP %d %s", w.Code, w.Body.String())
	}
	events := g.events.all()
	if len(events) != 1 {
		t.Fatalf("events = %+v, want one", events)
	}
	ev := events[0]
	if ev.OrgID != feedbackOrgA || ev.GenerationID != gen || ev.Outcome != "verify_failed" || ev.Source != "feedback" ||
		ev.Score == nil || *ev.Score != 0.2 || ev.AgentID != "researcher" || ev.AgentRunID != "R2" || ev.AgentStep != models.StepAct || ev.Model != "gpt-4o" {
		t.Errorf("event = %+v", ev)
	}
	if want := outcomes.EventID(feedbackOrgA, gen, "feedback", time.UnixMilli(ev.TsMs)); ev.EventID != want {
		t.Errorf("event_id = %s, want %s", ev.EventID, want)
	}
	checkOutcomeEventSchema(t, ev)

	// A repeat is another event (the control plane keeps the latest), but
	// the generation counts against its model once.
	if w := g.feedback(t, feedbackKeyA, fmt.Sprintf(`{"generation_id":%q,"outcome":"bad"}`, gen)); w.Code != http.StatusOK {
		t.Fatalf("repeat: HTTP %d %s", w.Code, w.Body.String())
	}
	if n := len(g.events.all()); n != 2 {
		t.Errorf("events after a repeat = %d, want 2", n)
	}
	key := outcomes.Key{Org: feedbackOrgA, Agent: "researcher", Step: models.StepAct, Model: "gpt-4o"}
	if c := g.out.Counters(context.Background(), key, outcomes.HalfLife(86400)); c.VerifyFailed != 1 {
		t.Errorf("verify_failed after a repeat = %v, want 1", c.VerifyFailed)
	}
}

func TestPrevOutcomeEmitsOutcomeEvent(t *testing.T) {
	g := newSignalsGateway(t)
	session := []string{"x-agentcc-session-id", "S1", "x-agentcc-agent-id", "researcher", "x-agentcc-step", "act"}
	_, first := g.chat(t, feedbackKeyA, session...)
	_, second := g.chat(t, feedbackKeyA, append(slices.Clone(session), "x-agentcc-prev-outcome", "verify_failed")...)
	events := g.events.all()
	if len(events) != 1 {
		t.Fatalf("events = %+v, want one", events)
	}
	ev := events[0]
	if ev.GenerationID != first || ev.GenerationID == second || ev.Outcome != "verify_failed" || ev.Source != "prev_outcome" || ev.Score != nil {
		t.Errorf("event = %+v, want verify_failed on the first call %s", ev, first)
	}
	checkOutcomeEventSchema(t, ev)
}

func TestOutcomeClassStamped(t *testing.T) {
	g := newSignalsGateway(t)
	for reply, want := range map[string]string{upstreamAnswered: "ok", upstreamEmpty: "empty", upstreamFiltered: "content_filter"} {
		g.reply.Store(reply)
		// A caller cannot forge the gateway's key through its own metadata.
		_, id := g.chat(t, feedbackKeyA, "x-agentcc-metadata", `{"outcome_class":"forged"}`)
		if got := g.capture.classOf(id); got != want {
			t.Errorf("outcome_class = %q, want %q", got, want)
		}
	}
}

func TestAffinitySubjectFallsBackToRunId(t *testing.T) {
	g := newSignalsGateway(t)
	_, id := g.chat(t, feedbackKeyA, "x-agentcc-run-id", "R1", "x-agentcc-step", "act")
	if got := affinity.Subject(g.capture.agentOf(id)); got != "run:R1" {
		t.Fatalf("Subject = %q, want run:R1", got)
	}
	pin, ok := g.aff.Get(context.Background(), feedbackOrgA, "run:R1", affinity.ClassMain)
	if !ok || pin.Model != "gpt-4o" || pin.Provider != "up" || pin.RequestID != id || pin.PrefixTokens != 1500 {
		t.Fatalf("pin = %+v, %v; want gpt-4o on up for %s with 1500 prefix tokens", pin, ok, id)
	}
	_, id2 := g.chat(t, feedbackKeyA, "x-agentcc-run-id", "R1", "x-agentcc-session-id", "S9", "x-agentcc-step", "act")
	if pin, ok := g.aff.Get(context.Background(), feedbackOrgA, "S9", affinity.ClassMain); !ok || pin.RequestID != id2 {
		t.Errorf("with a session the pin is the session's: %+v, %v", pin, ok)
	}
}

// --- outcome-event-v1 ----------------------------------------------------------

// checkOutcomeEventSchema validates ev as the webhook batch carries it (the
// payload plus kind, as the flusher's EnqueueEvent adds it) against the
// outcome-event-v1 schema.
func checkOutcomeEventSchema(t *testing.T, ev models.OutcomeEvent) {
	t.Helper()
	raw, _ := json.Marshal(ev)
	var inst map[string]any
	_ = json.Unmarshal(raw, &inst)
	inst["kind"] = models.EventKindOutcome
	if errs := validateOutcomeEvent(outcomeEventSchema(t), inst, "event"); len(errs) > 0 {
		t.Errorf("event fails outcome-event-v1:\n%s\n%s", strings.Join(errs, "\n"), raw)
	}
}

func outcomeEventSchema(t *testing.T) map[string]any {
	t.Helper()
	_, here, _, _ := runtime.Caller(0)
	raw, err := os.ReadFile(filepath.Join(filepath.Dir(here), "..", "..", "..", "api_contracts", "gateway", "examples", "outcome-event-v1.schema.json"))
	if err != nil {
		t.Fatalf("reading outcome-event-v1.schema.json: %v", err)
	}
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatalf("parsing the schema: %v", err)
	}
	return schema
}

// validateOutcomeEvent checks inst against schema, covering the keywords
// outcome-event-v1 uses and failing on any other, so a schema change cannot
// pass unchecked.
func validateOutcomeEvent(schema map[string]any, inst any, path string) []string {
	var errs []string
	fail := func(format string, args ...any) { errs = append(errs, path+": "+fmt.Sprintf(format, args...)) }
	for k := range schema {
		switch k {
		case "$schema", "$id", "title", "description", "type", "additionalProperties", "required", "properties", "const", "enum", "minLength", "minimum":
		default:
			fail("unsupported schema keyword %q", k)
		}
	}
	if c, ok := schema["const"]; ok && inst != c {
		fail("%v is not %v", inst, c)
	}
	if enum, ok := schema["enum"].([]any); ok && !slices.Contains(enum, inst) {
		fail("%v is not one of %v", inst, enum)
	}
	switch schema["type"] {
	case "object":
		obj, ok := inst.(map[string]any)
		if !ok {
			fail("want an object, got %T", inst)
			return errs
		}
		props, _ := schema["properties"].(map[string]any)
		for _, r := range schema["required"].([]any) {
			if _, ok := obj[r.(string)]; !ok {
				fail("missing required %q", r)
			}
		}
		for k, v := range obj {
			sub, ok := props[k].(map[string]any)
			if !ok {
				if schema["additionalProperties"] == false {
					fail("unexpected property %q", k)
				}
				continue
			}
			errs = append(errs, validateOutcomeEvent(sub, v, path+"."+k)...)
		}
	case "string":
		s, ok := inst.(string)
		if !ok {
			fail("want a string, got %T", inst)
		} else if m, ok := schema["minLength"].(float64); ok && len(s) < int(m) {
			fail("shorter than %v", m)
		}
	case "integer":
		n, ok := inst.(float64)
		if !ok || n != float64(int64(n)) {
			fail("want an integer, got %v", inst)
		} else if m, ok := schema["minimum"].(float64); ok && n < m {
			fail("%v is below %v", n, m)
		}
	case "number":
		if _, ok := inst.(float64); !ok {
			fail("want a number, got %T", inst)
		}
	case nil:
		if _, ok := schema["const"]; !ok {
			fail("no type")
		}
	default:
		fail("unsupported type %v", schema["type"])
	}
	return errs
}

// The validator rejects what the schema forbids, so a pass means something.
func TestOutcomeEventSchemaValidatorRejects(t *testing.T) {
	schema := outcomeEventSchema(t)
	good := map[string]any{"kind": "outcome", "event_id": "e", "org_id": "o", "generation_id": "g", "outcome": "ok", "source": "feedback", "ts_ms": float64(1)}
	if errs := validateOutcomeEvent(schema, good, "good"); len(errs) > 0 {
		t.Fatalf("a valid event was rejected: %v", errs)
	}
	for name, edit := range map[string]func(map[string]any){
		"no event_id":   func(m map[string]any) { delete(m, "event_id") },
		"empty org":     func(m map[string]any) { m["org_id"] = "" },
		"bad outcome":   func(m map[string]any) { m["outcome"] = "great" },
		"bad source":    func(m map[string]any) { m["source"] = "classified" },
		"wrong kind":    func(m map[string]any) { m["kind"] = "log" },
		"content field": func(m map[string]any) { m["response"] = "text" },
		"bad step":      func(m map[string]any) { m["agent_step"] = "think" },
		"float ts":      func(m map[string]any) { m["ts_ms"] = 1.5 },
	} {
		bad := map[string]any{}
		for k, v := range good {
			bad[k] = v
		}
		edit(bad)
		if errs := validateOutcomeEvent(schema, bad, name); len(errs) == 0 {
			t.Errorf("%s: the validator accepted an invalid event", name)
		}
	}
}
