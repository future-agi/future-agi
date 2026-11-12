package ingestbench

import (
	"bytes"
	"math"
	"strings"
	"testing"
	"time"
)

// A self-contained, internally consistent receipt; no collector is contacted.
func repairReceipt() Result {
	c := testConfig()
	c.Execute, c.Rate, c.Duration, c.BatchSize = true, 12, time.Second, 4
	r := newResult(c)
	r.Status, r.Started, r.StopReason, r.LoadSeconds = "measured-profile-complete", time.Unix(1700000000, 0).UTC(), "planned-span-count", 1.01
	r.Counters = Counters{Generated: 12, Attempted: 12, SuccessStatus: 12, Accepted: 12, Acknowledged: 12, Requests: 3, RequestOutcomes: RequestOutcomes{Acknowledged: 3}}
	r.Statuses = map[string]int64{"http:200": 3}
	r.RequestLatency = Percentiles{Count: 3, P50: 1, P95: 1, P99: 1, P999: 1, Max: 1}
	r.RequestLatencyFromScheduled = r.RequestLatency
	r.Lag = LagResult{Method: "sentinel_write_then_poll_acknowledged_only", Registered: 3, Sampled: 3, Stride: 1, MaxSentinels: c.MaxSentinels, PollIntervalMS: 100, WindowSeconds: 10,
		Latency: r.RequestLatency, FromScheduled: r.RequestLatency, LowerFromScheduled: r.RequestLatency,
		Buckets: []HistogramBucket{{LowerMS: 1, UpperMS: 1.004, Count: 3}}, Windows: []LagWindow{{EndS: 10, Seen: 3, Upper: r.RequestLatency}}}
	r.Schedule.ScheduledSpans = 12
	r.Schedule.StartDelay.Count = r.Counters.Requests
	r.Verification = Verification{Complete: true, Method: "chunked-exact-id-per-request-outcome", Rows: 12, DistinctExpected: 12, ScannedAttempted: 12, Chunks: 1, TotalChunks: 1, ChunkSpans: 12}
	return r
}

func TestRepairHeadlineDuplicatesAndStatus(t *testing.T) {
	for _, tc := range []struct {
		name, reason string
		mutate       func(*Result)
	}{
		{"positive", "", func(*Result) {}},
		{"duplicate-only", "visible duplicate", func(r *Result) { r.Verification.DuplicateRows = 1; r.Verification.Rows++ }},
		{"failed-status-only", "status", func(r *Result) { r.Status = "measured-with-errors" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := repairReceipt()
			tc.mutate(&r)
			p, err := Publish(r, PublishOptions{MinSustained: 500 * time.Millisecond})
			if err != nil {
				t.Fatal(err)
			}
			if tc.reason == "" {
				if p.Headline.Status != "published" {
					t.Fatal(p.Headline)
				}
				return
			}
			if p.Headline.Status != "withheld" || !strings.Contains(strings.Join(p.Headline.WithheldReasons, ";"), tc.reason) {
				t.Fatalf("ineligible headline: %+v", p.Headline)
			}
			if err := Report(&bytes.Buffer{}, r); err != nil {
				t.Fatalf("failed result must remain renderable: %v", err)
			}
		})
	}
}

