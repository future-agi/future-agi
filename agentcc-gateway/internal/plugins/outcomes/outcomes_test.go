package outcomes

import (
	"context"
	"encoding/json"
	"math"
	"slices"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const testOrg = "org-outcomes"

var bg = context.Background()

// frozenClock stops time, so counters compare exactly.
func frozenClock() time.Time { return time.Date(2026, 10, 16, 9, 0, 0, 0, time.UTC) }

type eventSink struct {
	mu     sync.Mutex
	events []models.OutcomeEvent
}

func (s *eventSink) sink(kind string, payload any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if kind == models.EventKindOutcome {
		s.events = append(s.events, payload.(models.OutcomeEvent))
	}
}

func (s *eventSink) all() []models.OutcomeEvent {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]models.OutcomeEvent(nil), s.events...)
}

type harness struct {
	plugin   *Plugin
	aff      *affinity.Store
	out      *outcomes.Store
	events   *eventSink
	catalog  *catalog.Store
	health   *routing.ModelHealth
	tenants  *tenant.Store
	halfLife time.Duration
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	h := &harness{
		aff:      affinity.NewStore(nil, 100, nil),
		out:      outcomes.NewStore(nil, 100, frozenClock), // no decay between calls
		events:   &eventSink{},
		catalog:  catalog.NewStore(),
		health:   routing.NewModelHealth(),
		tenants:  tenant.NewStore(),
		halfLife: outcomes.HalfLife(86400),
	}
	h.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: catalog.OriginLive, Models: map[string]*catalog.Model{
		"vendor/flaky": {ID: "vendor/flaky"}, "vendor/ok": {ID: "vendor/ok"},
	}})
	h.plugin = New(Deps{
		Affinity:  h.aff,
		Outcomes:  h.out,
		Catalog:   h.catalog,
		Health:    func() routing.ModelHealthReader { return h.health },
		Events:    func() models.EventSink { return h.events.sink },
		OrgConfig: h.tenants.Get,
	})
	return h
}

// call is one request as the post-plugins see it.
type call struct {
	id       string
	agent    models.AgentContext
	model    string
	outcome  models.AttemptOutcome
	errClass models.ErrorClass
	cred     models.CredentialType // the attempt's; "" for an attempt that carries none
	resp     *models.ChatCompletionResponse
	meta     map[string]string
}

func (h *harness) run(c call) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.RequestID = c.id
	rc.Metadata["org_id"] = testOrg
	for k, v := range c.meta {
		rc.Metadata[k] = v
	}
	rc.Agent = c.agent
	if c.model != "" {
		outcome := c.outcome
		if outcome == "" {
			outcome = models.OutcomeSuccess
		}
		origin := models.ErrorOrigin("")
		if c.errClass != "" {
			origin = models.OriginUpstream
		}
		rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "openrouter", Model: c.model, CredentialType: c.cred,
			Outcome: outcome, ErrorClass: c.errClass, ErrorOrigin: origin, LatencyMs: 120})
	}
	rc.Response = c.resp
	h.plugin.ProcessResponse(bg, rc)
	return rc
}

func answered(text string, completion int) *models.ChatCompletionResponse {
	raw, _ := json.Marshal(text)
	return &models.ChatCompletionResponse{
		Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: raw}, FinishReason: "stop"}},
		Usage:   &models.Usage{PromptTokens: 1500, CompletionTokens: completion},
	}
}

func filtered() *models.ChatCompletionResponse {
	r := answered("", 0)
	r.Choices[0].FinishReason = "content_filter"
	return r
}

func near(got, want float64) bool { return math.Abs(got-want) < 1e-9 }

// tagged is a, agent-tagged the way the parse hook tags it: any agent field
// that arrived by header, body, alias or runtime token sets IdentitySource.
func tagged(a models.AgentContext) models.AgentContext {
	a.IdentitySource = models.IdentitySourceHeader
	return a
}

func actAgent(session string) models.AgentContext {
	return tagged(models.AgentContext{AgentID: "researcher", RunID: "R1", SessionID: session, Step: models.StepAct})
}

func TestOutcomePluginPlacement(t *testing.T) {
	p := New(Deps{})
	if p.Priority() != 905 || p.Name() != "outcomes" {
		t.Errorf("plugin %s at %d, want outcomes at 905", p.Name(), p.Priority())
	}
	if _, parallel := any(p).(interface{ IsPostParallel() bool }); parallel {
		t.Error("the plugin writes rc.Metadata, so it must not run in the parallel post phase")
	}
}

