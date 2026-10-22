package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"

	agentcca2a "github.com/futureagi/agentcc-gateway/internal/a2a"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Request and trace ids key managed credit holds and settlements, attempt ids,
// spans and the request log, so every run of the pipeline needs its own. The
// RequestID middleware gives one to every request the HTTP server receives.
// These tests cover the runs the gateway starts itself, which never pass
// through it: an A2A task (here) and a /-/batches item. The scheduler's
// executor, which config validation keeps off (routing.scheduled.enabled),
// is covered in scheduled_request_ids_test.go.

// runIDProbe is a post-plugin that records the request and trace id of every
// run of the pipeline.
type runIDProbe struct {
	mu   sync.Mutex
	runs []runIDs
}

type runIDs struct{ requestID, traceID string }

func (p *runIDProbe) Name() string  { return "run-id-probe" }
func (p *runIDProbe) Priority() int { return 990 }
func (p *runIDProbe) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *runIDProbe) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	p.runs = append(p.runs, runIDs{rc.RequestID, rc.TraceID})
	p.mu.Unlock()
	return pipeline.ResultContinue()
}

// waitRuns returns the runs recorded once there are n of them, or what was
// recorded after 5 s.
func (p *runIDProbe) waitRuns(n int) []runIDs {
	deadline := time.Now().Add(5 * time.Second)
	for {
		p.mu.Lock()
		runs := append([]runIDs(nil), p.runs...)
		p.mu.Unlock()
		if len(runs) >= n || time.Now().After(deadline) {
			return runs
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// assertOwnRequestIDs fails unless every run has a request id of its own: a
// ULID, and no two runs share one.
func assertOwnRequestIDs(t *testing.T, what string, runs []runIDs) {
	t.Helper()
	seen := map[string]bool{}
	for i, r := range runs {
		if _, err := ulid.ParseStrict(r.requestID); err != nil {
			t.Errorf("%s %d ran with request id %q, which is not a ULID: %v", what, i, r.requestID, err)
			continue
		}
		if seen[r.requestID] {
			t.Errorf("%s %d ran with request id %s, which another %s already had", what, i, r.requestID, what)
		}
		seen[r.requestID] = true
	}
}

// rendezvousUpstream is an OpenAI-format upstream that holds every chat call
// until n calls are in flight at the same moment (or 5 s pass), so the runs
// that make them are proven to overlap.
type rendezvousUpstream struct {
	*httptest.Server
	n        int
	mu       sync.Mutex
	inFlight int
	most     int
	all      chan struct{}
	opened   bool
}

func startRendezvousUpstream(t *testing.T, n int) *rendezvousUpstream {
	t.Helper()
	u := &rendezvousUpstream{n: n, all: make(chan struct{})}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.WriteHeader(http.StatusOK)
			return
		}
		var req models.ChatCompletionRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		u.mu.Lock()
		u.inFlight++
		u.most = max(u.most, u.inFlight)
		if u.inFlight >= u.n && !u.opened {
			u.opened = true
			close(u.all)
		}
		u.mu.Unlock()
		select {
		case <-u.all:
		case <-time.After(5 * time.Second):
		}
		u.mu.Lock()
		u.inFlight--
		u.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","created":1700000000,"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":%g}}`, req.Model, upstreamCost)
	}))
	t.Cleanup(u.Close)
	return u
}

// mostInFlight is the largest number of chat calls the upstream held at once.
func (u *rendezvousUpstream) mostInFlight() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.most
}

// a2aReply is one A2A message/send: the HTTP response's trace and request
// headers, and the task it returned.
type a2aReply struct {
	code      int
	traceID   string
	requestID string
	task      agentcca2a.Task
}

