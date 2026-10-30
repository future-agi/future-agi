package ingestbench

import (
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
)

// consume is a bounded accumulator. Subtracting from a known non-negative
// total makes all aggregate checks safe even for hostile uint64 inputs.
func consume(left *uint64, n uint64) bool {
	if n > *left {
		return false
	}
	*left -= n
	return true
}

func boundedSeries(lower, upper Percentiles) bool {
	return lower.Count == upper.Count && lower.P50 <= upper.P50 && lower.P95 <= upper.P95 && lower.P99 <= upper.P99 && lower.P999 <= upper.P999 && lower.Max <= upper.Max
}

func validateSentinels(r Result) error {
	l := r.Lag
	if !sumEquals(uint64(l.Sampled), uint64(l.FromScheduled.Count), uint64(l.Unseen)) {
		return fmt.Errorf("lag.sampled_spans must equal seen read_visible_lag_upper_bound_from_scheduled_send.count + unseen_sampled_spans")
	}
	if !sumEquals(uint64(l.Registered), uint64(l.Sampled), uint64(l.DroppedNotAcknowledged)) || l.Registered > r.Counters.Requests || int64(l.Sampled) > r.Counters.RequestOutcomes.Acknowledged || l.DroppedNotAcknowledged > r.Counters.Requests-r.Counters.RequestOutcomes.Acknowledged {
		return fmt.Errorf("lag.registered_sentinels must equal sampled_spans + dropped_not_acknowledged_sentinels and respect request outcomes")
	}
	if !boundedSeries(l.Latency, l.FromScheduled) || !boundedSeries(l.LowerFromScheduled, l.FromScheduled) {
		return fmt.Errorf("lag seen-series counts must agree and actual-send/lower-bound quantiles must not exceed scheduled-send upper bounds")
	}
	if r.Counters.Requests > 0 && l.Method == "" {
		return fmt.Errorf("lag.method is required for attempted requests")
	}
	if l.Method != "" {
		if l.Stride < 1 || l.MaxSentinels < 1 || l.Registered > int64(l.MaxSentinels) || l.PollIntervalMS <= 0 || l.WindowSeconds <= 0 {
			return fmt.Errorf("lag sentinel_request_stride, max_sentinels, poll_interval_ms and window_seconds must describe a positive sampling plan")
		}
		if l.Registered != r.Counters.Requests/l.Stride+boolInt(r.Counters.Requests%l.Stride != 0) {
			return fmt.Errorf("lag.registered_sentinels disagrees with sentinel_request_stride and counters.requests")
		}
	}
	seen, unseen := uint64(l.FromScheduled.Count), uint64(l.Unseen)
	for i, w := range l.Windows {
		if w.StartS >= w.EndS || (i > 0 && w.StartS < l.Windows[i-1].EndS) || w.Seen != w.Upper.Count || !consume(&seen, uint64(w.Seen)) || !consume(&unseen, uint64(w.Unseen)) {
			return fmt.Errorf("lag.windows[%d] requires ordered windows and consistent seen/unseen/upper_bound_from_scheduled counts", i)
		}
	}
	if seen != 0 || unseen != 0 {
		return fmt.Errorf("lag.windows seen/unseen totals must equal the global sentinel counts")
	}
	return validateHistogram("lag.upper_bound_from_scheduled_buckets", l.Buckets, l.FromScheduled, false)
}

func boolInt(b bool) int64 {
	if b {
		return 1
	}
	return 0
}

