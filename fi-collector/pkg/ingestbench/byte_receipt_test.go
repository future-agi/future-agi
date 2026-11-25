package ingestbench

import (
	"context"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func byteReceipt(t *testing.T) Result {
	t.Helper()
	r := retainedResult(t, "exact-head-mock")
	r.Provenance.Collector.Limits = &CollectorLimits{AdmissionModel: "byte-leases-v1", IngestBudgetBytes: 3 << 20, WirePoolBytes: 1 << 20, WorkPoolBytes: 2 << 20}
	r.Provenance.Collector.Budget = CheckBudget(r.Config.Rate, r.Config.BatchSize, r.Config.Concurrency, r.Provenance.Collector.Limits, 0)
	if err := ValidateResult(r); err != nil {
		t.Fatal(err)
	}
	return r
}

func TestByteReceiptRejectsImpossibleEvidence(t *testing.T) {
	for _, tc := range []struct {
		name, field string
		mutate      func(*Result)
	}{
		{"no-samples", "byte_pool_samples", func(r *Result) { r.Backlog.BytePoolSamples = 1 }},
		{"no-retained-samples", "byte_pool_samples", func(r *Result) { r.Backlog.Samples = 1; r.Backlog.BytePoolSamples = 1 }},
		{"only-failed-samples", "byte_pool_samples", func(r *Result) {
			r.Backlog.Samples = 1
			r.Backlog.KeptSamples = 1
			r.Backlog.FailedSamples = 1
			r.Backlog.BytePoolSamples = 1
		}},
		{"peak-without-samples", "byte_pool_samples", func(r *Result) { r.Backlog.PeakWorkBytes = 1 }},
		{"budget-without-samples", "byte_pool_samples", func(r *Result) { r.Backlog.IngestBudgetBytes = 1 }},
		{"samples-without-budget", "ingest_budget_bytes", func(r *Result) { r.Backlog.Samples = 1; r.Backlog.KeptSamples = 1; r.Backlog.BytePoolSamples = 1 }},
		{"partition-off-one", "byte-pool", func(r *Result) { r.Provenance.Collector.Limits.WirePoolBytes++ }},
		{"zero-work-pool", "byte-pool", func(r *Result) { r.Provenance.Collector.Limits.WorkPoolBytes = 0 }},
		{"wrapped-pool-sum", "byte-pool", func(r *Result) {
			r.Provenance.Collector.Limits.WirePoolBytes = math.MaxInt64
			r.Provenance.Collector.Limits.WorkPoolBytes = math.MaxInt64
		}},
		{"row-cap-on-byte-model", "max_pending_rows", func(r *Result) { r.Provenance.Collector.Limits.MaxPendingRows = 1 }},
		{"sufficiency-claim", "budget_check", func(r *Result) { r.Provenance.Collector.Budget.Sufficient = true }},
		{"row-sizing-on-byte-model", "budget_check", func(r *Result) { r.Provenance.Collector.Budget.RequiredPendingRows = 1 }},
		{"wrong-model", "budget_check", func(r *Result) { r.Provenance.Collector.Budget.AdmissionModel = "legacy-row-count" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := byteReceipt(t)
			tc.mutate(&r)
			if err := ValidateResult(r); err == nil || !strings.Contains(err.Error(), tc.field) {
				t.Fatalf("want %s rejection, got %v", tc.field, err)
			}
		})
	}
}

func TestByteReceiptAllowsRestartPeaksAndLegacyMetadata(t *testing.T) {
	r := byteReceipt(t)
	// The last sampled budget may be lower after a restart; the run-wide peaks
	// must not be bounded against that one budget or the pre-load limits.
	r.Backlog.Samples, r.Backlog.KeptSamples, r.Backlog.BytePoolSamples = 2, 2, 2
	r.Backlog.IngestBudgetBytes, r.Backlog.PeakWorkBytes, r.Backlog.PeakWireBytes = 1024, 2<<20, 1<<20
	if err := ValidateResult(r); err != nil {
		t.Fatal(err)
	}
	legacy := retainedResult(t, "exact-head-mock")
	legacy.Provenance.Collector.Ingest = []byte(`{"future_opaque_metadata":{"budget_bytes":"not typed evidence"}}`)
	if err := ValidateResult(legacy); err != nil {
		t.Fatal(err)
	}
}

func TestInvalidByteHealthRemainsDiagnostic(t *testing.T) {
	body := `{"status":"ok","limits":{"admission_model":"byte-leases-v1","ingest_budget_bytes":3145728,"wire_pool_bytes":1048577,"work_pool_bytes":2097152}}`
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(body)) }))
	defer ts.Close()
	info := FetchCollectorHealth(context.Background(), ts.Client(), ts.URL)
	if info.Limits != nil || !strings.Contains(info.Error, "byte-pool") {
		t.Fatalf("invalid observed limits retained: %+v", info)
	}
	r, err := Run(context.Background(), testConfig(), Options{})
	if err != nil {
		t.Fatal(err)
	}
	r.Status = "preflight-failed"
	r.Provenance.Collector = info
	r.Provenance.Collector.Budget = CheckBudget(r.Config.Rate, r.Config.BatchSize, r.Config.Concurrency, info.Limits, 0)
	if err := ValidateResult(r); err != nil {
		t.Fatal(err)
	}
	p, err := Publish(r, PublishOptions{})
	if err != nil || p.Headline.Status != "withheld" {
		t.Fatalf("diagnostic publication: %v %+v", err, p.Headline)
	}
}

func TestInvalidByteTelemetryIsAFailedSample(t *testing.T) {
	for _, ingest := range []string{
		`{"budget_bytes":-1}`, `{"budget_bytes":0,"work":{"in_use_bytes":1}}`,
		`{"budget_bytes":100,"work":{"in_use_bytes":-1}}`,
		`{"budget_bytes":100,"work":{"in_use_bytes":80},"wire":{"in_use_bytes":21}}`,
	} {
		body := `{"stats":{},"admission":{"buffered_rows":0,"pending_rows":0,"max_pending_rows":0},"ingest":` + ingest + `}`
		s := ParseCollectorHealth(200, []byte(body))
		if s.OK || s.Error == "" {
			t.Fatalf("invalid sample accepted: %+v", s)
		}
		b := summariseBacklog([]CollectorSample{s}, 1, 1, time.Second, 1, 1, time.Second)
		if b.BytePoolSamples != 0 || b.Status != "errors" {
			t.Fatalf("failed sample became a measurement: %+v", b)
		}
	}
}
