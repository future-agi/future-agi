package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/scheduled"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// newScheduledIDsServer builds a gateway with scheduled completions on, its
// openai provider at upstreamURL, and the request log (to wh) and probe in the
// engine. Config validation refuses routing.scheduled.enabled;
// New does not validate, so the scheduler and its executor run here as they
// would if it were allowed. A retried job is due again 1 ms after it failed.
func newScheduledIDsServer(t *testing.T, upstreamURL string, wh *requestLogWebhook, probe *runIDProbe) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Routing.Scheduled = config.ScheduledConfig{Enabled: true, RetryBackoff: time.Millisecond, WorkerCount: 4}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstreamURL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	srv := New(cfg, "", registry, pipeline.NewEngine(startRequestLog(t, wh, tenants), probe), nil, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv
}

// scheduleNow stores a job that is due now, as SubmitScheduled stores one.
func scheduleNow(t *testing.T, srv *Server, id, content string, maxAttempts int) {
	t.Helper()
	now := time.Now()
	body, _ := json.Marshal(models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{{Role: "user", Content: json.RawMessage(fmt.Sprintf("%q", content))}}})
	err := srv.handlers.scheduledStore.Create(&scheduled.ScheduledJob{
		ID: id, Status: scheduled.StatusPending, ScheduledAt: now, CreatedAt: now, UpdatedAt: now,
		Request: body, Model: "gpt-4o", MaxAttempts: maxAttempts,
	})
	if err != nil {
		t.Fatalf("storing job %s: %v", id, err)
	}
}

// A scheduled job runs the pipeline on a request context the scheduler's
// executor in server.go builds, long after the request that scheduled it has
// answered. Each run must have a request id of its own, from the source every
// request id comes from, and a trace id; and the request log must hold each
// run under its id. Two jobs fall due together and run at the same time: the
// upstream answers neither until both calls are in flight.
func TestScheduledRunsHaveTheirOwnRequestIDs(t *testing.T) {
	up := startRendezvousUpstream(t, 2)
	wh := newRequestLogWebhook(t)
	probe := &runIDProbe{}
	srv := newScheduledIDsServer(t, up.URL, wh, probe)

	scheduleNow(t, srv, "sched-ids-a", "a", 1)
	scheduleNow(t, srv, "sched-ids-b", "b", 1)

	runs := probe.waitRuns(2)
	t.Logf("runs (request id, trace id): %q", runs)
	if len(runs) != 2 {
		t.Fatalf("the pipeline ran %d times for 2 scheduled jobs", len(runs))
	}
	if n := up.mostInFlight(); n != 2 {
		t.Fatalf("the upstream held at most %d call(s) at once: the two jobs did not run at the same time", n)
	}
	assertOwnRequestIDs(t, "scheduled run", runs)
	assertOwnTraceIDs(t, "scheduled run", runs)

	entries := wh.waitFor(2)
	t.Logf("request log entries: %+v", entries)
	assertLoggedAsRuns(t, entries, runs)
}

// A retried job runs the pipeline again: each attempt is a run of its own,
// with its own request id, and the request log holds each attempt under its
// id. The upstream refuses the job's first call (400) and answers the second.
func TestEachScheduledAttemptIsARunOfItsOwn(t *testing.T) {
	var calls atomic.Int64
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.WriteHeader(http.StatusOK)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if calls.Add(1) == 1 {
			w.WriteHeader(http.StatusBadRequest)
			fmt.Fprint(w, `{"error":{"message":"refused once","type":"invalid_request_error","code":"refused_once"}}`)
			return
		}
		fmt.Fprint(w, `{"id":"c1","object":"chat.completion","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}`)
	}))
	t.Cleanup(up.Close)
	wh := newRequestLogWebhook(t)
	probe := &runIDProbe{}
	srv := newScheduledIDsServer(t, up.URL, wh, probe)

	scheduleNow(t, srv, "sched-ids-retry", "retry me", 2)

	runs := probe.waitRuns(2)
	t.Logf("runs (request id, trace id): %q; upstream calls %d", runs, calls.Load())
	if len(runs) != 2 || calls.Load() != 2 {
		t.Fatalf("the pipeline ran %d times and the upstream had %d call(s) for one job retried once; want 2 and 2", len(runs), calls.Load())
	}
	assertOwnRequestIDs(t, "attempt", runs)
	assertOwnTraceIDs(t, "attempt", runs)

	entries := wh.waitFor(2)
	t.Logf("request log entries: %+v", entries)
	assertLoggedAsRuns(t, entries, runs)
}