func validateResponses(r Result) error {
	k, o := r.Counters, r.Counters.RequestOutcomes
	left := uint64(k.Requests)
	var success, retry, nonretry, ambiguous, exhausted int64
	for label, n := range r.Statuses {
		if !consume(&left, uint64(n)) {
			return fmt.Errorf("response_statuses[%s] exceeds counters.requests", label)
		}
		switch {
		case label == "transport-error":
			ambiguous += n
		case strings.HasPrefix(label, "http:") && r.Config.Encoding != EncodingGRPC:
			code, err := strconv.Atoi(strings.TrimPrefix(label, "http:"))
			if err != nil || code < 100 || code > 599 {
				return fmt.Errorf("response_statuses[%s] is not a valid HTTP status", label)
			}
			switch {
			case code == 200:
				success += n
			case code == 429 || code == 503:
				retry += n
			case code >= 400 && code < 500:
				nonretry += n
			default:
				ambiguous += n
			}
		case strings.HasPrefix(label, "grpc:") && r.Config.Encoding == EncodingGRPC:
			switch strings.TrimPrefix(label, "grpc:") {
			case "OK":
				success += n
			case "ResourceExhausted":
				exhausted += n // RetryInfo distinguishes these two outcomes.
			case "InvalidArgument", "NotFound", "AlreadyExists", "PermissionDenied", "FailedPrecondition", "Unimplemented", "Unauthenticated":
				nonretry += n
			case "Canceled", "Unknown", "DeadlineExceeded", "Aborted", "OutOfRange", "Internal", "Unavailable", "DataLoss":
				ambiguous += n
			default:
				return fmt.Errorf("response_statuses[%s] is not a valid gRPC status", label)
			}
		default:
			return fmt.Errorf("response_statuses[%s] does not match config.encoding", label)
		}
	}
	if left != 0 {
		return fmt.Errorf("response_statuses totals must equal counters.requests (including transport-error outcomes)")
	}
	// Success statuses may be ambiguous if their bodies are malformed. HTTP
	// failures before a body decode are transport errors, not successful statuses.
	if success < o.Acknowledged+o.PartialRejection || success-(o.Acknowledged+o.PartialRejection) != o.Ambiguous-ambiguous || retry > o.RejectedRetryable || nonretry > o.RejectedNonRetryable || !sumEquals(uint64(exhausted), uint64(o.RejectedRetryable-retry), uint64(o.RejectedNonRetryable-nonretry)) {
		return fmt.Errorf("response_statuses disagree with counters.request_outcomes")
	}
	if k.PartialResponses > success || k.SuccessStatus < success || k.SuccessStatus > success*int64(r.Config.BatchSize) {
		return fmt.Errorf("response_statuses success totals disagree with counters.success_status_spans or otlp_partial_responses")
	}
	if r.RequestLatency.Count != k.Requests || r.RequestLatencyFromScheduled.Count != k.Requests || !boundedSeries(r.RequestLatency, r.RequestLatencyFromScheduled) {
		return fmt.Errorf("otlp_request_latency and otlp_request_latency_from_scheduled_send must cover counters.requests with scheduled-send bounds no smaller than actual-send bounds")
	}
	if r.RetryHints.Requests != o.RejectedWithRetryHint || r.RetryHints.Delay.Count != r.RetryHints.Requests {
		return fmt.Errorf("retry_hints requests_with_retry_hint/requested_delay.count must equal the retry-hint request outcome subset")
	}
	if k.Requests == 0 && (k.Bytes != 0 || k.UncompressedBytes != 0) {
		return fmt.Errorf("counters.attempted_wire_body_bytes/attempted_uncompressed_body_bytes require requests")
	}
	// Wire bytes may be zero on a failed gRPC send, and gzip can enlarge a small
	// body. Neither bytes>0 nor compressed<=uncompressed is an all-receipt rule.
	return nil
}

