package server

import (
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"

	"github.com/oklog/ulid/v2"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// assertLoggedAsRuns fails unless the request log holds one full entry per
// run, under the run's request id (its generation id is that id), as it
// holds a request the HTTP server receives. A batch item or a scheduled run
// makes its provider call itself, outside the chat handler, so its entry
// records no attempts.
func assertLoggedAsRuns(t *testing.T, entries []requestLogEntry, runs []runIDs) {
	t.Helper()
	var got, want []string
	for _, e := range entries {
		got = append(got, e.RequestID)
		if e.GenerationID != e.RequestID {
			t.Errorf("the entry for request %q has generation id %q, not its request id", e.RequestID, e.GenerationID)
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

// assertOwnTraceIDs fails unless every run carries a trace id: a ULID, as
// the RequestID middleware makes for a request that brings none.
func assertOwnTraceIDs(t *testing.T, what string, runs []runIDs) {
	t.Helper()
	for i, r := range runs {
		if _, err := ulid.ParseStrict(r.traceID); err != nil {
			t.Errorf("%s %d ran with trace id %q, which is not a ULID: %v", what, i, r.traceID, err)
		}
	}
}

// A /-/batches item runs the pipeline on a request context the batch handler
// in server.go builds, with no HTTP request of its own. Each item's run must
// have a request id of its own, from the source every request id comes from,
// and a trace id; and the request log must hold each item under its id. The
// two items run at the same time: the upstream answers neither until both
// calls are in flight.
func TestBatchItemsRunWithTheirOwnRequestIDs(t *testing.T) {
	up := startRendezvousUpstream(t, 2)
	wh := newRequestLogWebhook(t)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "batch-request-ids-admin-token-9c1a6e3f5b0d8a2c"
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	probe := &runIDProbe{}
	srv := New(cfg, "", registry, pipeline.NewEngine(startRequestLog(t, wh, tenants), probe), nil, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)

	body := `{"requests":[{"model":"gpt-4o","messages":[{"role":"user","content":"a"}]},{"model":"gpt-4o","messages":[{"role":"user","content":"b"}]}],"max_concurrency":2}`
	req := httptest.NewRequest(http.MethodPost, "/-/batches", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+cfg.Admin.Token)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusAccepted {
		t.Fatalf("batch submit: status %d body %s", w.Code, w.Body.String())
	}

	runs := probe.waitRuns(2)
	t.Logf("runs (request id, trace id): %q; the batch request's own request id is %s", runs, w.Header().Get("x-agentcc-request-id"))
	if len(runs) != 2 {
		t.Fatalf("the pipeline ran %d times for 2 batch items", len(runs))
	}
	if n := up.mostInFlight(); n != 2 {
		t.Fatalf("the upstream held at most %d call(s) at once: the two items did not run at the same time", n)
	}
	assertOwnRequestIDs(t, "batch item", runs)
	assertOwnTraceIDs(t, "batch item", runs)

	entries := wh.waitFor(2)
	t.Logf("request log entries: %+v", entries)
	assertLoggedAsRuns(t, entries, runs)
}
