package ingestbench

import (
	"fmt"
	"math"
	"reflect"
	"strings"
	"time"

	"github.com/google/uuid"
)

// sumEquals compares non-negative counts without ever adding them: neither
// signed nor unsigned overflow can turn impossible evidence into a valid sum.
func sumEquals(total uint64, terms ...uint64) bool {
	for _, n := range terms {
		if n > total {
			return false
		}
		total -= n
	}
	return total == 0
}

// meanWithinIntegerBounds compares the binary64 mean with exact integer
// extrema. Converting extrema to float64 would round some bounds above 2^53
// outward and admit an out-of-range mean, even for a constant population.
func meanWithinIntegerBounds(mean float64, lo, hi int64) bool {
	if math.IsNaN(mean) || math.IsInf(mean, 0) || mean < 0 || mean >= 0x1p63 || lo < 0 || hi < lo {
		return false
	}
	whole := int64(mean) // non-negative and strictly below 2^63: conversion is safe
	return whole >= lo && whole <= hi && (whole < hi || mean == math.Trunc(mean))
}

// measurementNumbers checks counters and measurements using their JSON field
// names in diagnostics. Signed clock corrections and backlog slopes are not
// counts; they may be negative, but must still be finite.
func measurementNumbers(x any, path string) error {
	var walk func(reflect.Value, string) error
	walk = func(v reflect.Value, p string) error {
		switch v.Kind() {
		case reflect.Pointer, reflect.Interface:
			if !v.IsNil() {
				return walk(v.Elem(), p)
			}
		case reflect.Struct:
			// Type-based dispatch covers every current and future occurrence,
			// including optional application/direct probes and lag windows.
			switch x := v.Interface().(type) {
			case Percentiles:
				if x.P50 > x.P95 || x.P95 > x.P99 || x.P99 > x.P999 || x.P999 > x.Max || (x.Count == 0 && (x.P50 != 0 || x.P95 != 0 || x.P99 != 0 || x.P999 != 0 || x.Max != 0)) {
					return fmt.Errorf("%s requires p50_ms <= p95_ms <= p99_ms <= p999_ms <= max_ms and zero values for count=0", p)
				}
			case Distribution:
				if x.Min > x.P50 || x.P50 > x.P95 || x.P95 > x.P99 || x.P99 > x.Max || !meanWithinIntegerBounds(x.Mean, x.Min, x.Max) || (x.Count == 0 && (x.Min != 0 || x.Mean != 0 || x.Max != 0)) {
					return fmt.Errorf("%s requires min <= p50 <= p95 <= p99 <= max, mean within min/max, and zero values for count=0", p)
				}
			}
			for i := 0; i < v.NumField(); i++ {
				f := v.Type().Field(i)
				name := strings.Split(f.Tag.Get("json"), ",")[0]
				if name == "" || name == "-" {
					continue
				}
				if err := walk(v.Field(i), p+"."+name); err != nil {
					return err
				}
			}
		case reflect.Slice, reflect.Array:
			for i := 0; i < v.Len(); i++ {
				if err := walk(v.Index(i), fmt.Sprintf("%s[%d]", p, i)); err != nil {
					return err
				}
			}
		case reflect.Map:
			it := v.MapRange()
			for it.Next() {
				if err := walk(it.Value(), fmt.Sprintf("%s[%v]", p, it.Key())); err != nil {
					return err
				}
			}
		case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
			if v.Int() < 0 && p != "config.seed" && !strings.HasPrefix(p, "resources[") {
				return fmt.Errorf("%s must be non-negative", p)
			}
		case reflect.Float32, reflect.Float64:
			n := v.Float()
			signed := strings.HasPrefix(p, "resources[") || strings.HasSuffix(p, ".clock_offset_applied_ms") || strings.HasSuffix(p, ".server_minus_generator_ms") || strings.HasSuffix(p, ".drift_ms") || strings.HasSuffix(p, ".buffered_rows_slope_rows_per_second") || strings.HasSuffix(p, ".second_half_slope_rows_per_second")
			if math.IsNaN(n) || math.IsInf(n, 0) || (!signed && n < 0) {
				return fmt.Errorf("%s must be finite%s", p, map[bool]string{true: "", false: " and non-negative"}[signed])
			}
		}
		return nil
	}
	return walk(reflect.ValueOf(x), path)
}

