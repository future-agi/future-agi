package server

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An Assistants run spends tokens upstream, and its usage is known only when
// it finishes: on the stream that started it, or on a later poll. Its tokens
// count once, into the budgets of the key that started it (and its org), into
// spend (the request log) and, for a key with its own prepaid allowance, into
// that allowance, as a chat call on the key does; never again on a later poll.

const (
	runOrg        = "org-run-charges"
	runCreatorKey = "sk-run-creator"
	runPollerKey  = "sk-run-poller"
	runQueuedJSON = `{"id":"run_1","object":"thread.run","status":"queued","model":"gpt-4o","usage":null}`
	runDoneJSON   = `{"id":"run_1","object":"thread.run","status":"completed","model":"gpt-4o","usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}`
	runChatJSON   = `{"id":"chatcmpl-1","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}`
)

// A run still in progress, as a poll reads it; and two keys of the creator's
// org a poll is refused to.
const (
	runRunningJSON      = `{"id":"run_1","object":"thread.run","status":"in_progress","model":"gpt-4o","usage":null}`
	runExpiredKey       = "sk-run-expired"
	runOtherProviderKey = "sk-run-other-provider"
)

// runUpstream answers as OpenAI's Assistants API does: a run is queued when
// created, and complete, with its usage, when polled; a streamed run finishes
// on its stream. A chat completion is answered with the same 15 tokens a run
// spends.
func runUpstream(t *testing.T) *httptest.Server {
	t.Helper()
	return runUpstreamAfter(t, 0, new(atomic.Int32))
}

// runUpstreamAfter is runUpstream with a run that is still in progress on
// its first pending polls, and complete from the next one on; polls counts
// every poll it answers. A poll's answer carries OpenAI's own headers.
func runUpstreamAfter(t *testing.T, pending int32, polls *atomic.Int32) *httptest.Server {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch {
		case strings.HasSuffix(r.URL.Path, "/chat/completions"):
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, runChatJSON)
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/runs/run_1"):
			w.Header().Set("openai-version", "2020-10-01")
			w.Header().Set("openai-processing-ms", "7")
			w.Header().Set("x-request-id", "req_upstream_poll")
			if polls.Add(1) <= pending {
				io.WriteString(w, runRunningJSON)
				return
			}
			io.WriteString(w, runDoneJSON)
		case strings.Contains(string(body), `"stream":true`):
			w.Header().Set("Content-Type", "text/event-stream")
			w.WriteHeader(http.StatusOK)
			io.WriteString(w, "event: thread.run.created\ndata: "+runQueuedJSON+"\n\n")
			w.(http.Flusher).Flush()
			io.WriteString(w, "event: thread.run.completed\ndata: "+runDoneJSON+"\n\nevent: done\ndata: [DONE]\n\n")
		case strings.HasSuffix(r.URL.Path, "/runs"):
			io.WriteString(w, runQueuedJSON)
		default:
			io.WriteString(w, `{"data":[]}`)
		}
	}))
	t.Cleanup(ts.Close)
	return ts
}

type runGateway struct {
	// polls counts the polls the upstream answered.
	polls   *atomic.Int32
	srv     *Server
	tracker *budgetpkg.Tracker
	keys    *auth.KeyStore
	sink    *r7LogSink
	flusher *loggingplugin.LogFlusher
}

// newRunGateway serves two keys of one org, "creator" and "poller", on the
// org's own OpenAI key (the config file's "openai" entry gives only the
// upstream URL). creatorType is the creator key's key_type ("byok" or
// "managed", a config-file key with its own prepaid allowance of $10). The
// pipeline prices (cost), counts budgets (budget, with an org and a per-key
// limit), draws a prepaid allowance (credits) and logs.
func newRunGateway(t *testing.T, creatorType string) *runGateway {
	t.Helper()
	return newRunGatewayWith(t, creatorType, "byok", runOrg)
}

// newRunGatewayWith is newRunGateway with the poller key's key_type (a
// managed poller has its own prepaid allowance of $10 too) and org, which
// has its own OpenAI key as the creator's org does.
func newRunGatewayWith(t *testing.T, creatorType, pollerType, pollerOrg string) *runGateway {
	t.Helper()
	return newRunGatewayOn(t, 0, creatorType, pollerType, pollerOrg)
}

