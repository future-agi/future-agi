package logging

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Django raises budget.exceeded (webhook and email) only from a log entry's
// top-level error_code (futureagi/agentcc/services/log_ingestion.py
// _budget_error_code), so the request-log batch carries the gateway's error
// code, and a budget block or throttle raises its notification (GW-E2E-012).

// budgetRefusalFixture is the entry the gateway sends for the budget block
// below, as Django's tests read it (agentcc/tests/test_budget_refusal_log_entry.py).
var budgetRefusalFixture = filepath.Join("..", "..", "..", "..", "api_contracts", "gateway", "fixtures",
	"log-entry-v2-budget-refusal.json")

// budgetBlockRequest is a key-level org budget block as the gateway leaves
// it for the logging plugin: the auth metadata, the refusal metadata as the
// budget plugin's stampRefusal writes it with org_budget_blocked, and the 429
// org_budget_exceeded APIError (plugins/budget/budget.go, past()).
func budgetBlockRequest(t *testing.T) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.RequestID = "req_log_batch_budget_block"
	rc.StartTime = time.Date(2026, 10, 22, 23, 50, 44, 0, time.UTC)
	rc.Model = "gpt-4o-mini"
	for k, v := range map[string]string{
		"auth_key_id":           "key_log_batch_b",
		"auth_key_name":         "log-batch-key-b",
		"key_org_id":            "org_log_batch",
		"org_id":                "org_log_batch",
		"org_budget_blocked":    "true",
		"budget_exceeded_level": "key:log-batch-key-b",
		"budget_period":         "monthly",
		"budget_period_start":   "2026-10-01T00:00:00Z",
		"budget_action":         "block",
	} {
		rc.Metadata[k] = v
	}
	rc.AddError(&models.APIError{
		Status:  429,
		Type:    models.ErrTypeRateLimit,
		Code:    "org_budget_exceeded",
		Message: "budget exceeded for key 'log-batch-key-b': spent $0.000005 of $0.000001 monthly limit",
	})
	return rc
}

// sentEntry is rec's entry in the batch the flusher posts, as JSON.
func sentEntry(t *testing.T, rec TraceRecord, rc *models.RequestContext, opts LogEntryOptions) map[string]any {
	t.Helper()
	wh := newBatchRecorder(t)
	f := NewLogFlusher(wh.URL, "", time.Hour, 100)
	f.EnqueueRequest(rec, rc, opts)
	f.flush()
	batches := wh.all()
	if len(batches) != 1 || len(batches[0]) != 1 {
		t.Fatalf("batches = %v; want one batch of one entry", batches)
	}
	return batches[0][0]
}

func TestLogBatchCarriesTheBudgetRefusalErrorCode(t *testing.T) {
	rc := budgetBlockRequest(t)
	rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
	rec.LatencyMs = 3 // buildRecord reads the clock; pinned so the entry is stable
	entry := sentEntry(t, rec, rc, LogEntryOptions{IncludeBodies: true, ContentMode: models.ContentModeFull})
	b, _ := json.Marshal(entry)
	t.Logf("budget refusal entry: %s", b)

	if entry["error_code"] != "org_budget_exceeded" {
		t.Errorf("error_code = %v; want org_budget_exceeded (the budget plugin's APIError code)", entry["error_code"])
	}
	if entry["status_code"] != 429.0 || entry["is_error"] != true {
		t.Errorf("status_code = %v, is_error = %v; want 429, true", entry["status_code"], entry["is_error"])
	}
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", entry); len(errs) > 0 {
		t.Errorf("the budget refusal entry fails log-entry-v2.schema.json: %v", errs)
	}

	raw, err := os.ReadFile(budgetRefusalFixture)
	if err != nil {
		t.Fatalf("read the fixture Django's test feeds to log ingestion: %v", err)
	}
	var want map[string]any
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatalf("parse %s: %v", budgetRefusalFixture, err)
	}
	if !reflect.DeepEqual(entry, want) {
		got, _ := json.MarshalIndent(entry, "", "  ")
		t.Errorf("the gateway's entry differs from %s, which Django's test feeds to log ingestion; "+
			"if the change is intended, write this entry there:\n%s", budgetRefusalFixture, got)
	}
}

