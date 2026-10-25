package budget

// A budget refusal, as the pipeline runs it, reaches the request-log batch
// with the gateway's error code and the refusal metadata. Django raises budget.exceeded (webhook and email) only from a log
// entry's top-level error_code (futureagi/agentcc/services/log_ingestion.py
// _budget_error_code). The budget plugin, the engine, the logging plugin and
// the flusher's HTTP batch run here as the gateway wires them; nothing is
// built by hand past the request's auth metadata.
//
// The refused entries are pinned to api_contracts/gateway/fixtures, which
// Django's test agentcc/tests/test_budget_refusal_log_entry.py feeds to log
// ingestion, so both suites read the entry the gateway really sends.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
	"time"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

var logBatchFixtures = filepath.Join("..", "..", "..", "..", "api_contracts", "gateway", "fixtures")

// logBatchSink is a stand-in for Django's log webhook: it keeps every
// entry of every batch the flusher posts, in order.
type logBatchSink struct {
	mu      sync.Mutex
	entries []map[string]any
}

func (b *logBatchSink) serve(t *testing.T) *httptest.Server {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var batch struct {
			Logs []map[string]any `json:"logs"`
		}
		if err := json.Unmarshal(body, &batch); err != nil {
			t.Errorf("the flusher posted a batch that is not {\"logs\": [...]}: %v", err)
		}
		b.mu.Lock()
		b.entries = append(b.entries, batch.Logs...)
		b.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(srv.Close)
	return srv
}

// logBatchPipeline is the budget plugin and the logging plugin in one engine,
// the logging plugin posting to a stand-in webhook. send runs one request
// through it; sent closes the logging plugin (its flusher delivers what it
// holds) and returns every entry Django got.
type logBatchPipeline struct {
	engine  *pipeline.Engine
	logging *logging.Plugin
	batches *logBatchSink
}

func newLogBatchPipeline(t *testing.T, budget *Plugin) *logBatchPipeline {
	t.Helper()
	batches := &logBatchSink{}
	srv := batches.serve(t)
	lp := logging.New(config.RequestLoggingConfig{Enabled: true, BufferSize: 16, Workers: 1}, nil)
	lp.SetFlusher(logging.NewLogFlusher(srv.URL, "", time.Hour, 100))
	return &logBatchPipeline{engine: pipeline.NewEngine(budget, lp), logging: lp, batches: batches}
}

// send runs one chat request, with the auth plugin's metadata for the key
// log-batch-key-b of org btOrg plus meta, at start. A request the budget
// admits gets an empty answer from the provider.
func (rp *logBatchPipeline) send(t *testing.T, requestID string, start time.Time, meta map[string]string) error {
	t.Helper()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = requestID
	rc.StartTime = start
	rc.Model, rc.Provider = "gpt-4o", "openai"
	rc.Privacy.ContentMode = models.ContentModeFull // as the chat pipeline's step 5 resolves it
	for k, v := range map[string]string{
		"auth_key_id":           "key_log_batch_b",
		"auth_key_name":         "log-batch-key-b",
		tenant.MetadataKeyOrgID: btOrg,
		"org_id":                btOrg,
	} {
		rc.Metadata[k] = v
	}
	for k, v := range meta {
		rc.Metadata[k] = v
	}
	return rp.engine.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error {
		rc.Response = &models.ChatCompletionResponse{}
		return nil
	})
}

func (rp *logBatchPipeline) sent(t *testing.T) []map[string]any {
	t.Helper()
	rp.logging.Close()
	rp.batches.mu.Lock()
	defer rp.batches.mu.Unlock()
	return rp.batches.entries
}

// logBatchRefusal is what a refused entry must carry: the error code and the
// refusal metadata's level, period, period start and action.
type logBatchRefusal struct {
	code, level, period, start, action string
}

func checkLogBatchRefusal(t *testing.T, entry map[string]any, want logBatchRefusal, fixture string) {
	t.Helper()
	b, _ := json.Marshal(entry)
	t.Logf("%s entry: %s", want.code, b)
	if entry["error_code"] != want.code {
		t.Errorf("error_code = %v; want %s (the budget plugin's APIError code)", entry["error_code"], want.code)
	}
	if entry["status_code"] != 429.0 || entry["is_error"] != true {
		t.Errorf("status_code = %v, is_error = %v; want 429, true", entry["status_code"], entry["is_error"])
	}
	meta, _ := entry["metadata"].(map[string]any)
	for k, v := range map[string]string{
		"budget_exceeded_level": want.level, "budget_period": want.period,
		"budget_period_start": want.start, "budget_action": want.action,
	} {
		if meta[k] != v {
			t.Errorf("metadata %s = %v; want %s (the refusal metadata)", k, meta[k], v)
		}
	}
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", entry); len(errs) > 0 {
		t.Errorf("the entry fails log-entry-v2.schema.json: %v", errs)
	}
	pinLogBatchFixture(t, entry, fixture)
}