// sendA2ATask sends one blocking A2A message/send for model with key, with
// the caller's x-agentcc-trace-id when traceID is set. It returns an error
// instead of failing, so goroutines can call it.
func sendA2ATask(srv *Server, key, model, traceID string) (a2aReply, error) {
	params, err := json.Marshal(agentcca2a.MessageSendParams{
		Message:  agentcca2a.Message{Role: "user", Parts: []agentcca2a.MessagePart{{Type: "text", Text: "hi"}}},
		Metadata: json.RawMessage(`{"model":` + strconv.Quote(model) + `}`),
	})
	if err != nil {
		return a2aReply{}, err
	}
	body, err := json.Marshal(mcp.Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: agentcca2a.MethodMessageSend, Params: params})
	if err != nil {
		return a2aReply{}, err
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/a2a", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	if traceID != "" {
		req.Header.Set("x-agentcc-trace-id", traceID)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	reply := a2aReply{code: w.Code, traceID: w.Header().Get("x-agentcc-trace-id"), requestID: w.Header().Get("x-agentcc-request-id")}
	var resp mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		return reply, fmt.Errorf("status %d, body %s: %w", w.Code, w.Body.String(), err)
	}
	if resp.Error != nil {
		return reply, fmt.Errorf("JSON-RPC error %d: %s", resp.Error.Code, resp.Error.Message)
	}
	if err := json.Unmarshal(resp.Result, &reply.task); err != nil {
		return reply, fmt.Errorf("decoding the task: %w (body %s)", err, w.Body.String())
	}
	return reply, nil
}

// sendA2ATasksTogether sends one A2A task per trace id (an empty one sends
// no x-agentcc-trace-id), all at once, and fails unless every task completed.
func sendA2ATasksTogether(t *testing.T, srv *Server, key, model string, traceIDs ...string) []a2aReply {
	t.Helper()
	replies := make([]a2aReply, len(traceIDs))
	errs := make([]error, len(traceIDs))
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i, trace := range traceIDs {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			replies[i], errs[i] = sendA2ATask(srv, key, model, trace)
		}()
	}
	close(start)
	wg.Wait()
	for i, err := range errs {
		if err != nil {
			t.Fatalf("A2A task %d: %v", i, err)
		}
		if st := replies[i].task.Status; st.State != agentcca2a.TaskStatusCompleted {
			t.Fatalf("A2A task %d ended %q (%+v), want %q", i, st.State, st.Message, agentcca2a.TaskStatusCompleted)
		}
	}
	return replies
}

// assertRunsCarryTheirA2ATraces fails unless the runs' trace ids are exactly
// the trace ids the A2A responses carried, one run per A2A request.
func assertRunsCarryTheirA2ATraces(t *testing.T, runs []runIDs, replies []a2aReply) {
	t.Helper()
	var got, want []string
	for _, r := range runs {
		got = append(got, r.traceID)
	}
	for _, r := range replies {
		want = append(want, r.traceID)
	}
	sort.Strings(got)
	sort.Strings(want)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("the runs carry trace ids %q; the A2A requests that started them carry %q", got, want)
	}
}

// An A2A task runs the chat pipeline on a request the gateway builds itself
// (the A2A executor in server.go), so it never passes the RequestID
// middleware. Each task's run must still have a request id of its own, from
// the source every request id comes from, and carry the trace id of the A2A
// request that started it: the caller's x-agentcc-trace-id, or the one the
// gateway gave that request. The two tasks run at the same time: the
// upstream answers neither until both are in flight.
func TestA2ATasksRunWithTheirOwnRequestIDs(t *testing.T) {
	up := startRendezvousUpstream(t, 2)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "svc", Key: "sk-test-key-001", Owner: "test-org", KeyType: "internal"}}
	cfg.A2A.Enabled = true
	cfg.A2A.Card = config.A2ACardConfig{Name: "gw", Description: "d", Version: "1.0.0"}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	probe := &runIDProbe{}
	srv := New(cfg, "", registry, pipeline.NewEngine(probe), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)

	const callerTrace = "trace-from-the-a2a-caller"
	replies := sendA2ATasksTogether(t, srv, "sk-test-key-001", "gpt-4o", callerTrace, "")
	if replies[0].traceID != callerTrace || replies[1].traceID == "" {
		t.Fatalf("the A2A responses carry trace ids %q and %q; want the caller's %q, and one the gateway made", replies[0].traceID, replies[1].traceID, callerTrace)
	}
	if n := up.mostInFlight(); n != 2 {
		t.Fatalf("the upstream held at most %d call(s) at once: the two tasks did not run at the same time", n)
	}

	runs := probe.waitRuns(2)
	t.Logf("runs (request id, trace id): %q", runs)
	if len(runs) != 2 {
		t.Fatalf("the pipeline ran %d times for 2 A2A tasks", len(runs))
	}
	assertOwnRequestIDs(t, "A2A task", runs)
	assertRunsCarryTheirA2ATraces(t, runs, replies)
}

// requestLogWebhook stands in for Django's request-log webhook and keeps the
// fields of each entry that carry the request id.
type requestLogWebhook struct {
	srv     *httptest.Server
	mu      sync.Mutex
	entries []requestLogEntry
}

