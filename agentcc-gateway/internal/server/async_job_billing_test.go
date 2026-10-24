package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An async job is billed exactly as its request would have been: the cost
// plugin prices it, the credits plugin charges the key, and the logging
// plugin sends Django the request log it bills and shows usage from. Only a
// non-stream request context runs those post-plugins inside the pipeline
// (asyncExecutor sets jobRC.IsStream = false).
//
// A key limited to its org's providers, a managed key included, cannot
// queue an async job (400 deferred_run_unavailable,
// TestAsyncIsRefusedToOrgKeys); only an internal key can. An internal key is
// neither charged (credits charge managed keys) nor logged (the logging
// plugin skips key_type internal). These tests pin both: a billable key's
// async request is refused before anything is called, charged or logged; and
// the job an internal key queues runs the post-plugins, so it is priced
// exactly as the same request run synchronously.

// billingLogWebhook stands in for Django's /agentcc/webhook/logs/ endpoint
// and records each request log the gateway delivers.
type billingLogWebhook struct {
	srv  *httptest.Server
	mu   sync.Mutex
	logs []billedLog
}

type billedLog struct {
	RequestID string  `json:"request_id"`
	AuthKeyID string  `json:"auth_key_id"`
	Cost      float64 `json:"cost"`
	IsStream  bool    `json:"is_stream"`
}

