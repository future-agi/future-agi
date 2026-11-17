package logging

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// costKeys are the log entry's cost amounts: present, 0 included, whenever
// the entry's cost_source names a price, and absent when it is unpriced.
var costKeys = []string{"cost_upstream", "cost_markup", "cost_markup_bps", "cost_landed"}

// zeroCostRequest is a served request whose billing is b, as the cost plugin
// leaves it.
func zeroCostRequest(t *testing.T, id string, b models.BillingState) (TraceRecord, *models.RequestContext) {
	t.Helper()
	rec := TraceRecord{RequestID: id, Timestamp: time.Unix(1700000000, 0).UTC(), Model: "gpt-4o",
		Provider: "openai", PromptTokens: 10, CompletionTokens: 5, TotalTokens: 15, StatusCode: 200,
		Metadata: map[string]string{"cost": "0"}}
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.RequestID = id
	rc.Model = rec.Model
	rc.Provider = rec.Provider
	rc.Billing = b
	return rec, rc
}

// sentZeroCostEntries sends each request through the flusher, the way the
// webhook receives it, and returns the entries by request id.
func sentZeroCostEntries(t *testing.T, enqueue func(f *LogFlusher)) map[string]map[string]any {
	t.Helper()
	wh := newBatchRecorder(t)
	f := NewLogFlusher(wh.URL, "", time.Hour, 100)
	enqueue(f)
	f.flush()
	batches := wh.all()
	if len(batches) != 1 {
		t.Fatalf("webhook got %d batches; want 1", len(batches))
	}
	out := map[string]map[string]any{}
	for _, e := range batches[0] {
		id, _ := e["request_id"].(string)
		out[id] = e
	}
	return out
}

// A priced call reports its cost amounts even when they are 0, so Django
// stores 0 rather than reading the gap as missing (or filling it from the
// metadata's percent); an unpriced call reports none of them, never 0; a v1
// entry carries no v2 key at all.
func TestLogEntryV2ZeroCostsAreSent(t *testing.T) {
	managed := models.BillingState{Mode: models.BillingModeManaged, CredentialType: models.CredentialManaged,
		UpstreamMicros: 9, BilledMicros: 9, MarkupMicros: 0, MarkupBps: 0, LandedMicros: 0,
		CostSource: models.CostSourcePriceCard}
	// writeRequestBilling's BYOK branch: a source, upstream 0, the rest untouched.
	byok := models.BillingState{Mode: models.BillingModeBYOK, CredentialType: models.CredentialBYOK,
		CostSource: models.CostSourceCatalog}
	// Nonzero amounts: they must still be left out, not sent, for an unpriced call.
	unpriced := models.BillingState{Mode: models.BillingModeManaged, CredentialType: models.CredentialManaged,
		UpstreamMicros: 1200, BilledMicros: 1260, MarkupMicros: 60, MarkupBps: 500, LandedMicros: 1300,
		CostSource: models.CostSourceUnpriced}
	// No cost source: refused before dispatch, a cache hit, nothing billable.
	none := models.BillingState{Mode: models.BillingModeManaged}

	entries := sentZeroCostEntries(t, func(f *LogFlusher) {
		for id, b := range map[string]models.BillingState{
			"req_managed_zero_markup": managed, "req_byok_zero": byok,
			"req_unpriced": unpriced, "req_no_source": none,
		} {
			rec, rc := zeroCostRequest(t, id, b)
			f.EnqueueRequest(rec, rc, DefaultLogEntryOptions())
		}
		rec, _ := zeroCostRequest(t, "req_v1", models.BillingState{})
		f.Enqueue(rec)
	})
	if len(entries) != 5 {
		t.Fatalf("webhook got %d entries %v; want 5", len(entries), entries)
	}
	for id, e := range entries {
		if id == "req_v1" {
			continue
		}
		if errs := schematest.Validate(t, "log-entry-v2.schema.json", e); len(errs) > 0 {
			t.Errorf("%s fails log-entry-v2.schema.json: %v", id, errs)
		}
	}

	for _, tc := range []struct {
		id, source                    string
		upstream, markup, bps, landed float64
	}{
		{"req_managed_zero_markup", "price_card", 0.000009, 0, 0, 0},
		{"req_byok_zero", "catalog", 0, 0, 0, 0},
	} {
		e := entries[tc.id]
		for _, k := range costKeys {
			if !hasKey(e, k) {
				t.Errorf("%s: the priced entry left out %s: %v", tc.id, k, e)
			}
		}
		if e["cost_upstream"] != tc.upstream || e["cost_markup"] != tc.markup ||
			e["cost_markup_bps"] != tc.bps || e["cost_landed"] != tc.landed {
			t.Errorf("%s: cost_upstream %v, cost_markup %v, cost_markup_bps %v, cost_landed %v; want %v, %v, %v, %v",
				tc.id, e["cost_upstream"], e["cost_markup"], e["cost_markup_bps"], e["cost_landed"],
				tc.upstream, tc.markup, tc.bps, tc.landed)
		}
		if e["cost_source"] != tc.source {
			t.Errorf("%s: cost_source = %v; want %s", tc.id, e["cost_source"], tc.source)
		}
	}

	if e := entries["req_unpriced"]; e["cost_source"] != "unpriced" {
		t.Errorf("unpriced call: cost_source = %v; want unpriced", e["cost_source"])
	} else {
		for _, k := range costKeys {
			if v, ok := e[k]; ok && v != nil {
				t.Errorf("unpriced call sent %s = %v; want it absent (never 0)", k, v)
			}
		}
	}

	if e := entries["req_no_source"]; hasKey(e, "cost_source") {
		t.Errorf("an entry with no cost source sent cost_source = %v", e["cost_source"])
	} else {
		for _, k := range costKeys {
			if hasKey(e, k) {
				t.Errorf("an entry with no cost source sent %s = %v", k, e[k])
			}
		}
	}

	v1 := entries["req_v1"]
	for _, k := range append([]string{"cost_source", "generation_id"}, costKeys...) {
		if hasKey(v1, k) {
			t.Errorf("a v1 entry carries the v2 key %s = %v", k, v1[k])
		}
	}
}

// The spool keeps the same entry the webhook gets: a priced zero stays 0.
func TestSpooledEntryKeepsZeroCosts(t *testing.T) {
	f := NewLogFlusher("http://127.0.0.1:0", "", time.Hour, 100)
	rec, rc := zeroCostRequest(t, "req_spool_zero", models.BillingState{Mode: models.BillingModeBYOK,
		CredentialType: models.CredentialBYOK, CostSource: models.CostSourceCatalog})
	f.EnqueueRequest(rec, rc, DefaultLogEntryOptions())
	raws, err := f.spoolEntries([]TraceRecord{rec})
	if err != nil || len(raws) != 1 {
		t.Fatalf("spoolEntries = %d entries, %v", len(raws), err)
	}
	m := toJSONMap(t, raws[0])
	for _, k := range costKeys {
		if v, ok := m[k]; !ok || v != 0.0 {
			t.Errorf("the spooled entry's %s = %v (present %v); want 0", k, v, ok)
		}
	}
}