// newRunGatewayPending is newRunGateway (a byok creator) whose run is still
// in progress on its first pending polls.
func newRunGatewayPending(t *testing.T, pending int32) *runGateway {
	t.Helper()
	return newRunGatewayOn(t, pending, "byok", "byok", runOrg)
}

// newRunGatewayOn serves the creator and the poller, and two keys of the
// creator's org a poll is refused to: an expired one, and one that may use
// only another provider.
func newRunGatewayOn(t *testing.T, pending int32, creatorType, pollerType, pollerOrg string) *runGateway {
	t.Helper()
	polls := new(atomic.Int32)
	up := runUpstreamAfter(t, pending, polls)
	sink := startR7LogSink(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "creator", Key: runCreatorKey, Owner: "t", KeyType: creatorType, CreditBalance: 10, Metadata: map[string]string{"org_id": runOrg}},
		{Name: "poller", Key: runPollerKey, Owner: "t", KeyType: pollerType, CreditBalance: 10, Metadata: map[string]string{"org_id": pollerOrg}},
		{Name: "expired", Key: runExpiredKey, Owner: "t", KeyType: "byok", ExpiresAt: "2001-01-01T00:00:00Z", Metadata: map[string]string{"org_id": runOrg}},
		{Name: "other-provider", Key: runOtherProviderKey, Owner: "t", KeyType: "byok", Providers: []string{"anthropic"}, Metadata: map[string]string{"org_id": runOrg}},
	}
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL: up.URL, APIKey: "config-file-key", APIFormat: "openai",
		Models: []string{"gpt-4o"}, AutoDiscover: &off,
	}
	cfg.Budgets = config.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		Org:           &config.BudgetLevelConfig{Limit: 1000},
		Keys: map[string]config.BudgetLevelConfig{
			"creator": {Limit: 1000},
			"poller":  {Limit: 1000},
		},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	tenants := tenant.NewStore()
	for _, org := range []string{runOrg, pollerOrg} {
		tenants.Set(org, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"openai": {APIKey: "org-key", Enabled: true, Models: []string{"gpt-4o"}},
		}})
	}
	ks := auth.NewKeyStore(cfg.Auth)
	mdb := testModelDBPtr()
	tracker := budgetpkg.NewTracker(cfg.Budgets)
	flusher := loggingplugin.NewLogFlusher(sink.URL, "", time.Hour, 100)
	logPlugin := loggingplugin.New(config.RequestLoggingConfig{Enabled: true}, tenants)
	logPlugin.SetFlusher(flusher)
	engine := pipeline.NewEngine(
		authplugin.New(ks, true),
		costplugin.New(true, mdb.Load, tenants, nil),
		budgetplugin.New(tracker, nil, true, tenants),
		creditsplugin.New(true, ks),
		logPlugin,
	)
	srv := New(cfg, "", registry, engine, ks, nil, nil, nil, mdb, tenants, nil)
	releaseTestServer(t, srv, logPlugin.Close)
	srv.ready.Store(true)
	return &runGateway{polls: polls, srv: srv, tracker: tracker, keys: ks, sink: sink, flusher: flusher}
}

func (g *runGateway) call(t *testing.T, method, path, key, body string) (int, string) {
	t.Helper()
	w := g.answer(t, method, path, key, body)
	return w.Code, w.Body.String()
}

// answer makes a call and returns the gateway's whole answer, its headers
// too.
func (g *runGateway) answer(t *testing.T, method, path, key, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func (g *runGateway) spend(level, key string) float64 {
	total, _ := g.tracker.GetSpend(level, key)
	return total
}

// runEntries are the logged entries that carry the run's 15 tokens.
func (g *runGateway) runEntries(t *testing.T) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, e := range g.entries(t) {
		if total, _ := e["total_tokens"].(float64); total == 15 {
			out = append(out, e)
		}
	}
	return out
}

// entries are every request log entry the gateway wrote. Each is one
// metered gateway request where the log is ingested. Call it once, after the
// calls: it flushes and closes the log.
func (g *runGateway) entries(t *testing.T) []map[string]any {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	g.flusher.Close(ctx)
	g.sink.mu.Lock()
	defer g.sink.mu.Unlock()
	return append([]map[string]any(nil), g.sink.entries...)
}