func newBillingLogWebhook(t *testing.T) *billingLogWebhook {
	t.Helper()
	wh := &billingLogWebhook{}
	wh.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Logs []billedLog `json:"logs"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		wh.mu.Lock()
		wh.logs = append(wh.logs, body.Logs...)
		wh.mu.Unlock()
	}))
	t.Cleanup(wh.srv.Close)
	return wh
}

// waitFor returns the delivered logs once there are n of them, or what was
// delivered after 5 s.
func (wh *billingLogWebhook) waitFor(n int) []billedLog {
	deadline := time.Now().Add(5 * time.Second)
	for {
		wh.mu.Lock()
		logs := append([]billedLog(nil), wh.logs...)
		wh.mu.Unlock()
		if len(logs) >= n || time.Now().After(deadline) {
			return logs
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// billingServer: auth on; a managed key "sk-managed" in org "org-scope",
// whose org has its own openai provider, and an internal key "sk-ops"; the
// cost, credits and logging plugins wired as cmd/agentcc wires them, the logs
// going to wh. calls counts the upstream's chat calls.
func billingServer(t *testing.T, wh *billingLogWebhook) (srv *Server, managed *auth.APIKey, calls *atomic.Int64) {
	t.Helper()
	upstream, calls := countingUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "managed", Key: "sk-managed", Owner: "o", KeyType: "managed",
			CreditBalance: 10, Metadata: map[string]string{"org_id": "org-scope"}},
		{Name: "ops", Key: "sk-ops", Owner: "ops", KeyType: "internal"},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream, APIKey: "platform-openai-key", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ts := tenant.NewStore()
	ts.Set("org-scope", orgOpenAI("gpt-4o"))
	ks := auth.NewKeyStore(cfg.Auth)
	mdb := testModelDBPtr()

	flusher := loggingplugin.NewLogFlusher(wh.srv.URL, "test-log-webhook-secret", time.Hour, 1)
	ctx, cancel := context.WithCancel(context.Background())
	go flusher.Run(ctx)
	logging := loggingplugin.New(config.RequestLoggingConfig{Enabled: true, Workers: 1}, ts)
	logging.SetFlusher(flusher)

	eng := pipeline.NewEngine(
		authplugin.New(ks, true),
		costplugin.New(true, func() *modeldb.ModelDB { return mdb.Load() }, ts, nil),
		creditsplugin.New(true, ks),
		logging,
	)
	srv = New(cfg, "", registry, eng, ks, nil, nil, nil, mdb, ts, nil)
	srv.ready.Store(true)
	t.Cleanup(func() {
		srv.asyncWorker.Stop()
		cancel()
		logging.Close()
	})
	managed = ks.Lookup("sk-managed")
	if managed == nil {
		t.Fatal("the managed key is not in the key store")
	}
	return srv, managed, calls
}

const billingChat = `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`

// A managed key's async request is refused before the provider is called:
// nothing is charged, no job is stored and Django gets no request log for
// it. The same key's sync request is charged and logged, so the set-up bills.
func TestAsyncJobs_AManagedKeyCannotQueueAnUnbilledJob(t *testing.T) {
	wh := newBillingLogWebhook(t)
	srv, key, calls := billingServer(t, wh)
	balance := func() int64 { return auth.USDToMicros(key.BalanceUSD()) }

	start := balance()
	before := srv.handlers.asyncStore.Count()
	w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-managed", billingChat, map[string]string{"x-agentcc-async": "true"})
	assertAsyncRefusedToOrgKey(t, srv, w, before)
	if n := calls.Load(); n != 0 {
		t.Fatalf("the refused async request reached the upstream %d time(s)", n)
	}
	if got := balance(); got != start {
		t.Fatalf("the refused async request changed the balance from %d to %d micro-USD", start, got)
	}

	w = objCall(t, srv, "POST", "/v1/chat/completions", "sk-managed", billingChat, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("sync request: status %d body %s", w.Code, w.Body.String())
	}
	if charge := start - balance(); charge <= 0 {
		t.Fatalf("the sync request charged %d micro-USD; the test set-up does not bill at all", charge)
	}
	// Logs go out one at a time in order, so a log for the refused request
	// would arrive before the sync request's.
	logs := wh.waitFor(1)
	if len(logs) != 1 {
		t.Fatalf("Django received %d request logs, want exactly the sync request's: %+v", len(logs), logs)
	}
	if id := w.Header().Get("x-agentcc-request-id"); id == "" || logs[0].RequestID != id {
		t.Fatalf("the first request log is %q, want the sync request's %q: the refused async request was logged", logs[0].RequestID, id)
	}
	if logs[0].Cost <= 0 {
		t.Fatalf("the sync request's log carries cost %v", logs[0].Cost)
	}
}

// The job an internal key queues runs the post-plugins inside the pipeline,
// so the cost plugin prices it exactly as the same request run
// synchronously.
func TestAsyncJobs_AreCostedLikeTheSyncRequest(t *testing.T) {
	wh := newBillingLogWebhook(t)
	srv, _, calls := billingServer(t, wh)

	w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-ops", billingChat, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("sync request: status %d body %s", w.Code, w.Body.String())
	}
	syncCost := w.Header().Get("x-agentcc-cost")
	if syncCost == "" || syncCost == "0" || syncCost == "0.000000" {
		t.Fatalf("the sync request carries cost %q; the test set-up does not price at all", syncCost)
	}

	w = objCall(t, srv, "POST", "/v1/chat/completions", "sk-ops", billingChat, map[string]string{"x-agentcc-async": "true"})
	if w.Code != http.StatusAccepted {
		t.Fatalf("async submit: status %d body %s", w.Code, w.Body.String())
	}
	id, _ := mediaDecode(t, w)["job_id"].(string)
	if job := waitAsyncJob(t, srv, "sk-ops", id); job["status"] != "completed" {
		t.Fatalf("async job ended %v: %v", job["status"], job)
	}
	if n := calls.Load(); n != 2 {
		t.Fatalf("the upstream saw %d chat calls, want 2 (the sync request and the job)", n)
	}
	job := srv.handlers.asyncStore.Get(id)
	if job == nil {
		t.Fatal("the completed job is not in the store")
	}
	if got := job.Metadata["cost"]; got != syncCost {
		t.Errorf("the async job is priced %q, the same request run synchronously %q: an async job must run the cost plugin as its request does", got, syncCost)
	}
}