type requestLogEntry struct {
	RequestID    string `json:"request_id"`
	GenerationID string `json:"generation_id"`
	Model        string `json:"model"`
	Attempts     []struct {
		AttemptID string `json:"attempt_id"`
	} `json:"attempts"`
}

func newRequestLogWebhook(t *testing.T) *requestLogWebhook {
	t.Helper()
	wh := &requestLogWebhook{}
	wh.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Logs []json.RawMessage `json:"logs"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		for _, raw := range body.Logs {
			var kind struct {
				Kind string `json:"kind"`
			}
			if json.Unmarshal(raw, &kind) == nil && kind.Kind != "" {
				continue // an event, not a request's entry
			}
			var e requestLogEntry
			if err := json.Unmarshal(raw, &e); err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			wh.mu.Lock()
			wh.entries = append(wh.entries, e)
			wh.mu.Unlock()
		}
	}))
	t.Cleanup(wh.srv.Close)
	return wh
}

// waitFor returns the entries delivered once there are n of them, or what was
// delivered after 5 s.
func (wh *requestLogWebhook) waitFor(n int) []requestLogEntry {
	deadline := time.Now().Add(5 * time.Second)
	for {
		wh.mu.Lock()
		entries := append([]requestLogEntry(nil), wh.entries...)
		wh.mu.Unlock()
		if len(entries) >= n || time.Now().After(deadline) {
			return entries
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// startRequestLog wires the logging plugin as cmd/agentcc does, its entries
// going to wh, and returns it for the engine.
func startRequestLog(t *testing.T, wh *requestLogWebhook, tenants *tenant.Store) *loggingplugin.Plugin {
	t.Helper()
	flusher := loggingplugin.NewLogFlusher(wh.srv.URL, "", time.Hour, 1)
	ctx, cancel := context.WithCancel(context.Background())
	go flusher.Run(ctx)
	logging := loggingplugin.New(config.RequestLoggingConfig{Enabled: true, Workers: 1}, tenants)
	logging.SetFlusher(flusher)
	t.Cleanup(func() {
		cancel()
		logging.Close()
	})
	return logging
}

// assertLoggedUnderTheirIDs fails unless the request log holds one entry per
// run, each under its run's request id the way an HTTP request's entry is: a
// full entry, whose generation id and first attempt id are made from it.
func assertLoggedUnderTheirIDs(t *testing.T, entries []requestLogEntry, runs []runIDs) {
	t.Helper()
	var got, want []string
	for _, e := range entries {
		got = append(got, e.RequestID)
		if e.GenerationID != e.RequestID {
			t.Errorf("the entry for request %q has generation id %q, not its request id", e.RequestID, e.GenerationID)
		}
		if len(e.Attempts) == 0 || e.Attempts[0].AttemptID != models.AttemptID(e.RequestID, 1) {
			t.Errorf("the entry for request %q has attempts %+v; want a first attempt %q", e.RequestID, e.Attempts, models.AttemptID(e.RequestID, 1))
		}
	}
	for _, r := range runs {
		want = append(want, r.requestID)
	}
	sort.Strings(got)
	sort.Strings(want)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("the request log holds entries for request ids %q; the runs had %q", got, want)
	}
}

// newManagedA2AGateway is newManagedTestGateway with A2A on, its managed
// provider at upstreamURL, and the request log and probe in the engine.
func newManagedA2AGateway(t *testing.T, upstreamURL string, wh *requestLogWebhook, probe *runIDProbe) *managedTestGateway {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = adminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "org-user", Key: managedOrgKey, Owner: "user", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-m"},
	}}
	cfg.Providers["openrouter"] = config.ProviderConfig{
		BaseURL: upstreamURL, APIKey: "fagi-openrouter-key", APIFormat: "openai",
		Type: "openrouter", Managed: true, AutoDiscover: &off,
	}
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
	cfg.A2A.Enabled = true
	cfg.A2A.Card = config.A2ACardConfig{Name: "gw", Description: "d", Version: "1.0.0"}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	store := catalog.NewStore()
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled", CatalogSource: catalog.SourceOpenRouter,
		FetchedAt: time.Now(), Models: testCatalogModels})
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	tenants := tenant.NewStore()
	tenants.Set("org-m", managedOptIn())

	keyStore := auth.NewKeyStore(cfg.Auth)
	ledger := creditsplugin.NewOrgLedger(nil)
	cost := costplugin.New(true, mdb.Load, tenants, nil)
	cost.SetManagedMarkup(5)
	credits := creditsplugin.New(true, keyStore)
	credits.SetOrgLedger(ledger, true)
	logging := startRequestLog(t, wh, tenants)

	srv := New(cfg, "", registry, pipeline.NewEngine(cost, credits, logging, probe), keyStore, nil, nil, nil, &mdb, tenants, nil)
	srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
	h := srv.handlers
	credits.SetPrices(func(providerID, model, endpointID string) (models.PriceCard, bool) {
		return h.Deps().Prices(providerID, model, endpointID)
	})
	credits.SetOrgMaxTokens(h.OrgDefaultMaxTokensEstimate)
	h.SetBilling(credits)
	srv.ready.Store(true)
	return &managedTestGateway{srv: srv, ledger: ledger, credits: credits, tenants: tenants}
}

// A managed A2A task is held, settled and logged under its run's request id,
// exactly as a managed chat request the HTTP server receives is under the id
// the middleware gave it; one such request is the control. Each task is
// held, settled and billed under its own id. The two tasks run at the same
// time.
func TestManagedA2ATasksAreHeldSettledAndLoggedUnderTheirOwnIDs(t *testing.T) {
	up := startRendezvousUpstream(t, 2)
	wh := newRequestLogWebhook(t)
	probe := &runIDProbe{}
	g := newManagedA2AGateway(t, up.URL, wh, probe)
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	replies := sendA2ATasksTogether(t, g.srv, managedOrgKey, "anthropic/claude-sonnet-5.5", "", "")
	if n := up.mostInFlight(); n != 2 {
		t.Fatalf("the upstream held at most %d call(s) at once: the two tasks did not run at the same time", n)
	}
	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)
	controlID := w.Header().Get("x-agentcc-request-id")
	if w.Code != http.StatusOK || controlID == "" {
		t.Fatalf("the control chat request: status %d, request id %q, body %s", w.Code, controlID, w.Body.String())
	}

	runs := probe.waitRuns(3)
	t.Logf("runs (request id, trace id): %q; the control's request id is %s", runs, controlID)
	var a2aRuns []runIDs
	sawControl := false
	for _, r := range runs {
		if r.requestID == controlID && !sawControl {
			sawControl = true
			continue
		}
		a2aRuns = append(a2aRuns, r)
	}
	if !sawControl || len(a2aRuns) != 2 {
		t.Fatalf("the pipeline ran %d times (the control's run seen: %v) for 2 A2A tasks and 1 chat request", len(runs), sawControl)
	}

	// Held and settled: one applied settlement per run, under its request id,
	// and every one of them spent on the org's account.
	outbox := g.ledger.Ledger().(*billing.MemoryLedger).Outbox("org-m")
	unsettled := map[string]int{}
	for _, r := range runs {
		unsettled[r.requestID]++
	}
	var billed int64
	a2aBilled := 0
	for _, rec := range outbox {
		id := rec["request_id"]
		micros, _ := strconv.ParseInt(rec["billed_micros"], 10, 64)
		if rec["kind"] != billing.KindSpend || micros <= 0 {
			t.Errorf("the record for request %q is a %s of %d micros, want a billed spend: %v", id, rec["kind"], micros, rec)
		}
		billed += micros
		if unsettled[id] == 0 {
			t.Errorf("a settlement for request %q, which no run had", id)
			continue
		}
		unsettled[id]--
		if id != controlID {
			a2aBilled++
		}
	}
	if a2aBilled != 2 {
		t.Errorf("the org was billed for %d of its 2 A2A tasks (%d settlement(s) for 3 runs)", a2aBilled, len(outbox))
	}
	if unsettled[controlID] != 0 {
		t.Errorf("the control chat request %s was never settled", controlID)
	}
	acct := g.account(t, "org-m")
	if acct.SpentMicros != billed || acct.HeldMicros != 0 {
		t.Errorf("the org's account has spent %d and holds %d micros; want spent %d (every settlement) and nothing held", acct.SpentMicros, acct.HeldMicros, billed)
	}

	assertOwnRequestIDs(t, "A2A task", a2aRuns)
	assertRunsCarryTheirA2ATraces(t, a2aRuns, replies)

	entries := wh.waitFor(3)
	t.Logf("request log entries: %+v", entries)
	assertLoggedUnderTheirIDs(t, entries, runs)
}