func assertRunCountedOnce(t *testing.T, g *runGateway, entries []map[string]any) {
	t.Helper()
	creator := g.spend("key", "creator")
	if creator <= 0 {
		t.Fatalf("the creator key's budget spend = %v, want the run's cost", creator)
	}
	if org := g.spend("org", ""); org != creator {
		t.Errorf("org budget spend = %v, want the run's cost %v, counted once", org, creator)
	}
	if poller := g.spend("key", "poller"); poller != 0 {
		t.Errorf("poller key spend = %v, want 0: the run is the creator's", poller)
	}
	if len(entries) != 1 {
		t.Fatalf("request log entries with the run's 15 tokens = %d, want 1", len(entries))
	}
	if cost, _ := entries[0]["cost"].(float64); cost <= 0 {
		t.Errorf("the run's log entry has cost %v, want its cost", entries[0]["cost"])
	}
}

// Created without a stream and then polled: counted once, on the first poll
// that sees it finished, and to the key that started it.
func TestAssistantsRunCountsOnceWhenAPollSeesItFinish(t *testing.T) {
	g := newRunGateway(t, "byok")
	if code, body := g.call(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1"}`); code != http.StatusOK {
		t.Fatalf("create run: %d %s", code, body)
	}
	if s := g.spend("key", "creator"); s != 0 {
		t.Fatalf("counted %v before the run finished", s)
	}
	for range 3 {
		if code, body := g.call(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, ""); code != http.StatusOK {
			t.Fatalf("poll: %d %s", code, body)
		}
	}
	assertRunCountedOnce(t, g, g.runEntries(t))
}