func validateCoverage(r Result) error {
	v, k := r.Verification, r.Counters
	if (v.ScannedAttempted > 0 || v.Chunks > 0 || (v.Complete && k.Attempted > 0)) && (v.Chunks == 0 || v.TotalChunks == 0 || v.ChunkSpans == 0) {
		return fmt.Errorf("verification.verified_chunks/planned_chunks/chunk_spans must be nonzero for scanned work or complete nonzero attempts")
	}
	if v.ChunkSpans > 0 {
		want := k.Attempted/v.ChunkSpans + boolInt(k.Attempted%v.ChunkSpans != 0)
		if v.TotalChunks != want {
			return fmt.Errorf("verification.planned_chunks must cover counters.attempted_spans at chunk_spans per chunk")
		}
		// Chunks are a scanned prefix. Compute without multiplying adversarial
		// values; after the final chunk the short last chunk caps the span count.
		if v.Chunks == v.TotalChunks {
			if v.ScannedAttempted != uint64(k.Attempted) {
				return fmt.Errorf("verification.scanned_attempted_spans disagrees with verified_chunks coverage")
			}
		} else if v.Chunks > 0 && (v.ScannedAttempted/uint64(v.ChunkSpans) != uint64(v.Chunks) || v.ScannedAttempted%uint64(v.ChunkSpans) != 0) {
			return fmt.Errorf("verification.scanned_attempted_spans disagrees with verified_chunks * chunk_spans")
		}
	}
	if v.QueryRetries > v.Queries || v.RecheckedChunks > v.Chunks || v.DurableLagElapsedSecs > v.ElapsedSeconds {
		return fmt.Errorf("verification.query_retries/rechecked_chunks/durable_lag_elapsed_seconds exceed queries/verified_chunks/elapsed_seconds")
	}
	d := v.DurableLag
	if !sumEquals(d.Rows, uint64(d.Lag.Count), d.NegativeRows) {
		return fmt.Errorf("verification.durable_lag.stored_spans_measured must equal insert_lag count + spans_inserted_before_scheduled_send")
	}
	// A lag query may see a different database snapshot; errors, partial and
	// inconsistent observations remain diagnostics, not a claimed full measure.
	if v.Complete && d.Status == "measured" && d.Rows != v.DistinctExpected {
		return fmt.Errorf("verification.durable_lag.stored_spans_measured disagrees with persisted_distinct_attempted_spans for measured complete lag")
	}
	if err := validateHistogram("verification.durable_lag.insert_lag_buckets", d.Buckets, d.Lag, true); err != nil {
		return err
	}
	remain := v.Missing - v.Unscanned // primary decomposition already proved this safe.
	for i, m := range v.MissingRanges {
		if m.FirstRequest > m.LastRequest || m.LastRequest >= k.Requests || m.FirstScheduledS > m.LastScheduledS || m.MissingSpans == 0 || !consume(&remain, m.MissingSpans) || (i > 0 && m.FirstRequest <= v.MissingRanges[i-1].LastRequest) {
			return fmt.Errorf("verification.missing_ranges[%d] must be an ordered disjoint request range within scanned missing spans", i)
		}
	}
	if !consume(&remain, v.MissingRangesOmittedSpans) {
		return fmt.Errorf("verification.missing_ranges_omitted_spans exceeds scanned missing spans")
	}
	// Ranges are optional diagnostics in imported receipts, not the loss ledger.
	remain = v.MissingBy.AcknowledgedMissing
	for i, f := range v.FaultLoss {
		if !consume(&remain, f.MissingSpans) {
			return fmt.Errorf("verification.fault_loss_windows[%d] exceeds acknowledged missing spans", i)
		}
	}
	return nil
}