func TestOutcomeEmptyAndFilteredResponsesClassified(t *testing.T) {
	h := newHarness(t)
	for _, c := range []struct {
		resp *models.ChatCompletionResponse
		want string
	}{
		{answered("hi", 4), "ok"},
		{answered("", 0), "empty"},
		{filtered(), "content_filter"},
	} {
		rc := h.run(call{id: "req-" + c.want, agent: actAgent(""), model: "vendor/ok", resp: c.resp})
		if got := rc.Metadata[MetadataOutcomeClass]; got != c.want {
			t.Errorf("outcome_class = %q, want %q", got, c.want)
		}
		if last, ok := h.out.Last(bg, testOrg, "req-"+c.want); !ok || string(last.Class) != c.want || last.Model != "vendor/ok" || last.Run != "R1" {
			t.Errorf("Last(%s) = %+v, %v", c.want, last, ok)
		}
		rc.Release()
	}
	st := h.out.Stats(bg, outcomes.Key{Org: testOrg, Agent: "researcher", Step: models.StepAct, Model: "vendor/ok"}, h.halfLife)
	if !near(st.N, 3) || !near(st.EmptyRate, 1.0/3) || !near(st.FilterRate, 1.0/3) || st.P50Ms == 0 {
		t.Errorf("Stats = %+v, want 3 samples, a third empty, a third filtered", st)
	}
}

// The gateway keeps a generation's Last record, which
// POST /v1/feedback and x-agentcc-prev-outcome judge, only for agent-tagged
// or managed traffic. Every classified response is still stamped on the
// request log and counted.
func TestOutcomeRemembersOnlyAgentTaggedOrManagedTraffic(t *testing.T) {
	for _, c := range []struct {
		name     string
		agent    models.AgentContext
		cred     models.CredentialType
		remember bool
	}{
		{"untagged byok", models.AgentContext{}, models.CredentialBYOK, false},
		{"untagged, no credential type", models.AgentContext{}, "", false},
		{"attribution only is not agent tagging", models.AgentContext{EndUser: "u-1", Tags: []string{"t"}}, models.CredentialBYOK, false},
		{"a body session id alone is not agent tagging", models.AgentContext{SessionID: "s-1"}, models.CredentialBYOK, false},
		{"agent-tagged byok", actAgent(""), models.CredentialBYOK, true},
		{"untagged managed", models.AgentContext{}, models.CredentialManaged, true},
		{"agent-tagged managed", actAgent(""), models.CredentialManaged, true},
	} {
		t.Run(c.name, func(t *testing.T) {
			h := newHarness(t)
			rc := h.run(call{id: "req-1", agent: c.agent, model: "vendor/ok", cred: c.cred, resp: answered("hi", 4)})
			defer rc.Release()
			if got := rc.Metadata[MetadataOutcomeClass]; got != "ok" {
				t.Errorf("outcome_class = %q, want ok on every classified response", got)
			}
			if _, ok := h.out.Last(bg, testOrg, "req-1"); ok != c.remember {
				t.Errorf("Last kept = %v, want %v", ok, c.remember)
			}
			k := outcomes.Key{Org: testOrg, Agent: c.agent.AgentID, Step: c.agent.Step, Model: "vendor/ok"}
			if st := h.out.Stats(bg, k, h.halfLife); !near(st.N, 1) {
				t.Errorf("Stats = %+v, want the response counted once", st)
			}
		})
	}

	// Feedback on an untagged BYOK generation finds nothing to judge.
	h := newHarness(t)
	h.run(call{id: "req-byok", model: "vendor/ok", cred: models.CredentialBYOK, resp: answered("hi", 4)}).Release()
	if h.out.Judge(bg, testOrg, "req-byok", outcomes.Last{Model: "vendor/ok"}, outcomes.VerdictVerifyFailed, h.halfLife) {
		t.Error("a verdict on an untagged BYOK generation was counted")
	}
}

