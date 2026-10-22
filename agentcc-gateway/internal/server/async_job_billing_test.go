package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
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

// An async job is billed exactly as the same request served synchronously: the cost
// plugin prices it, the credits plugin charges the key, and the logging
// plugin sends Django the request log it bills and shows usage from. Only a
// non-stream request context runs those post-plugins inside the pipeline, so
// these tests pin that the job runs as one.

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

// billingServer: auth on; one managed key "sk-managed" in org "org-scope",
// whose org has its own openai provider; the cost, credits and logging
// plugins wired as cmd/agentcc wires them, the logs going to wh.
func billingServer(t *testing.T, up *scopeUpstream, wh *billingLogWebhook) (*Server, *auth.APIKey) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "managed", Key: "sk-managed", Owner: "o", KeyType: "managed",
		CreditBalance: 10, Metadata: map[string]string{"org_id": "org-scope"},
	}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.srv.URL, APIKey: "platform-openai-key", APIFormat: "openai", Models: []string{"gpt-4o"}}
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
	srv := New(cfg, "", registry, eng, ks, nil, nil, nil, mdb, ts, nil)
	srv.ready.Store(true)
	t.Cleanup(func() {
		srv.asyncWorker.Stop()
		cancel()
		logging.Close()
	})
	keys := ks.List()
	if len(keys) != 1 {
		t.Fatalf("key store holds %d keys, want 1", len(keys))
	}
	return srv, keys[0]
}

func TestAsyncJobs_AreBilledAndLoggedLikeTheSyncRequest(t *testing.T) {
	up := newScopeUpstream(t)
	wh := newBillingLogWebhook(t)
	srv, key := billingServer(t, up, wh)
	balance := func() int64 { return auth.USDToMicros(key.BalanceUSD()) }

	start := balance()
	w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-managed", scopeChat, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("sync request: status %d body %s", w.Code, w.Body.String())
	}
	syncCharge := start - balance()
	if syncCharge <= 0 {
		t.Fatalf("the sync request charged %d micro-USD; the test set-up does not bill at all", syncCharge)
	}
	logs := wh.waitFor(1)
	if len(logs) != 1 {
		t.Fatalf("Django received %d request logs for the sync request, want 1: %+v", len(logs), logs)
	}
	syncLog := logs[0]

	afterSync := balance()
	w = objCall(t, srv, "POST", "/v1/chat/completions", "sk-managed", scopeChat, map[string]string{"x-agentcc-async": "true"})
	if w.Code != http.StatusAccepted {
		t.Fatalf("async submit: status %d body %s", w.Code, w.Body.String())
	}
	id, _ := mediaDecode(t, w)["job_id"].(string)
	if job := waitAsyncJob(t, srv, "sk-managed", id); job["status"] != "completed" {
		t.Fatalf("async job ended %v: %v", job["status"], job)
	}
	if asyncCharge := afterSync - balance(); asyncCharge != syncCharge {
		t.Errorf("the async job charged the key %d micro-USD, the same request run synchronously %d: an async job must be charged as its request", asyncCharge, syncCharge)
	}

	logs = wh.waitFor(2)
	if len(logs) != 2 {
		t.Fatalf("Django received %d request logs, want 2 (the sync request and the async job): %+v", len(logs), logs)
	}
	jobLog := logs[1]
	if jobLog.Cost <= 0 || jobLog.Cost != syncLog.Cost {
		t.Errorf("the async job's request log carries cost %v, the sync request's %v: Django bills from that cost", jobLog.Cost, syncLog.Cost)
	}
	if jobLog.AuthKeyID == "" || jobLog.AuthKeyID != syncLog.AuthKeyID {
		t.Errorf("the async job's request log names key %q, the sync request's %q", jobLog.AuthKeyID, syncLog.AuthKeyID)
	}
	if jobLog.IsStream {
		t.Errorf("the async job's request log says is_stream; an async job is a non-stream request")
	}
	if jobLog.RequestID == syncLog.RequestID {
		t.Errorf("the async job reused the sync request's id %q", jobLog.RequestID)
	}
}

func TestAsyncJobs_AManagedKeyWithNoCreditGetsNoUpstreamCall(t *testing.T) {
	up := newScopeUpstream(t)
	wh := newBillingLogWebhook(t)
	srv, key := billingServer(t, up, wh)
	key.SetBalanceMicros(0)

	w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-managed", scopeChat, map[string]string{"x-agentcc-async": "true"})
	if w.Code != http.StatusAccepted {
		t.Fatalf("async submit: status %d body %s", w.Code, w.Body.String())
	}
	id, _ := mediaDecode(t, w)["job_id"].(string)
	job := waitAsyncJob(t, srv, "sk-managed", id)

	if job["status"] != "failed" {
		t.Errorf("a $0 managed key's job ended %v, want failed", job["status"])
	}
	if errObj, _ := job["error"].(map[string]any); errObj == nil || errObj["Code"] != "payment_required" {
		t.Errorf("job error %v, want payment_required", job["error"])
	}
	if seen := up.take(); len(seen) != 0 {
		t.Errorf("the upstream was called %q for a $0 managed key's job", seen)
	}
	if got := auth.USDToMicros(key.BalanceUSD()); got != 0 {
		t.Errorf("balance %d micro-USD after a refused job, want 0", got)
	}
}