func TestRepairImportedEvidence(t *testing.T) {
	for _, tc := range []struct {
		name, field string
		mutate      func(*Result)
	}{
		{"schema-only", "config.run_id", func(r *Result) { *r = Result{SchemaVersion: SchemaVersion} }},
		{"identity", "attempted_spans", func(r *Result) { r.Counters.Ambiguous++; r.Counters.Unacknowledged++ }},
		{"negative", "whole_request_rejected_spans", func(r *Result) {
			r.Counters.WholeRequestRejected = -1
			r.Counters.RejectedRetryable = -1
			r.Counters.Ambiguous = 1
		}},
		{"overflow", "attempted_spans", func(r *Result) {
			r.Counters.Acknowledged = math.MaxInt64
			r.Counters.PartialRejected = math.MaxInt64
			r.Counters.Ambiguous = 14
		}},
		{"plan", "planned_spans", func(r *Result) { r.PlannedSpans++ }},
		{"schedule", "schedule", func(r *Result) { r.Schedule.ScheduledSpans++ }},
		{"verified", "verification", func(r *Result) { r.Verification.DistinctExpected++ }},
		{"rows-overflow", "verification", func(r *Result) { r.Verification.DuplicateRows = math.MaxUint64; r.Verification.UnexpectedRows = 1 }},
		{"missing-decomposition", "verification", func(r *Result) { r.Verification.MissingBy.AcknowledgedMissing++ }},
		{"short-clock", "load_elapsed_seconds", func(r *Result) { r.LoadSeconds = .001 }},
		{"negative-clock", "load_elapsed_seconds", func(r *Result) { r.LoadSeconds = -1 }},
		{"infinite-clock", "load_elapsed_seconds", func(r *Result) { r.LoadSeconds = math.Inf(1) }},
		{"nan-clock", "load_elapsed_seconds", func(r *Result) { r.LoadSeconds = math.NaN() }},
		{"missing-start", "started_at", func(r *Result) { r.Started = time.Time{} }},
		{"missing-status", "status", func(r *Result) { r.Status = "" }},
		{"missing-stop", "stop_reason", func(r *Result) { r.StopReason = "" }},
		{"unknown-stop", "stop_reason", func(r *Result) { r.StopReason = "invented" }},
		{"missing-project", "config.project_id", func(r *Result) { r.Config.ProjectID = "" }},
		{"missing-environment", "config.environment", func(r *Result) { r.Config.Environment = "" }},
		{"unrepresentable-clock", "load_elapsed_seconds", func(r *Result) { r.LoadSeconds = math.MaxFloat64 }},
		{"infinite-clock-sample", "clock.before_load", func(r *Result) { r.Clock.Start.OffsetMS = math.Inf(-1) }},
		{"negative-probe-count", "application_reads", func(r *Result) { r.Reads = []ProbeResult{{Censored: -1}} }},
		{"negative-latency", "otlp_request_latency", func(r *Result) { r.RequestLatency.P99 = -1 }},
		{"false-loss-flag", "verification", func(r *Result) { r.Verification.DataLossDetected = true }},
		{"missing-fraction", "verification", func(r *Result) { r.Verification.MissingFraction = 0.5 }},
		{"incomplete-scan", "verification", func(r *Result) { r.Verification.ScannedAttempted = 11; r.Verification.Unscanned = 1 }},
		{"request-overflow", "counters.requests", func(r *Result) {
			r.Counters.RequestOutcomes = RequestOutcomes{Acknowledged: math.MaxInt64, PartialRejection: math.MaxInt64, Ambiguous: 5}
		}},
		{"accepted-alias", "counters.accepted_spans", func(r *Result) { r.Counters.Accepted-- }},
		{"unacknowledged", "unacknowledged_spans", func(r *Result) { r.Counters.Unacknowledged++ }},
		{"request-span-disagreement", "counters.ambiguous_spans", func(r *Result) {
			r.Counters.RequestOutcomes = RequestOutcomes{Acknowledged: 2, Ambiguous: 1}
			r.Counters.RequestErrors = 1
		}},
		{"persisted-class-disagreement", "verification.ambiguous", func(r *Result) {
			r.Counters.Acknowledged, r.Counters.Accepted, r.Counters.SuccessStatus = 8, 8, 8
			r.Counters.Ambiguous, r.Counters.Unacknowledged, r.Counters.RequestErrors = 4, 4, 1
			r.Counters.RequestOutcomes = RequestOutcomes{Acknowledged: 2, Ambiguous: 1}
			// All 12 were found stored, but none is attributed to ambiguity.
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := repairReceipt()
			tc.mutate(&r)
			var out bytes.Buffer
			for _, err := range []error{Report(&out, r), func() error { _, e := Publish(r, PublishOptions{}); return e }()} {
				if err == nil || !strings.Contains(err.Error(), tc.field) {
					t.Errorf("want field-specific %s error, got %v", tc.field, err)
				}
			}
			if out.Len() != 0 {
				t.Error("invalid evidence emitted report bytes")
			}
		})
	}
}