func TestOutcomeErrorsCountedButNotClassified(t *testing.T) {
	h := newHarness(t)
	rc := h.run(call{id: "req-err", agent: actAgent(""), model: "vendor/ok", outcome: models.OutcomeError, errClass: models.ErrClassUpstream5xx})
	defer rc.Release()
	if _, ok := rc.Metadata[MetadataOutcomeClass]; ok {
		t.Error("a failed request was classified")
	}
	if _, ok := h.out.Last(bg, testOrg, "req-err"); ok {
		t.Error("a failed request left a Last record for feedback")
	}
	st := h.out.Stats(bg, outcomes.Key{Org: testOrg, Agent: "researcher", Step: models.StepAct, Model: "vendor/ok"}, h.halfLife)
	if !near(st.N, 1) || !near(st.ErrRate, 1) {
		t.Errorf("Stats = %+v, want one failed sample", st)
	}
	refused := h.run(call{id: "req-refused", model: "vendor/ok", outcome: models.OutcomeSkipped, errClass: models.ErrClassPolicy})
	defer refused.Release()
	if st := h.out.Stats(bg, outcomes.Key{Org: testOrg, Model: "vendor/ok"}, h.halfLife); st.N != 0 {
		t.Errorf("a refused hop counted against the model: %+v", st)
	}
}

func TestOutcomeCallerOutcomeClassScrubbed(t *testing.T) {
	h := newHarness(t)
	noCall := h.run(call{id: "req-1", meta: map[string]string{MetadataOutcomeClass: "ok"}})
	defer noCall.Release()
	unclassified := h.run(call{id: "req-2", model: "vendor/ok", resp: &models.ChatCompletionResponse{},
		meta: map[string]string{MetadataOutcomeClass: "ok"}})
	defer unclassified.Release()
	for _, rc := range []*models.RequestContext{noCall, unclassified} {
		if v, ok := rc.Metadata[MetadataOutcomeClass]; ok {
			t.Errorf("%s kept the caller's outcome_class %q", rc.RequestID, v)
		}
	}

	// A classified call keeps the gateway's class, and its name leaves the
	// caller's own keys (content mode none drops those).
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID, rc.Metadata["org_id"] = "req-3", testOrg
	rc.Metadata[MetadataOutcomeClass] = "forged"
	rc.CustomMetadataKeys = []string{"team", MetadataOutcomeClass}
	rc.AddAttempt(models.Attempt{Provider: "openrouter", Model: "vendor/ok", Outcome: models.OutcomeSuccess})
	rc.Response = answered("", 0)
	h.plugin.ProcessResponse(bg, rc)
	if got := rc.Metadata[MetadataOutcomeClass]; got != "empty" {
		t.Errorf("outcome_class = %q, want the gateway's empty", got)
	}
	if want := []string{"team"}; !slices.Equal(rc.CustomMetadataKeys, want) {
		t.Errorf("caller keys = %v, want %v", rc.CustomMetadataKeys, want)
	}
}

func TestOutcomeRecordsTheServedAttemptNotTheLast(t *testing.T) {
	h := newHarness(t)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID, rc.Metadata["org_id"] = "req-hedge", testOrg
	rc.Agent = actAgent("S")
	rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "openrouter", Model: "vendor/ok", Outcome: models.OutcomeSuccess, LatencyMs: 90})
	rc.AddAttempt(models.Attempt{Kind: models.AttemptHedge, Provider: "openrouter", Model: "vendor/flaky", Outcome: models.OutcomeDiscarded})
	rc.Response = answered("won", 4)
	h.plugin.ProcessResponse(bg, rc)
	if last, _ := h.out.Last(bg, testOrg, "req-hedge"); last.Model != "vendor/ok" {
		t.Errorf("Last model = %q, want the hedge winner vendor/ok", last.Model)
	}
	if pin, _ := h.aff.Get(bg, testOrg, "S", affinity.ClassMain); pin.Model != "vendor/ok" {
		t.Errorf("pinned %q, want the hedge winner vendor/ok", pin.Model)
	}
}

func TestOutcomeAffinityPinsTheServedModel(t *testing.T) {
	h := newHarness(t)
	runOnly := tagged(models.AgentContext{RunID: "R5", Step: models.StepPlan})
	rc := h.run(call{id: "req-1", agent: runOnly, model: "vendor/ok", resp: answered("plan", 9)})
	defer rc.Release()
	want := affinity.Value{Model: "vendor/ok", Provider: "openrouter", RequestID: "req-1", PrefixTokens: 1500}
	if got, ok := h.aff.Get(bg, testOrg, "run:R5", affinity.ClassMain); !ok || got != want {
		t.Fatalf("pin = %+v, %v; want %+v", got, ok, want)
	}
	disabled := false
	h.tenants.Set(testOrg, &tenant.OrgConfig{Routing: &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{
		Affinity: &tenant.AutoAffinityConfig{Enabled: &disabled}}}})
	rc2 := h.run(call{id: "req-2", agent: tagged(models.AgentContext{RunID: "R8", Step: models.StepPlan}), model: "vendor/ok", resp: answered("plan", 9)})
	defer rc2.Release()
	if v, ok := h.aff.Get(bg, testOrg, "run:R8", affinity.ClassMain); ok {
		t.Errorf("affinity off, yet the run was pinned: %+v", v)
	}
	other := h.run(call{id: "req-3", agent: tagged(models.AgentContext{SessionID: "s9", Step: models.StepOther}), model: "vendor/ok", resp: answered("x", 1)})
	defer other.Release()
	if _, ok := h.out.Prev(bg, testOrg, "s9", affinity.ClassMain); ok {
		t.Error("step other has no class, yet it became the session's previous call")
	}
}