func validateWorkload(r Result) error {
	w, k := r.Workload, r.Counters
	if w.Spans == 0 {
		// An omitted disclosure is not a measured population of zero with
		// nonzero distributions attached to it.
		if w.Traces != 0 || w.Sessions != 0 || w.ErrorSpans != 0 || w.VariantCohortSpans != 0 || w.ErrorFraction != 0 || w.VariantFraction != 0 || len(w.SpansByKind) != 0 || len(w.SpansByPayloadClass) != 0 || len(w.SpansByDepth) != 0 || w.InputBytes.Count != 0 || w.OutputBytes.Count != 0 || w.InputTokens.Count != 0 || w.OutputTokens.Count != 0 || w.DurationMS.Count != 0 || w.RequestBodyBytes.Count != 0 || w.RequestWireBytes.Count != 0 || w.BuildLatency.Count != 0 || w.DispatchDelay.Count != 0 {
			return fmt.Errorf("workload.spans=0 requires empty realised populations")
		}
		return nil
	} // absent optional realised workload disclosure
	if w.Spans != k.Attempted || w.Traces > w.Spans || w.Sessions > w.Traces || w.ErrorSpans > w.Spans || w.VariantCohortSpans > w.Spans {
		return fmt.Errorf("workload spans/traces/sessions/error_spans/variant_cohort_spans disagree with attempted spans")
	}
	for _, part := range []struct {
		name string
		m    map[string]int64
	}{{"spans_by_kind", w.SpansByKind}, {"spans_by_payload_class", w.SpansByPayloadClass}} {
		left := uint64(w.Spans)
		for _, n := range part.m {
			if !consume(&left, uint64(n)) {
				return fmt.Errorf("workload.%s exceeds spans", part.name)
			}
		}
		if left != 0 {
			return fmt.Errorf("workload.%s must partition spans", part.name)
		}
	}
	left := uint64(w.Spans)
	for _, n := range w.SpansByDepth {
		if !consume(&left, uint64(n)) {
			return fmt.Errorf("workload.spans_by_depth exceeds spans")
		}
	}
	if left != 0 {
		return fmt.Errorf("workload.spans_by_depth must partition spans")
	}
	if math.Abs(w.ErrorFraction-float64(w.ErrorSpans)/float64(w.Spans)) > 1e-12 || math.Abs(w.VariantFraction-float64(w.VariantCohortSpans)/float64(w.Spans)) > 1e-12 {
		return fmt.Errorf("workload.error_fraction/variant_cohort_fraction must equal count / spans")
	}
	for _, p := range []struct {
		name string
		d    Distribution
		n    int64
	}{{"input_text_bytes", w.InputBytes, w.Spans}, {"output_text_bytes", w.OutputBytes, w.Spans}, {"span_duration_ms", w.DurationMS, w.Spans}, {"llm_input_tokens", w.InputTokens, w.SpansByKind["LLM"]}, {"llm_output_tokens", w.OutputTokens, w.SpansByKind["LLM"]}, {"request_uncompressed_body_bytes", w.RequestBodyBytes, k.Requests}, {"request_wire_body_bytes", w.RequestWireBytes, k.Requests}} {
		if p.d.Count != p.n {
			return fmt.Errorf("workload.%s.count disagrees with its population", p.name)
		}
	}
	for _, p := range []struct {
		name string
		d    Distribution
		n    int64
	}{{"request_uncompressed_body_bytes", w.RequestBodyBytes, k.UncompressedBytes}, {"request_wire_body_bytes", w.RequestWireBytes, k.Bytes}} {
		if p.d.Count > 0 && math.Abs(p.d.Mean-float64(p.n)/float64(p.d.Count)) > math.Max(1, p.d.Mean)*1e-12 {
			return fmt.Errorf("workload.%s.mean disagrees with attempted bytes / requests", p.name)
		}
	}
	if w.BuildLatency.Count != k.Requests || w.DispatchDelay.Count != k.Requests {
		return fmt.Errorf("workload generator_request_build_latency/dispatch_delay_behind_schedule must cover requests")
	}
	return nil
}

func validateProbes(r Result) error {
	for i, p := range r.Reads {
		// Request construction can fail before a latency exists: failed latency is
		// a subset of errors. Censored reads can have received HTTP headers already.
		if p.Latency.Count != p.Successes || p.FailureLatency.Count > p.Errors || p.CensoredElapsed.Count != p.Censored {
			return fmt.Errorf("application_reads[%d] successful/failed/censored latency counts disagree with outcomes", i)
		}
		remaining := []uint64{uint64(p.Successes), uint64(p.Errors), uint64(p.Censored)}
		for status, n := range p.HTTPStatuses {
			if status < 100 || status > 599 {
				return fmt.Errorf("application_reads[%d].http_statuses contains an invalid status", i)
			}
			rest := uint64(n)
			for j := range remaining {
				used := min(rest, remaining[j])
				rest -= used
				remaining[j] -= used
			}
			if rest != 0 {
				return fmt.Errorf("application_reads[%d].http_statuses exceeds request outcomes", i)
			}
		}
		if p.Successes == 0 && p.Errors == 0 && p.Censored == 0 && p.ResponseBytes != 0 {
			return fmt.Errorf("application_reads[%d].response_bytes requires requests", i)
		}
	}
	for i, p := range r.DirectReads {
		if p.Latency.Count != p.Successes || p.FailureLatency.Count != p.Errors || p.CensoredElapsed.Count != p.Censored || p.NonEmpty > p.Successes || p.Rows.Count != p.Successes || (p.PayloadBytes != nil && p.PayloadBytes.Count != p.Successes) {
			return fmt.Errorf("direct_clickhouse_reads[%d] latency/rows/payload/non_empty_results counts disagree with probe outcomes", i)
		}
	}
	return nil
}