// Every path a log entry takes keeps the code: the v1 entry of Enqueue, the
// v2 entry under each content mode (none drops error text, never the code:
// the notification must not depend on the org's content mode), and the
// plugin's own ProcessResponse.
func TestLogEntryErrorCodeOnEveryPath(t *testing.T) {
	rc := budgetBlockRequest(t)
	rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
	if rec.ErrorCode != "org_budget_exceeded" {
		t.Fatalf("record.ErrorCode = %q; the record lost the APIError's code", rec.ErrorCode)
	}

	if v1 := toJSONMap(t, buildLogEntryV1(rec)); v1["error_code"] != "org_budget_exceeded" {
		t.Errorf("v1 entry error_code = %v; want org_budget_exceeded", v1["error_code"])
	}
	st := rc.SnapshotV2()
	for _, mode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
		e := toJSONMap(t, buildLogEntry(rec, &st, nil, LogEntryOptions{IncludeBodies: true, ContentMode: mode}))
		if e["error_code"] != "org_budget_exceeded" {
			t.Errorf("content mode %s: error_code = %v; want org_budget_exceeded", mode, e["error_code"])
		}
		if errs := schematest.Validate(t, "log-entry-v2.schema.json", e); len(errs) > 0 {
			t.Errorf("content mode %s: entry fails log-entry-v2.schema.json: %v", mode, errs)
		}
	}

	wh := newBatchRecorder(t)
	f := NewLogFlusher(wh.URL, "", time.Hour, 100)
	p := New(config.RequestLoggingConfig{Enabled: true, BufferSize: 16, Workers: 1}, nil)
	defer p.Close()
	p.SetFlusher(f)
	p.ProcessResponse(context.Background(), rc)
	f.flush()
	batches := wh.all()
	if len(batches) != 1 || len(batches[0]) != 1 {
		t.Fatalf("plugin batches = %v", batches)
	}
	if got := batches[0][0]["error_code"]; got != "org_budget_exceeded" {
		t.Errorf("the plugin's entry error_code = %v; want org_budget_exceeded", got)
	}
}

// A request without an error code has no error_code key: a success, and a
// failure that was not an APIError.
func TestLogEntryOmitsAnAbsentErrorCode(t *testing.T) {
	ok := TraceRecord{RequestID: "req_ok", Timestamp: time.Unix(1700000000, 0).UTC(), Model: "gpt-4o",
		Provider: "openai", StatusCode: 200}
	failed := ok
	failed.RequestID, failed.StatusCode, failed.ErrorMessage = "req_failed", 500, "dial tcp: connection refused"
	for _, rec := range []TraceRecord{ok, failed} {
		if v1 := toJSONMap(t, buildLogEntryV1(rec)); hasKey(v1, "error_code") {
			t.Errorf("%s: v1 entry has error_code %v; want none", rec.RequestID, v1["error_code"])
		}
		rc := models.AcquireRequestContext()
		rc.RequestID = rec.RequestID
		st := rc.SnapshotV2()
		rc.Release()
		if v2 := toJSONMap(t, buildLogEntry(rec, &st, nil, DefaultLogEntryOptions())); hasKey(v2, "error_code") {
			t.Errorf("%s: v2 entry has error_code %v; want none", rec.RequestID, v2["error_code"])
		}
	}
}

func hasKey(m map[string]any, k string) bool {
	_, ok := m[k]
	return ok
}

// A provider's own error code is not the gateway's. The openai and azure
// providers pass an upstream's error.code through as the APIError's code
// (parseProviderError, which sets UpstreamStatus), and Django reads a budget
// code in error_code as the gateway's own refusal, so an upstream answering
// "budget_exceeded" (another gateway in front of the model, say) must not
// raise budget.exceeded. The entry keeps the status and the message, and the
// record keeps the code for the trace line.
func TestLogEntryLeavesOutAProviderErrorCode(t *testing.T) {
	for _, upstream := range []*models.APIError{
		{Status: 429, Type: "insufficient_quota", Code: "budget_exceeded",
			Message: "Budget has been exceeded", UpstreamStatus: 429},
		{Status: 400, Type: "invalid_request_error", Code: "org_budget_exceeded",
			Message: "org budget exceeded upstream", UpstreamStatus: 400},
		{Status: 429, Type: "rate_limit_error", Code: "budget_throttled",
			Message: "throttled upstream", UpstreamStatus: 429},
		models.ErrUpstreamProvider(503, "provider error (HTTP 503): overloaded"),
	} {
		rc := models.AcquireRequestContext()
		rc.RequestID = "req_upstream_" + upstream.Code
		rc.Model, rc.Provider = "gpt-4o", "openai"
		rc.Metadata["auth_key_id"] = "key_log_batch_b"
		rc.AddError(upstream)
		rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
		st := rc.SnapshotV2()
		rc.Release()
		if rec.ErrorCode != upstream.Code {
			t.Errorf("%s: record.ErrorCode = %q; the trace line keeps the provider's code", upstream.Code, rec.ErrorCode)
		}
		for name, e := range map[string]map[string]any{
			"v1": toJSONMap(t, buildLogEntryV1(rec)),
			"v2": toJSONMap(t, buildLogEntry(rec, &st, nil, DefaultLogEntryOptions())),
		} {
			if hasKey(e, "error_code") {
				t.Errorf("%s %s entry: error_code = %v; a provider's code must not reach Django as the gateway's",
					upstream.Code, name, e["error_code"])
			}
			if e["status_code"] != float64(upstream.Status) || e["error_message"] != upstream.Message {
				t.Errorf("%s %s entry: status_code = %v, error_message = %v; want %d, %q",
					upstream.Code, name, e["status_code"], e["error_message"], upstream.Status, upstream.Message)
			}
		}
	}
}