func TestOutcomePrevOutcomeAttribution(t *testing.T) {
	h := newHarness(t)
	first := h.run(call{id: "req-1", agent: actAgent("S"), model: "vendor/ok", resp: answered("a", 5)})
	first.Release()
	// A verify call in the same session has its own class, so its header
	// cannot reach the act call.
	verify := tagged(models.AgentContext{AgentID: "checker", SessionID: "S", Step: models.StepVerify, PrevOutcome: outcomes.VerdictVerifyFailed})
	h.run(call{id: "req-v", agent: verify, model: "vendor/ok", resp: answered("v", 2)}).Release()
	if n := len(h.events.all()); n != 0 {
		t.Fatalf("a verify call's header was attributed across classes: %d events", n)
	}

	second := actAgent("S")
	second.PrevOutcome = outcomes.VerdictVerifyFailed
	h.run(call{id: "req-2", agent: second, model: "vendor/flaky", resp: answered("b", 5)}).Release()
	events := h.events.all()
	if len(events) != 1 {
		t.Fatalf("events = %+v, want one", events)
	}
	ev := events[0]
	if ev.GenerationID != "req-1" || ev.Outcome != "verify_failed" || ev.Source != "prev_outcome" || ev.OrgID != testOrg ||
		ev.Model != "vendor/ok" || ev.AgentID != "researcher" || ev.AgentRunID != "R1" || ev.AgentStep != models.StepAct ||
		ev.EventID != outcomes.EventID(testOrg, "req-1", "prev_outcome", time.UnixMilli(ev.TsMs)) {
		t.Errorf("event = %+v", ev)
	}
	firstKey := outcomes.Key{Org: testOrg, Agent: "researcher", Step: models.StepAct, Model: "vendor/ok"}
	if c := h.out.Counters(bg, firstKey, h.halfLife); !near(c.VerifyFailed, 1) {
		t.Errorf("the first call's verify_failed = %v, want 1", c.VerifyFailed)
	}

	// A retried request with the same header reports again but counts once;
	// the next header in the session names the second call.
	h.run(call{id: "req-2b", agent: second, model: "vendor/flaky", resp: answered("b", 5)}).Release()
	if c := h.out.Counters(bg, firstKey, h.halfLife); !near(c.VerifyFailed, 1) {
		t.Errorf("verify_failed after a repeat = %v, want 1", c.VerifyFailed)
	}
	third := actAgent("S")
	third.PrevOutcome = outcomes.VerdictEmpty
	h.run(call{id: "req-3", agent: third, model: "vendor/ok", resp: answered("c", 5)}).Release()
	events = h.events.all()
	if len(events) != 3 || events[2].GenerationID != "req-2b" || events[2].Outcome != "empty" {
		t.Fatalf("events = %+v, want the third to name req-2b as empty", events)
	}
	flakyKey := outcomes.Key{Org: testOrg, Agent: "researcher", Step: models.StepAct, Model: "vendor/flaky"}
	if c := h.out.Counters(bg, flakyKey, h.halfLife); !near(c.Empty, 1) {
		t.Errorf("the flaky call's empty = %v, want 1", c.Empty)
	}
	// ok attributes nothing.
	fourth := actAgent("S")
	fourth.PrevOutcome = outcomes.VerdictOK
	h.run(call{id: "req-4", agent: fourth, model: "vendor/ok", resp: answered("d", 5)}).Release()
	if n := len(h.events.all()); n != 3 {
		t.Errorf("prev-outcome ok emitted an event (%d events)", n)
	}
}