// A run that finishes on its own stream is counted there; a later poll does
// not count it again.
func TestAssistantsRunCountsOnTheStreamItFinishesOn(t *testing.T) {
	g := newRunGateway(t, "byok")
	code, body := g.call(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1","stream":true}`)
	if code != http.StatusOK || !strings.Contains(body, "thread.run.completed") {
		t.Fatalf("stream: %d %s", code, body)
	}
	g.call(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, "")
	assertRunCountedOnce(t, g, g.runEntries(t))
}

// A key with its own prepaid allowance pays for a run as it pays for a chat
// call: the run's priced cost, drawn once from the key that started it, when a
// poll sees the run finish. A second poll draws nothing.
func TestAssistantsRunDrawsTheKeysAllowanceOnce(t *testing.T) {
	g := newRunGateway(t, "managed")
	key := g.keys.Authenticate(runCreatorKey)
	if key == nil {
		t.Fatal("creator key not found")
	}
	before := key.BalanceMicros()
	if code, body := g.call(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1"}`); code != http.StatusOK {
		t.Fatalf("create run: %d %s", code, body)
	}
	if drawn := before - key.BalanceMicros(); drawn != 0 {
		t.Fatalf("drew %d micros before the run finished", drawn)
	}
	g.call(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, "")
	drawn := before - key.BalanceMicros()
	g.call(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, "")
	if again := before - key.BalanceMicros() - drawn; again != 0 {
		t.Errorf("a second poll drew %d micros more", again)
	}
	entries := g.runEntries(t)
	if len(entries) != 1 {
		t.Fatalf("request log entries with the run's 15 tokens = %d, want 1", len(entries))
	}
	cost, _ := entries[0]["cost"].(float64)
	if want := auth.USDToMicros(cost); drawn <= 0 || drawn != want {
		t.Errorf("the run drew %d micros from the key's allowance, want its cost, %d micros", drawn, want)
	}
}

// Chat and Assistants on the org's own OpenAI key are paid alike, as on dev: a
// key with its own prepaid allowance draws each call's priced cost once, a
// chat completion when it answers and a run when it finishes. The same 15
// gpt-4o tokens draw the same amount either way, and both are logged.
func TestChatAndAssistantsOnTheOrgsOwnKeyDrawTheAllowanceAlike(t *testing.T) {
	g := newRunGateway(t, "managed")
	key := g.keys.Authenticate(runCreatorKey)
	if key == nil {
		t.Fatal("creator key not found")
	}
	start := key.BalanceMicros()
	if code, body := g.call(t, http.MethodPost, "/v1/chat/completions", runCreatorKey, `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`); code != http.StatusOK {
		t.Fatalf("chat: %d %s", code, body)
	}
	chat := start - key.BalanceMicros()
	if code, body := g.call(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1","stream":true}`); code != http.StatusOK {
		t.Fatalf("run: %d %s", code, body)
	}
	run := start - key.BalanceMicros() - chat
	if chat <= 0 {
		t.Fatalf("a chat call on the org's own key drew %d micros from the key's allowance, want its cost", chat)
	}
	if run != chat {
		t.Errorf("an Assistants run drew %d micros and a chat call with the same tokens drew %d: the two are paid alike", run, chat)
	}
	if entries := g.runEntries(t); len(entries) != 2 {
		t.Errorf("request log entries with 15 tokens = %d, want 2 (the chat call and the run)", len(entries))
	}
}

// A run that finishes on a later call is charged on that call's own request
// log entry, as on dev: the gateway writes no entry of its own for the run.
// Each entry is one metered gateway request where the log is ingested, so a
// run must not add one.

// requestIDs are the gateway's request ids of the given answers.
func requestIDs(answers ...*httptest.ResponseRecorder) []string {
	var ids []string
	for _, w := range answers {
		ids = append(ids, w.Header().Get("x-agentcc-request-id"))
	}
	return ids
}

// entryFor is the entry logged for the request id, or nil.
func entryFor(entries []map[string]any, requestID string) map[string]any {
	for _, e := range entries {
		if e["request_id"] == requestID {
			return e
		}
	}
	return nil
}

// Created without a stream, polled while it runs, and finished on a later
// poll: two entries, the creating call's and the finishing poll's, as dev
// (cd292323) writes for the same calls. A poll that does not see the run
// finish stays outside the pipeline and writes none; the first poll that sees
// it finish writes one, which carries the run; a poll after that writes none.
func TestAssistantsRunFinishingOnALaterCallWritesDevsRowCount(t *testing.T) {
	const running = 3
	g := newRunGatewayPending(t, running)
	create := g.answer(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1"}`)
	if create.Code != http.StatusOK {
		t.Fatalf("create run: %d %s", create.Code, create.Body.String())
	}
	poll := func() *httptest.ResponseRecorder {
		w := g.answer(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, "")
		if w.Code != http.StatusOK {
			t.Fatalf("poll: %d %s", w.Code, w.Body.String())
		}
		return w
	}
	var unfinished, after []*httptest.ResponseRecorder
	for range running {
		unfinished = append(unfinished, poll())
	}
	finishing := poll()
	for range 2 {
		after = append(after, poll())
	}
	if !strings.Contains(finishing.Body.String(), `"completed"`) || strings.Contains(unfinished[running-1].Body.String(), `"completed"`) {
		t.Fatalf("the run did not finish on poll %d", running+1)
	}

	entries := g.entries(t)
	for _, id := range requestIDs(unfinished...) {
		if entryFor(entries, id) != nil {
			t.Errorf("a poll of the unfinished run (%q) wrote a request log entry, want none", id)
		}
	}
	for _, id := range requestIDs(after...) {
		if entryFor(entries, id) != nil {
			t.Errorf("a poll after the run finished (%q) wrote a request log entry, want none", id)
		}
	}
	if e := entryFor(entries, finishing.Header().Get("x-agentcc-request-id")); e == nil {
		t.Error("the poll that saw the run finish wrote no entry, want one carrying the run")
	} else if total, _ := e["total_tokens"].(float64); total != 15 {
		t.Errorf("the finishing poll's entry has %v tokens, want the run's 15", e["total_tokens"])
	}
	const devRows = 2 // cd292323: the creating call's entry and the finishing poll's
	if len(entries) != devRows {
		t.Errorf("request log entries = %d, want %d as on dev: polls that do not see the run finish write none", len(entries), devRows)
	}
}

// A poll of an unfinished run, outside the pipeline, is answered as it was in
// it: the upstream's status and body as they came, the upstream's headers,
// and the gateway's own request id, provider and billing mode.
func TestAssistantsPollAnswerIsUnchanged(t *testing.T) {
	g := newRunGatewayPending(t, 1)
	if code, body := g.call(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1"}`); code != http.StatusOK {
		t.Fatalf("create run: %d %s", code, body)
	}
	w := g.answer(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, "")
	if w.Code != http.StatusOK || w.Body.String() != runRunningJSON {
		t.Fatalf("poll answered %d %q, want 200 and the upstream's body as it came", w.Code, w.Body.String())
	}
	want := map[string]string{
		"Content-Type":           "application/json",
		"openai-version":         "2020-10-01",
		"openai-processing-ms":   "7",
		"x-request-id":           "req_upstream_poll",
		"x-agentcc-provider":     "openai",
		"x-agentcc-billing-mode": "byok",
	}
	for name, value := range want {
		if got := w.Header().Get(name); got != value {
			t.Errorf("poll answer header %s = %q, want %q", name, got, value)
		}
	}
	if w.Header().Get("x-agentcc-request-id") == "" {
		t.Error("poll answer has no x-agentcc-request-id")
	}
}

// A poll from a key that may not make it is refused as before, and the
// upstream is never asked: an expired key, and a key that may use only
// another provider.
func TestAssistantsPollIsStillRefusedToAKeyThatMayNotMakeIt(t *testing.T) {
	g := newRunGatewayPending(t, 1)
	for _, tc := range []struct {
		name, key string
		status    int
	}{
		{"expired key", runExpiredKey, http.StatusUnauthorized},
		{"unknown key", "sk-run-unknown", http.StatusUnauthorized},
		{"key for another provider", runOtherProviderKey, http.StatusForbidden},
	} {
		w := g.answer(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", tc.key, "")
		if w.Code != tc.status || strings.Contains(w.Body.String(), "run_1") {
			t.Errorf("%s: poll answered %d %s, want %d without the run", tc.name, w.Code, w.Body.String(), tc.status)
		}
	}
	if n := g.polls.Load(); n != 0 {
		t.Errorf("the upstream was polled %d times for keys that may not poll", n)
	}
}

// A run that a poll sees finish is charged on the poll's own entry: every
// entry is one a caller was answered with (no new request id), the polls
// after it write none, and the run's tokens and cost ride the first poll's
// entry.
func TestAssistantsRunFinishingOnAPollAddsNoEntryOfItsOwn(t *testing.T) {
	g := newRunGateway(t, "byok")
	create := g.answer(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1"}`)
	if create.Code != http.StatusOK {
		t.Fatalf("create run: %d %s", create.Code, create.Body.String())
	}
	answers := []*httptest.ResponseRecorder{create}
	for range 3 {
		poll := g.answer(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, "")
		if poll.Code != http.StatusOK {
			t.Fatalf("poll: %d %s", poll.Code, poll.Body.String())
		}
		answers = append(answers, poll)
	}
	ids := requestIDs(answers...)
	entries := g.entries(t)

	answered := make(map[string]bool, len(ids))
	for _, id := range ids {
		if id == "" || answered[id] {
			t.Fatalf("request ids answered = %v, want one of its own per call", ids)
		}
		answered[id] = true
	}
	for _, e := range entries {
		if id, _ := e["request_id"].(string); !answered[id] {
			t.Errorf("an entry with request id %q, which no call was answered with: the run was logged as a request of its own", id)
		}
	}
	if len(entries) != 2 {
		t.Errorf("request log entries = %d, want 2: the creating call's and the first poll's; a poll after the run finished writes none", len(entries))
	}
	poll := entryFor(entries, ids[1])
	if poll == nil {
		t.Fatalf("no entry for the first poll %q", ids[1])
	}
	if total, _ := poll["total_tokens"].(float64); total != 15 {
		t.Errorf("the first poll's entry has %v tokens, want the run's 15", poll["total_tokens"])
	}
	if cost, _ := poll["cost"].(float64); cost <= 0 {
		t.Errorf("the first poll's entry has cost %v, want the run's cost", poll["cost"])
	}
	for _, id := range []string{ids[0], ids[2], ids[3]} {
		if e := entryFor(entries, id); e != nil {
			if total, _ := e["total_tokens"].(float64); total != 0 {
				t.Errorf("entry %q has %v tokens, want 0: the run is charged once, on the first poll", id, total)
			}
		}
	}
}

// The entry a poll carries the run's charge on is the starter's: its key and
// org, not the poller's, when the poller is another key of another org. The
// poller's key spends nothing.
func TestAssistantsRunChargeOnAPollCarriesTheStartersKeyAndOrg(t *testing.T) {
	const pollerOrg = "org-run-poller"
	g := newRunGatewayWith(t, "byok", "byok", pollerOrg)
	creator, poller := g.keys.Authenticate(runCreatorKey), g.keys.Authenticate(runPollerKey)
	if creator == nil || poller == nil {
		t.Fatal("keys not found")
	}
	if code, body := g.call(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1"}`); code != http.StatusOK {
		t.Fatalf("create run: %d %s", code, body)
	}
	poll := g.answer(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, "")
	if poll.Code != http.StatusOK {
		t.Fatalf("poll: %d %s", poll.Code, poll.Body.String())
	}
	if s := g.spend("key", "creator"); s <= 0 {
		t.Errorf("the creator key's budget spend = %v, want the run's cost", s)
	}
	if s := g.spend("key", "poller"); s != 0 {
		t.Errorf("the poller key's budget spend = %v, want 0: the run is the creator's", s)
	}
	runs := g.runEntries(t)
	if len(runs) != 1 {
		t.Fatalf("request log entries with the run's 15 tokens = %d, want 1", len(runs))
	}
	e := runs[0]
	if e["request_id"] != poll.Header().Get("x-agentcc-request-id") {
		t.Errorf("the run is charged on entry %v, want the poll's own, %q", e["request_id"], poll.Header().Get("x-agentcc-request-id"))
	}
	if e["auth_key_id"] != creator.ID {
		t.Errorf("the run's entry is the key %v's, want the creator's %q (the poller is %q)", e["auth_key_id"], creator.ID, poller.ID)
	}
	metadata, _ := e["metadata"].(map[string]any)
	if metadata["org_id"] != runOrg {
		t.Errorf("the run's entry is the org %v's, want the creator's %q", metadata["org_id"], runOrg)
	}
	if metadata["run_id"] != "run_1" {
		t.Errorf("the run's entry names run %v, want run_1", metadata["run_id"])
	}
}

// A key with its own prepaid allowance that polls another key's run pays
// nothing for it: the run is the starter's.
func TestAssistantsRunFinishingOnAPollDrawsNothingFromThePollersAllowance(t *testing.T) {
	g := newRunGatewayWith(t, "byok", "managed", runOrg)
	poller := g.keys.Authenticate(runPollerKey)
	if poller == nil {
		t.Fatal("poller key not found")
	}
	before := poller.BalanceMicros()
	if code, body := g.call(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1"}`); code != http.StatusOK {
		t.Fatalf("create run: %d %s", code, body)
	}
	for range 2 {
		if code, body := g.call(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, ""); code != http.StatusOK {
			t.Fatalf("poll: %d %s", code, body)
		}
	}
	if drawn := before - poller.BalanceMicros(); drawn != 0 {
		t.Errorf("the poller's allowance was drawn %d micros for a run it did not start", drawn)
	}
	if s := g.spend("key", "poller"); s != 0 {
		t.Errorf("the poller key's budget spend = %v, want 0", s)
	}
}

// startersChargeHeaders are the answer headers that carry a charge's cost or
// a key's prepaid allowance.
var startersChargeHeaders = []string{
	"x-agentcc-cost",
	"x-agentcc-charge",
	"x-agentcc-credits-used",
	"x-agentcc-credits-remaining",
	"x-agentcc-managed-credits-remaining",
}

// The poll that a run's charge rides is answered as the poller's own call,
// as dev answers it before it charges the run: none of the starter's charge
// headers (startersChargeHeaders), so a key does not learn another key's
// cost or allowance.
func TestAssistantsPollAnswerCarriesNoneOfTheStartersChargeHeaders(t *testing.T) {
	g := newRunGateway(t, "managed")
	if code, body := g.call(t, http.MethodPost, "/v1/threads/thread_1/runs", runCreatorKey, `{"assistant_id":"asst_1"}`); code != http.StatusOK {
		t.Fatalf("create run: %d %s", code, body)
	}
	poll := g.answer(t, http.MethodGet, "/v1/threads/thread_1/runs/run_1", runPollerKey, "")
	if poll.Code != http.StatusOK {
		t.Fatalf("poll: %d %s", poll.Code, poll.Body.String())
	}
	for _, name := range startersChargeHeaders {
		v := poll.Header().Get(name)
		if v == "" {
			continue
		}
		if f, err := strconv.ParseFloat(v, 64); err == nil && f == 0 && (name == "x-agentcc-cost" || name == "x-agentcc-charge") {
			continue // the poll's own charge: nothing
		}
		t.Errorf("the poller's answer carries %s: %q, the starter's", name, v)
	}
	if runs := g.runEntries(t); len(runs) != 1 {
		t.Errorf("request log entries with the run's 15 tokens = %d, want 1", len(runs))
	}
}