func TestRepairBudgetWideArithmetic(t *testing.T) {
	for _, tc := range []struct {
		rate              int64
		batch, concurrent int
		limits            CollectorLimits
	}{
		{math.MaxInt64, 100, 2, collectorDefaultLimits},
		{100, math.MaxInt, 2, collectorDefaultLimits},
		{100, 100, math.MaxInt, collectorDefaultLimits},
		{1000000, 10000, 128, CollectorLimits{BatchMaxAgeMS: math.MaxInt64}},
	} {
		b := CheckBudget(tc.rate, tc.batch, tc.concurrent, &tc.limits, 0)
		if b.RecommendedSetting != "" || b.Sufficient || b.RequiredPendingRows < 0 {
			t.Errorf("invalid inputs produced a recommendation: %+v", b)
		}
	}
	limits := CollectorLimits{BatchMaxRows: 5000, BatchMaxAgeMS: math.MaxInt64}
	if n := RequiredPendingRows(1000000, 10000, 128, limits); n != 3285000 {
		t.Fatalf("wide intermediate capped by batch trigger: %d", n)
	}
}

func TestRepairLegitimateDiagnosticStates(t *testing.T) {
	for _, state := range []string{"failed", "incomplete", "censored", "preflight", "no-probes"} {
		t.Run(state, func(t *testing.T) {
			r := repairReceipt()
			switch state {
			case "failed":
				r.Status = "measured-with-errors"
			case "incomplete":
				r.Status = "incomplete"
				r.Verification = Verification{Method: "chunked-exact-id-per-request-outcome", Missing: 12, MissingFraction: 1, Unscanned: 12, TotalChunks: 1, ChunkSpans: 12, Error: "budget exhausted before first chunk"}
			case "censored":
				r.Reads = []ProbeResult{{Status: "window-censored", Censored: 1, CensoredElapsed: Percentiles{Count: 1, Max: 500}}}
			case "preflight":
				r = newResult(r.Config)
				r.Status = "preflight-failed"
				r.Verification.PreexistingRunRows = 1
			case "no-probes":
				r.Reads, r.DirectReads = nil, nil
			}
			if err := Report(&bytes.Buffer{}, r); err != nil {
				t.Fatal(err)
			}
			p, err := Publish(r, PublishOptions{})
			if err != nil || p.Headline.Status != "withheld" {
				t.Fatalf("diagnostic publication: %v %+v", err, p.Headline)
			}
		})
	}
}

func TestRepairExactPlannedSpans(t *testing.T) {
	for _, tc := range []struct {
		rate     int64
		duration time.Duration
		want     int64
	}{
		{1000, 1001 * time.Millisecond, 1001}, {1000, 1001*time.Millisecond - time.Nanosecond, 1000}, {1000, 1001*time.Millisecond + time.Nanosecond, 1001},
		{1000000, 24 * time.Hour, 86400000000}, {1000000, 24*time.Hour - time.Nanosecond, 86399999999},
		{1000000, time.Nanosecond, 0}, {1, time.Nanosecond, 0}, {1000000, time.Microsecond, 1}, {4, 5 * time.Second, 20},
	} {
		c := testConfig()
		c.Rate, c.Duration = tc.rate, tc.duration
		if got := c.PlannedSpans(); got != tc.want {
			t.Errorf("%d x %s = %d, want %d", tc.rate, tc.duration, got, tc.want)
		}
	}
	// Existing exact inputs retain their integer due times and batch schedule.
	c := testConfig()
	c.Rate, c.Duration, c.BatchSize = 4, 5*time.Second, 4
	for seq := int64(0); seq < 20; seq += 4 {
		if c.batchDue(seq) != time.Duration(seq/4)*time.Second {
			t.Fatal("due time changed")
		}
	}
	for sec := 0; sec <= 5; sec++ {
		if got := c.scheduledSpans(time.Duration(sec) * time.Second); got != min(20, int64(sec+1)*4) {
			t.Fatal(got)
		}
	}
}