// A verdict reports on the session's previous request. When that request
// left no Last record, the verdict is dropped; it never lands on the older
// generation before it.
func TestOutcomePrevOutcomeNeverLandsOnAnOlderGeneration(t *testing.T) {
	for _, c := range []struct {
		name string
		prev call // the request the verdict is meant for
	}{
		{"failed", call{model: "vendor/flaky", outcome: models.OutcomeError, errClass: models.ErrClassUpstream5xx}},
		{"no upstream call", call{meta: map[string]string{"cache_status": "hit_exact"}}},
		{"unclassified", call{model: "vendor/flaky", resp: &models.ChatCompletionResponse{}}},
	} {
		t.Run(c.name, func(t *testing.T) {
			h := newHarness(t)
			h.run(call{id: "call-1", agent: actAgent("S1"), model: "vendor/ok", resp: answered("a", 5)}).Release()
			okKey := outcomes.Key{Org: testOrg, Agent: "researcher", Step: models.StepAct, Model: "vendor/ok"}
			before := h.out.Counters(bg, okKey, h.halfLife)

			prev := c.prev
			prev.id, prev.agent = "call-2", actAgent("S1")
			h.run(prev).Release()
			if id, _ := h.out.Prev(bg, testOrg, "S1", affinity.ClassMain); id != "call-2" {
				t.Fatalf("the session's previous request = %q, want call-2", id)
			}

			retry := actAgent("S1")
			retry.PrevOutcome = outcomes.VerdictRetried
			h.run(call{id: "call-3", agent: retry, model: "vendor/ok", resp: answered("c", 5)}).Release()
			if events := h.events.all(); len(events) != 0 {
				t.Errorf("events = %+v, want none: call-2 left no generation to judge", events)
			}
			after := h.out.Counters(bg, okKey, h.halfLife)
			if !near(after.Empty, before.Empty) || !near(after.N, before.N+1) {
				t.Errorf("vendor/ok counters went from %+v to %+v; want one more answered sample and nothing else", before, after)
			}
			if last, _ := h.out.Last(bg, testOrg, "call-1"); last.Judged {
				t.Error("call-1 was judged for a verdict meant for call-2")
			}
		})
	}
}

// A request that made no upstream call (here a cache hit) still carries the
// caller's verdict on the session's previous request.
func TestOutcomePrevOutcomeReportedOnARequestWithoutAnUpstreamCall(t *testing.T) {
	h := newHarness(t)
	h.run(call{id: "call-1", agent: actAgent("S1"), model: "vendor/ok", resp: answered("a", 5)}).Release()
	hit := actAgent("S1")
	hit.PrevOutcome = outcomes.VerdictVerifyFailed
	h.run(call{id: "call-2", agent: hit, meta: map[string]string{"cache_status": "hit_exact"}}).Release()
	events := h.events.all()
	if len(events) != 1 || events[0].GenerationID != "call-1" || events[0].Outcome != "verify_failed" {
		t.Fatalf("events = %+v, want one verify_failed naming call-1", events)
	}
	okKey := outcomes.Key{Org: testOrg, Agent: "researcher", Step: models.StepAct, Model: "vendor/ok"}
	if c := h.out.Counters(bg, okKey, h.halfLife); !near(c.VerifyFailed, 1) || !near(c.N, 1) {
		t.Errorf("vendor/ok counters = %+v, want one sample with verify_failed 1", c)
	}
	if id, _ := h.out.Prev(bg, testOrg, "S1", affinity.ClassMain); id != "call-2" {
		t.Errorf("the session's previous request = %q, want call-2", id)
	}
}

// BenchmarkOutcomeProcessResponse is the plugin's per-request cost for an
// agent call on in-memory stores: classify, count, remember, point and pin.
func BenchmarkOutcomeProcessResponse(b *testing.B) {
	h := &harness{
		aff: affinity.NewStore(nil, 1000, nil), out: outcomes.NewStore(nil, 1000, nil), events: &eventSink{},
		catalog: catalog.NewStore(), health: routing.NewModelHealth(), tenants: tenant.NewStore(),
	}
	p := New(Deps{Affinity: h.aff, Outcomes: h.out, Catalog: h.catalog, OrgConfig: h.tenants.Get,
		Health: func() routing.ModelHealthReader { return h.health }, Events: func() models.EventSink { return h.events.sink }})
	resp := answered("ok", 4)
	b.ReportAllocs()
	for i := 0; b.Loop(); i++ {
		rc := models.AcquireRequestContext()
		rc.RequestID = "req-bench"
		rc.Metadata["org_id"] = testOrg
		rc.Agent = actAgent("S")
		rc.AddAttempt(models.Attempt{Provider: "openrouter", Model: "vendor/ok", Outcome: models.OutcomeSuccess, LatencyMs: 100})
		rc.Response = resp
		p.ProcessResponse(bg, rc)
		rc.Release()
	}
}
