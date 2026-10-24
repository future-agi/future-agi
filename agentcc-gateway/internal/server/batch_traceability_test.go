package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A /-/batches item is a run of the pipeline with a request id of its own
// and the platform's request log keeps its entry under that id. So
// that the entry can be found from the batch, each result in GET
// /-/batches/{id} names its item's request_id. And each item's run carries
// the trace id of the batch request that submitted it (the caller's
// x-agentcc-trace-id, or the one the gateway gave that request), which the
// gateway's own trace records and OpenTelemetry spans are keyed on.

// tracedLogWebhook is the platform's request-log webhook: it keeps each
// entry's request id.
type tracedLogWebhook struct {
	*httptest.Server
	mu      sync.Mutex
	entries []string
}

func startTracedLogWebhook(t *testing.T) *tracedLogWebhook {
	t.Helper()
	wh := &tracedLogWebhook{}
	wh.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Logs []json.RawMessage `json:"logs"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		for _, raw := range body.Logs {
			var e struct {
				Kind      string `json:"kind"`
				RequestID string `json:"request_id"`
			}
			if json.Unmarshal(raw, &e) != nil || e.Kind != "" {
				continue
			}
			wh.mu.Lock()
			wh.entries = append(wh.entries, e.RequestID)
			wh.mu.Unlock()
		}
	}))
	t.Cleanup(wh.Close)
	return wh
}

// holds waits up to 5 s for an entry under requestID, and reports whether
// one arrived.
func (wh *tracedLogWebhook) holds(requestID string) bool {
	for deadline := time.Now().Add(5 * time.Second); ; time.Sleep(10 * time.Millisecond) {
		wh.mu.Lock()
		for _, id := range wh.entries {
			if id == requestID {
				wh.mu.Unlock()
				return true
			}
		}
		wh.mu.Unlock()
		if time.Now().After(deadline) {
			return false
		}
	}
}

func TestBatchItemsCarryTheBatchRequestsTraceAndNameTheirRequestIDs(t *testing.T) {
	up := startRendezvousUpstream(t, 1)
	wh := startTracedLogWebhook(t)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "batch-traceability-admin-token-4e2c9a7b1d6f3e8a"
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	probe := &runIDProbe{}
	logging := startRequestLog(t, &requestLogWebhook{srv: wh.Server}, tenants)
	srv := New(cfg, "", registry, pipeline.NewEngine(logging, probe), nil, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)

	admin := func(method, path, body, trace string) (int, http.Header, map[string]any) {
		t.Helper()
		req := httptest.NewRequest(method, path, bytes.NewReader([]byte(body)))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+cfg.Admin.Token)
		if trace != "" {
			req.Header.Set("x-agentcc-trace-id", trace)
		}
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		b, _ := io.ReadAll(w.Result().Body)
		var out map[string]any
		if err := json.Unmarshal(b, &out); err != nil {
			t.Fatalf("%s %s: status %d, body %s is not JSON: %v", method, path, w.Code, b, err)
		}
		return w.Code, w.Header(), out
	}

	runs := 0
	for _, callerTrace := range []string{"trace-of-the-batch-request", ""} {
		code, header, submitted := admin(http.MethodPost, "/-/batches",
			`{"requests":[{"model":"gpt-4o","messages":[{"role":"user","content":"a"}]},{"model":"gpt-4o","messages":[{"role":"user","content":"b"}]}]}`,
			callerTrace)
		if code != http.StatusAccepted {
			t.Fatalf("submit: status %d, body %v", code, submitted)
		}
		batchTrace := header.Get("x-agentcc-trace-id")
		if callerTrace != "" && batchTrace != callerTrace {
			t.Fatalf("the batch request's trace id is %q, want the caller's %q", batchTrace, callerTrace)
		}
		id, _ := submitted["batch_id"].(string)
		var batch map[string]any
		for deadline := time.Now().Add(5 * time.Second); ; time.Sleep(10 * time.Millisecond) {
			_, _, batch = admin(http.MethodGet, "/-/batches/"+id, "", "")
			if batch["status"] == "completed" {
				break
			}
			if time.Now().After(deadline) {
				t.Fatalf("batch %s still %v after 5 s", id, batch["status"])
			}
		}
		runs += 2
		ran := probe.waitRuns(runs)[runs-2:]
		t.Logf("batch with caller trace %q: its trace %s; runs (request id, trace id) %q; results %v", callerTrace, batchTrace, ran, batch["results"])

		results, _ := batch["results"].([]any)
		var named, ranIDs []string
		for _, r := range results {
			item, _ := r.(map[string]any)
			rid, _ := item["request_id"].(string)
			if rid == "" {
				t.Errorf("batch result %v names no request_id", item)
				continue
			}
			named = append(named, rid)
			if !wh.holds(rid) {
				t.Errorf("the request log holds no entry under the request_id %s that a batch result names", rid)
			}
		}
		for _, r := range ran {
			ranIDs = append(ranIDs, r.requestID)
			if r.traceID != batchTrace {
				t.Errorf("a batch item ran with trace id %q, want the batch request's %q", r.traceID, batchTrace)
			}
		}
		sort.Strings(named)
		sort.Strings(ranIDs)
		if strings.Join(named, ",") != strings.Join(ranIDs, ",") {
			t.Errorf("the batch results name request ids %q; its items ran as %q", named, ranIDs)
		}
		assertOwnRequestIDs(t, "batch item", ran)
	}
}