// The numeric configuration contract is offline: deliberately do not call
// Config.Validate, which also validates execution approvals and target URLs.
func validateResultConfig(r Result) error {
	c := r.Config
	for _, bound := range []struct {
		name      string
		n, lo, hi int64
	}{
		{"max_duration_ns", int64(c.MaxDuration), int64(c.Duration), int64(24 * time.Hour)},
		{"max_otlp_bytes", c.MaxBytes, 1, math.MaxInt64},
		{"spans_per_trace", c.SpansPerTrace, 1, 10000}, {"traces_per_session", c.TracesPerSession, 1, 10000},
		{"request_timeout_ns", int64(c.Timeout), 1, int64(time.Minute)}, {"settle_ns", int64(c.Settle), 0, int64(10 * time.Minute)},
		{"poll_interval_ns", int64(c.PollInterval), int64(100 * time.Millisecond), math.MaxInt64},
		{"verification_chunk_spans", c.VerifyChunk, 1, 1000000},
		{"tree_depth", int64(c.TreeDepth), 1, 64}, {"fan_out", int64(c.FanOut), 1, 64}, {"text_pool_blocks", int64(c.TextPoolBlocks), 1, 65536},
		{"verify_timeout_ns", int64(c.VerifyTimeout), 0, int64(72 * time.Hour)}, {"durable_lag_timeout_ns", int64(c.DurableLagTimeout), 0, int64(72 * time.Hour)},
		{"verify_query_timeout_ns", int64(c.VerifyQueryTimeout), 0, int64(time.Hour)}, {"verify_query_retries", int64(c.VerifyQueryRetries), 0, 10},
		{"max_sentinels", int64(c.MaxSentinels), 1, 1 << 20},
	} {
		if bound.n < bound.lo || bound.n > bound.hi {
			return fmt.Errorf("config.%s must be within %d..%d", bound.name, bound.lo, bound.hi)
		}
	}
	if c.ErrorRate > 1 || c.CohortVariantFraction > 1 || c.BytesPerToken < 1 || c.BytesPerToken > 16 {
		return fmt.Errorf("config.error_rate/cohort_variant_fraction must be 0..1 and bytes_per_token 1..16")
	}
	if c.VerifyMaxMemory != 0 && (c.VerifyMaxMemory < 64<<20 || c.VerifyMaxMemory > 256<<30) {
		return fmt.Errorf("config.verify_query_max_memory_bytes must be 0 or 64MiB..256GiB")
	}
	if c.CollectorHealthURL != "" && (c.CollectorPollInterval < 100*time.Millisecond || c.CollectorPollInterval > time.Minute) {
		return fmt.Errorf("config.collector_poll_interval_ns must be 100ms..1m when enabled")
	}
	if err := c.DirectProbes.validate(); err != nil {
		return fmt.Errorf("config.direct_probes: %w", err)
	}
	for i, f := range c.DeclaredFaults {
		if f.At < 0 || f.Duration <= 0 || f.At > c.Duration || f.Duration > c.Duration-f.At {
			return fmt.Errorf("config.declared_faults[%d] must fit within duration_ns", i)
		}
	}
	for i, p := range c.ReadProbes {
		if p.ExpectStatus != 200 || p.IntervalMS < 1000 {
			return fmt.Errorf("config.read_probes[%d].expect_status/interval_ms must be 200 / >=1000", i)
		}
	}
	return nil
}