// pinLogBatchFixture checks entry against the fixture Django's test feeds to
// log ingestion, field for field. latency_ms is the one field the wall clock
// sets (buildRecord reads rc.Elapsed), so it is checked and then taken from
// the fixture. WRITE_LOG_BATCH_FIXTURES=1 writes the entry there instead.
func pinLogBatchFixture(t *testing.T, entry map[string]any, name string) {
	t.Helper()
	path := filepath.Join(logBatchFixtures, name)
	got := make(map[string]any, len(entry))
	for k, v := range entry {
		got[k] = v
	}
	if ms, ok := got["latency_ms"].(float64); !ok || ms < 0 {
		t.Errorf("latency_ms = %v; want a non-negative number", got["latency_ms"])
	}
	got["latency_ms"] = 3.0
	pretty, _ := json.MarshalIndent(got, "", "  ")
	pretty = append(pretty, '\n')
	if os.Getenv("WRITE_LOG_BATCH_FIXTURES") == "1" {
		if err := os.WriteFile(path, pretty, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read the fixture Django's test feeds to log ingestion: %v", err)
	}
	var want map[string]any
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("the gateway's entry differs from %s, which Django's test feeds to log ingestion; "+
			"if the change is intended, write this entry there (WRITE_LOG_BATCH_FIXTURES=1):\n%s", path, pretty)
	}
}

// An org level with the Block action refuses with org_budget_exceeded.
func TestOrgBudgetBlockReachesTheLogBatch(t *testing.T) {
	p, _ := btPlugin(t, &tenant.BudgetsConfig{Enabled: true, OrgLimit: 0.000001, HardLimit: true})
	btSpend(t, p, "1.00", nil)
	rp := newLogBatchPipeline(t, p)

	if err := rp.send(t, "req_log_batch_org_block", btNow, nil); err == nil {
		t.Fatal("the org budget admitted a request past its limit")
	}
	entries := rp.sent(t)
	if len(entries) != 1 {
		t.Fatalf("Django got %d entries; want 1", len(entries))
	}
	checkLogBatchRefusal(t, entries[0], logBatchRefusal{
		"org_budget_exceeded", "org", "monthly", "2026-10-01T00:00:00Z", "block",
	}, "log-entry-v2-budget-org-block.json")
}

// An org level with the Throttle action at 2 requests a minute admits two
// requests in the minute and refuses the third with budget_throttled.
func TestOrgBudgetThrottleReachesTheLogBatch(t *testing.T) {
	p, _ := btPlugin(t, &tenant.BudgetsConfig{Enabled: true, OrgLimit: 0.000001, Action: "throttle", ThrottleRPM: 2})
	btSpend(t, p, "1.00", nil)
	rp := newLogBatchPipeline(t, p)

	ids := []string{"req_log_batch_throttle_1", "req_log_batch_throttle_2", "req_log_batch_throttle_3"}
	for i, id := range ids {
		err := rp.send(t, id, btNow, nil)
		if (err != nil) != (i == 2) {
			t.Fatalf("request %d: error %v; want two admitted, then the third refused", i+1, err)
		}
	}
	entries := rp.sent(t)
	if len(entries) != len(ids) {
		t.Fatalf("Django got %d entries; want %d", len(entries), len(ids))
	}
	var codes []any
	for i, e := range entries {
		if e["request_id"] != ids[i] {
			t.Fatalf("entry %d is %v; want %s (batch order)", i, e["request_id"], ids[i])
		}
		codes = append(codes, e["error_code"])
	}
	if want := []any{nil, nil, "budget_throttled"}; !reflect.DeepEqual(codes, want) {
		t.Errorf("error codes = %v; want %v", codes, want)
	}
	for _, e := range entries[:2] {
		if e["status_code"] != 200.0 {
			t.Errorf("%v: status_code = %v; an admitted request is a 200", e["request_id"], e["status_code"])
		}
	}
	checkLogBatchRefusal(t, entries[2], logBatchRefusal{
		"budget_throttled", "org", "monthly", "2026-10-01T00:00:00Z", "throttle",
	}, "log-entry-v2-budget-throttled.json")
}

// The operator's config.yaml budgets refuse with budget_exceeded. Every level
// counts over the total period: the node tracker reads the wall clock, and
// the total period's start (the zero time) is the one it cannot move.
func TestGlobalBudgetBlockReachesTheLogBatch(t *testing.T) {
	tracker := budgetpkg.NewTracker(config.BudgetsConfig{
		Enabled: true, DefaultPeriod: "total",
		Teams: map[string]config.BudgetLevelConfig{"eng": {Limit: 0.000001, Period: "total", Hard: boolPtr(true)}},
	})
	p := New(tracker, testPricing(), true, nil)
	p.now = func() time.Time { return btNow }
	eng := map[string]string{"key_team": "eng"}
	btSpend(t, p, "1.00", eng)
	rp := newLogBatchPipeline(t, p)

	if err := rp.send(t, "req_log_batch_global_block", btNow, eng); err == nil {
		t.Fatal("the team budget admitted a request past its limit")
	}
	entries := rp.sent(t)
	if len(entries) != 1 {
		t.Fatalf("Django got %d entries; want 1", len(entries))
	}
	checkLogBatchRefusal(t, entries[0], logBatchRefusal{
		"budget_exceeded", "team:eng", "total", "0001-01-01T00:00:00Z", "block",
	}, "log-entry-v2-budget-exceeded-global.json")
}