// ValidateResult validates imported evidence offline, not permission to execute
// its config. Failed/preflight/incomplete results and absent probes are useful
// diagnostic evidence. No credentials, endpoint access or execution approvals
// are required here. Completion labels never override the actual counters.
func ValidateResult(r Result) error {
	if r.SchemaVersion != SchemaVersion {
		return fmt.Errorf("unsupported result schema %q (want %s)", r.SchemaVersion, SchemaVersion)
	}
	c := r.Config
	if !runIDPattern.MatchString(c.RunID) || c.RunID == "0000000000000000" {
		return fmt.Errorf("config.run_id must be a nonzero 16-character lowercase hex namespace")
	}
	switch r.Status {
	case "dry-run", "preflight-failed", "incomplete", "measured-with-errors", "measured-profile-complete", "measured-recovery-complete":
	default:
		return fmt.Errorf("status is missing or unsupported: %q", r.Status)
	}
	if _, err := uuid.Parse(c.ProjectID); err != nil {
		return fmt.Errorf("config.project_id must be a UUID")
	}
	for _, field := range []struct{ name, value string }{{"project_name", c.ProjectName}, {"environment", c.Environment}, {"topology", c.Topology}} {
		if strings.TrimSpace(field.value) == "" {
			return fmt.Errorf("config.%s is required", field.name)
		}
	}
	if c.Encoding != EncodingJSON && c.Encoding != EncodingProtobuf && c.Encoding != EncodingGRPC {
		return fmt.Errorf("config.encoding must be json, protobuf or grpc")
	}
	if err := ValidateLoadBounds(c.Rate, c.BatchSize, c.Concurrency); err != nil {
		return fmt.Errorf("config: %w", err)
	}
	if c.Duration <= 0 || c.Duration > 24*time.Hour {
		return fmt.Errorf("config.duration_ns must be positive and <= 24h")
	}
	if r.PlannedSpans < 1 || r.PlannedSpans != c.PlannedSpans() {
		return fmt.Errorf("planned_spans must equal floor(config.target_spans_per_second * config.duration_ns / 1e9)")
	}
	if err := r.Counters.CheckIdentity(); err != nil {
		return err
	}
	for _, v := range []struct {
		path  string
		value any
	}{
		{"config", r.Config}, {"provenance", r.Provenance}, {"resources", r.Resources},
		{"load_elapsed_seconds", r.LoadSeconds}, {"schedule", r.Schedule}, {"verification", r.Verification}, {"response_statuses", r.Statuses},
		{"retry_hints", r.RetryHints}, {"otlp_request_latency", r.RequestLatency}, {"otlp_request_latency_from_scheduled_send", r.RequestLatencyFromScheduled},
		{"lag", r.Lag}, {"workload", r.Workload}, {"application_reads", r.Reads}, {"direct_clickhouse_reads", r.DirectReads}, {"collector_backlog", r.Backlog}, {"recovery", r.Recovery}, {"clock", r.Clock}, {"latency_histogram", r.LatencyHistogram},
	} {
		if err := measurementNumbers(v.value, v.path); err != nil {
			return err
		}
	}
	k, s, v := r.Counters, r.Schedule, r.Verification
	if r.LoadSeconds > time.Duration(math.MaxInt64).Seconds() {
		return fmt.Errorf("load_elapsed_seconds exceeds the representable monotonic elapsed duration")
	}
	if k.Attempted > k.Generated || k.Generated > r.PlannedSpans {
		return fmt.Errorf("counters.generated_spans must lie between attempted_spans and planned_spans")
	}
	if s.ScheduledSpans > r.PlannedSpans || !sumEquals(uint64(s.ScheduledSpans), uint64(k.Attempted), uint64(s.NotStartedSpans)) {
		return fmt.Errorf("schedule.scheduled_spans must be <= planned_spans and equal attempted_spans + scheduled_but_not_started_spans")
	}
	if s.ScheduledSpans > 0 && r.LoadSeconds < c.batchDue((s.ScheduledSpans-1)/int64(c.BatchSize)*int64(c.BatchSize)).Seconds() {
		return fmt.Errorf("load_elapsed_seconds precedes the last scheduled batch in schedule.scheduled_spans")
	}
	if k.Requests != (k.Attempted+int64(c.BatchSize)-1)/int64(c.BatchSize) {
		return fmt.Errorf("counters.requests must equal the number of attempted batches")
	}
	o := k.RequestOutcomes
	// The span and request ledgers describe the same batches, not independent
	// totals. Each request contains 1..batch_size spans; a partial response
	// rejects at least one of them. Products are bounded by the validated plan.
	for _, class := range []struct {
		name            string
		spans, requests int64
	}{
		{"whole_request_rejected_retryable_spans", k.RejectedRetryable, o.RejectedRetryable},
		{"whole_request_rejected_non_retryable_spans", k.RejectedNonRetryable, o.RejectedNonRetryable},
		{"ambiguous_spans", k.Ambiguous, o.Ambiguous},
		{"otlp_partial_rejected_spans", k.PartialRejected, o.PartialRejection},
	} {
		if class.spans < class.requests || class.spans > class.requests*int64(c.BatchSize) {
			return fmt.Errorf("counters.%s is incompatible with request_outcomes and batch_size", class.name)
		}
	}
	if k.Acknowledged < o.Acknowledged || k.Acknowledged > (o.Acknowledged+o.PartialRejection)*int64(c.BatchSize)-k.PartialRejected {
		return fmt.Errorf("counters.acknowledged_spans is incompatible with request_outcomes and batch_size")
	}
	if k.SuccessStatus > k.Attempted || k.SuccessStatus < k.Acknowledged+k.PartialRejected || k.PartialResponses > k.Requests || k.PartialResponses < o.PartialRejection || k.RequestErrors > k.Requests {
		return fmt.Errorf("counters.success_status_spans, otlp_partial_responses or request_errors is incompatible with outcome totals")
	}
	if !sumEquals(uint64(k.RequestErrors), uint64(k.RequestOutcomes.RejectedRetryable), uint64(k.RequestOutcomes.RejectedNonRetryable), uint64(k.RequestOutcomes.Ambiguous)) {
		return fmt.Errorf("counters.request_errors does not match rejected and ambiguous request outcomes")
	}
	if r.Status != "dry-run" && r.Status != "preflight-failed" {
		if r.Started.IsZero() {
			return fmt.Errorf("started_at is required for a measured or incomplete run")
		}
		switch r.StopReason {
		case "planned-span-count", "duration-or-cancel", "byte-budget", "invalid-or-oversized-batch":
		default:
			return fmt.Errorf("stop_reason is missing or unsupported after load execution: %q", r.StopReason)
		}
		if r.LoadSeconds <= 0 {
			return fmt.Errorf("load_elapsed_seconds must be positive after load execution")
		}
	}
	if k.Attempted > 0 {
		// A partial/cancelled run need not last the full requested duration, but
		// cannot have sent a batch before its scheduled due time.
		last := (k.Requests - 1) * int64(c.BatchSize)
		if r.LoadSeconds < c.batchDue(last).Seconds() {
			return fmt.Errorf("load_elapsed_seconds precedes the last attempted batch's scheduled send")
		}
	}
	if (r.Status == "measured-profile-complete" || r.Status == "measured-recovery-complete") && r.LoadSeconds < c.Duration.Seconds() {
		return fmt.Errorf("load_elapsed_seconds is shorter than config.duration_ns for a completed profile")
	}
	if v.Method == "" && (k.Attempted != 0 || v.Complete) {
		return fmt.Errorf("verification.method is required when verification was attempted")
	}
	if v.Method != "" && v.Method != "chunked-exact-id-per-request-outcome" {
		return fmt.Errorf("verification.method is unsupported: %q", v.Method)
	}
	attempted := uint64(k.Attempted)
	if !sumEquals(attempted, v.DistinctExpected, v.Missing) {
		return fmt.Errorf("verification.persisted_distinct_attempted_spans + missing_attempted_spans must equal counters.attempted_spans")
	}
	if !sumEquals(attempted, v.ScannedAttempted, v.Unscanned) || v.DistinctExpected > v.ScannedAttempted {
		return fmt.Errorf("verification.scanned_attempted_spans + unscanned_attempted_spans must equal counters.attempted_spans and cover the distinct spans")
	}
	if !sumEquals(v.Rows, v.DistinctExpected, v.DuplicateRows, v.UnexpectedRows) || v.OutsideNamespace > v.UnexpectedRows {
		return fmt.Errorf("verification.scanned_run_rows must equal distinct + duplicate + unexpected rows; outside-namespace rows are unexpected")
	}
	m := v.MissingBy
	if !sumEquals(v.Missing, m.AcknowledgedMissing, m.PartialRejectedMissing, m.RejectedMissing, m.AmbiguousMissing, v.Unscanned) || m.PartialBeyondRejected > m.AcknowledgedMissing {
		return fmt.Errorf("verification.missing_by_acknowledgement and unscanned spans must decompose missing_attempted_spans")
	}
	if m.AcknowledgedMissing > uint64(k.Acknowledged) || m.PartialRejectedMissing > uint64(k.PartialRejected) || m.RejectedMissing > uint64(k.WholeRequestRejected) || m.AmbiguousMissing > uint64(k.Ambiguous) {
		return fmt.Errorf("verification.missing_by_acknowledgement exceeds the corresponding counters")
	}
	if v.PersistedBy.RejectedButPersisted > uint64(k.WholeRequestRejected) || v.PersistedBy.AmbiguousPersisted > uint64(k.Ambiguous) || v.PersistedBy.PartialExcess > uint64(k.PartialRejected) {
		return fmt.Errorf("verification.persisted_without_acknowledgement exceeds the corresponding counters")
	}
	for _, class := range []struct {
		name                      string
		total, missing, persisted uint64
	}{
		{"whole_request_rejected", uint64(k.WholeRequestRejected), m.RejectedMissing, v.PersistedBy.RejectedButPersisted},
		{"ambiguous", uint64(k.Ambiguous), m.AmbiguousMissing, v.PersistedBy.AmbiguousPersisted},
	} {
		// Subtract before comparing so even adversarial uint64 values cannot
		// wrap. An incomplete scan may account for only a prefix of a class.
		if class.missing > class.total || class.persisted > class.total-class.missing ||
			(v.Complete && !sumEquals(class.total, class.missing, class.persisted)) {
			return fmt.Errorf("verification.%s missing and persisted counts disagree with counters", class.name)
		}
	}
	fraction := 0.0
	if attempted > 0 {
		fraction = float64(v.Missing) / float64(attempted)
	}
	if math.Abs(v.MissingFraction-fraction) > 1e-12 {
		return fmt.Errorf("verification.missing_attempted_fraction does not match missing / attempted")
	}
	if v.Chunks > v.TotalChunks || v.ResumedAtChunk > v.Chunks {
		return fmt.Errorf("verification.verified_chunks/resumed_at_chunk exceeds planned or completed chunks")
	}
	if v.Complete && (v.Unscanned != 0 || v.Chunks != v.TotalChunks) {
		return fmt.Errorf("verification.complete requires all attempted spans and planned chunks to be scanned")
	}
	if v.Complete && (v.DataLossDetected != (m.AcknowledgedMissing > 0) || v.ContractViolation != (v.PersistedBy.RejectedButPersisted > 0 || v.PersistedBy.PartialExcess > 0)) {
		return fmt.Errorf("verification data_loss_detected/rejection_contract_violation disagrees with completed evidence")
	}
	return validateResultMeasurements(r)
}