func validateResultMeasurements(r Result) error {
	for _, check := range []func(Result) error{validateResultConfig, validateSentinels, validateResponses, validateCoverage, validateWorkload, validateProbes} {
		if err := check(r); err != nil {
			return err
		}
	}
	s, k := r.Schedule, r.Counters
	if s.StartDelay.Count != k.Requests || (r.Workload.Spans > 0 && s.StartDelay.Count != r.Workload.DispatchDelay.Count) {
		return fmt.Errorf("schedule.start_delay_after_scheduled_send.count must equal counters.requests and the disclosed workload.dispatch_delay_behind_schedule.count")
	}
	if s.LateStartRequestsOver1s > s.LateStartRequests || s.LateStartRequests > k.Requests || s.StartDelay.Count > k.Requests {
		return fmt.Errorf("schedule late_start_requests_over_1s/late_start_requests/start_delay count exceed their request populations")
	}
	b := r.Backlog
	if b.FailedSamples > b.Samples || b.KeptSamples > b.Samples || b.SlopeSamples > b.KeptSamples || b.SecondHalfSamples > b.SlopeSamples || b.AtBudgetSamples > b.SlopeSamples || b.BufferedRows.Count != b.SlopeSamples || b.MaxBufferedRows != b.BufferedRows.Max || b.CounterResets > b.KeptSamples {
		return fmt.Errorf("collector_backlog sample subsets, buffered_rows count/max and slope samples disagree")
	}
	for _, windows := range [][]Window{b.Unreachable, b.InsertStalls} {
		for i, w := range windows {
			if w.StartS > w.EndS || (i > 0 && w.StartS < windows[i-1].EndS) || w.Samples > b.KeptSamples {
				return fmt.Errorf("collector_backlog windows must be ordered and bounded by kept samples")
			}
		}
	}
	for _, sample := range []struct {
		name string
		s    ClockSample
	}{{"before_load", r.Clock.Start}, {"after_settle", r.Clock.End}} {
		if sample.s.Measured && (sample.s.Probes < 1 || sample.s.Probes > 5 || math.Abs(sample.s.UncertaintyMS-sample.s.RTTMS/2) > 1e-9) {
			return fmt.Errorf("clock.%s measured probes must be 1..5 and uncertainty_ms = best_round_trip_ms / 2", sample.name)
		}
	}
	if rec := r.Recovery; rec != nil {
		// An empty recovery block is an older diagnostic, never headline evidence.
		if rec.Method != "" && (rec.AcknowledgedMissing != r.Verification.MissingBy.AcknowledgedMissing || rec.VisibleDuplicates != r.Verification.DuplicateRows || rec.AmbiguousPersisted != r.Verification.PersistedBy.AmbiguousPersisted) {
			return fmt.Errorf("recovery integrity counters disagree with verification")
		}
		for i, f := range rec.Faults {
			if f.StartS > f.EndS || f.EarlierSentinelsUnseen > f.EarlierSentinels || f.EarlierSentinels > r.Lag.Sampled {
				return fmt.Errorf("recovery.faults[%d] window/sentinel subsets are inconsistent", i)
			}
			if f.Recovered {
				if f.RecoverySeconds == nil || f.FirstAckAfterEndS == nil || f.BacklogBaselineRows == nil || f.BacklogDrainedAfterEnd == nil || f.EarlierVisibleAfterEndS == nil || f.EarlierSentinels == 0 || f.EarlierSentinelsUnseen != 0 {
					return fmt.Errorf("recovery.faults[%d].recovered requires all observed components", i)
				}
				if *f.RecoverySeconds != max(*f.FirstAckAfterEndS, *f.BacklogDrainedAfterEnd, *f.EarlierVisibleAfterEndS) {
					return fmt.Errorf("recovery.faults[%d].recovery_seconds must be the maximum component", i)
				}
			}
		}
	}
	return nil
}
