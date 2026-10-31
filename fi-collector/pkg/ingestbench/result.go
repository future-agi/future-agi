package ingestbench

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/bits"
	"sort"
	"strings"
	"time"
)

// RequestOutcomes counts REQUESTS by outcome class; every attempted request is
// in exactly one class.
type RequestOutcomes struct {
	Acknowledged          int64 `json:"acknowledged"`
	PartialRejection      int64 `json:"partial_rejection"`
	RejectedRetryable     int64 `json:"whole_request_rejected_retryable"`
	RejectedWithRetryHint int64 `json:"whole_request_rejected_with_retry_hint"`
	RejectedNonRetryable  int64 `json:"whole_request_rejected_non_retryable"`
	Ambiguous             int64 `json:"ambiguous"`
}

func (o RequestOutcomes) total() int64 {
	return o.Acknowledged + o.PartialRejection + o.RejectedRetryable + o.RejectedNonRetryable + o.Ambiguous
}

// Counters count SPANS unless the name says requests. The identity
//
//	attempted = acknowledged + otlp_partial_rejected + whole_request_rejected + ambiguous
//
// holds for every finished run (CheckIdentity), and
// unacknowledged = whole_request_rejected + ambiguous.
type Counters struct {
	Generated            int64           `json:"generated_spans"`
	Attempted            int64           `json:"attempted_spans"`
	SuccessStatus        int64           `json:"success_status_spans"`
	Accepted             int64           `json:"accepted_spans"`
	Acknowledged         int64           `json:"acknowledged_spans"`
	PartialRejected      int64           `json:"otlp_partial_rejected_spans"`
	PartialResponses     int64           `json:"otlp_partial_responses"`
	WholeRequestRejected int64           `json:"whole_request_rejected_spans"`
	RejectedRetryable    int64           `json:"whole_request_rejected_retryable_spans"`
	RejectedNonRetryable int64           `json:"whole_request_rejected_non_retryable_spans"`
	Ambiguous            int64           `json:"ambiguous_spans"`
	Unacknowledged       int64           `json:"unacknowledged_spans"`
	Requests             int64           `json:"requests"`
	RequestOutcomes      RequestOutcomes `json:"request_outcomes"`
	RequestErrors        int64           `json:"request_errors"`
	Bytes                int64           `json:"attempted_wire_body_bytes"`
	UncompressedBytes    int64           `json:"attempted_uncompressed_body_bytes"`
}

// CheckIdentity verifies that every attempted span and request is in exactly
// one outcome class.
func (c Counters) CheckIdentity() error {
	if err := measurementNumbers(c, "counters"); err != nil {
		return err
	}
	if !sumEquals(uint64(c.Attempted), uint64(c.Acknowledged), uint64(c.PartialRejected), uint64(c.WholeRequestRejected), uint64(c.Ambiguous)) {
		return fmt.Errorf("counters.attempted_spans: span accounting identity broken: attempted %d != acknowledged %d + partial rejected %d + whole-request rejected %d + ambiguous %d",
			c.Attempted, c.Acknowledged, c.PartialRejected, c.WholeRequestRejected, c.Ambiguous)
	}
	if !sumEquals(uint64(c.WholeRequestRejected), uint64(c.RejectedRetryable), uint64(c.RejectedNonRetryable)) || !sumEquals(uint64(c.Unacknowledged), uint64(c.WholeRequestRejected), uint64(c.Ambiguous)) {
		return fmt.Errorf("counters.whole_request_rejected_spans/unacknowledged_spans: rejection sub-totals do not add up")
	}
	o := c.RequestOutcomes
	if !sumEquals(uint64(c.Requests), uint64(o.Acknowledged), uint64(o.PartialRejection), uint64(o.RejectedRetryable), uint64(o.RejectedNonRetryable), uint64(o.Ambiguous)) || o.RejectedWithRetryHint > o.RejectedRetryable {
		return fmt.Errorf("counters.requests: request accounting identity broken or retry hints exceed retryable rejections")
	}
	if c.Accepted != c.Acknowledged {
		return fmt.Errorf("counters.accepted_spans and acknowledged_spans differ")
	}
	return nil
}

// MissingByAcknowledgement decomposes missing attempted spans by what the
// collector said about them. Only AcknowledgedMissing is data loss; the rest
// is expected (rejected) or unknown (ambiguous). The four add up to missing.
type MissingByAcknowledgement struct {
	// AcknowledgedMissing: spans the collector acknowledged that are not stored.
	// Includes PartialBeyondRejected.
	AcknowledgedMissing uint64 `json:"acknowledged_but_missing_spans"`
	// PartialBeyondRejected: in partial-success requests, missing spans beyond
	// the rejected count (a lower bound on loss in those requests, because the
	// rejected identities are not reported).
	PartialBeyondRejected uint64 `json:"partial_requests_missing_beyond_rejected_spans"`
	// PartialRejectedMissing: missing spans covered by a partial rejection.
	PartialRejectedMissing uint64 `json:"partial_rejected_and_missing_spans"`
	// RejectedMissing: missing spans of whole-request rejections (expected).
	RejectedMissing uint64 `json:"whole_request_rejected_and_missing_spans"`
	// AmbiguousMissing: missing spans of requests with an unknown outcome.
	AmbiguousMissing uint64 `json:"ambiguous_and_missing_spans"`
}

// PersistedByAcknowledgement counts stored spans whose request was NOT
// acknowledged. Rejected-but-persisted spans contradict the collector's
// rejection contract and fail the run.
type PersistedByAcknowledgement struct {
	RejectedButPersisted uint64 `json:"whole_request_rejected_but_persisted_spans"`
	PartialExcess        uint64 `json:"partial_requests_persisted_beyond_acknowledged_spans"`
	AmbiguousPersisted   uint64 `json:"ambiguous_but_persisted_spans"`
}

type Verification struct {
	Complete         bool    `json:"complete"`
	Method           string  `json:"method"`
	Rows             uint64  `json:"scanned_run_rows"`
	DistinctExpected uint64  `json:"persisted_distinct_attempted_spans"`
	DuplicateRows    uint64  `json:"duplicate_expected_rows_visible"`
	UnexpectedRows   uint64  `json:"unexpected_rows"`
	OutsideNamespace uint64  `json:"run_rows_outside_trace_namespace"`
	Missing          uint64  `json:"missing_attempted_spans"`
	MissingFraction  float64 `json:"missing_attempted_fraction"`
	// ScannedAttempted is the attempted spans the finished chunks cover;
	// Unscanned (attempted - scanned) counts as missing until verified, and
	// MissingBy decomposes only the scanned part.
	ScannedAttempted  uint64                     `json:"scanned_attempted_spans"`
	Unscanned         uint64                     `json:"unscanned_attempted_spans"`
	MissingBy         MissingByAcknowledgement   `json:"missing_by_acknowledgement"`
	PersistedBy       PersistedByAcknowledgement `json:"persisted_without_acknowledgement"`
	DataLossDetected  bool                       `json:"data_loss_detected"`
	ContractViolation bool                       `json:"rejection_contract_violation"`
	Chunks            int64                      `json:"verified_chunks"`
	TotalChunks       int64                      `json:"planned_chunks"`
	ChunkSpans        int64                      `json:"chunk_spans"`
	// Chunks and NamespaceWindows are the totals verified so far, including
	// any done before a resume; ResumedAtChunk is where this process started.
	NamespaceWindows int64   `json:"verified_namespace_windows"`
	RecheckedChunks  int64   `json:"rechecked_chunks"`
	ResumedAtChunk   int64   `json:"resumed_at_chunk"`
	Queries          int64   `json:"queries"`
	QueryRetries     int64   `json:"query_retries"`
	TimeoutSeconds   float64 `json:"timeout_seconds"`
	// DurableLagTimeoutSecs is the durable lag's own budget (0 when disabled);
	// DurableLagElapsedSecs the time its queries took, which did not count
	// against TimeoutSeconds.
	DurableLagTimeoutSecs float64 `json:"durable_lag_timeout_seconds"`
	DurableLagElapsedSecs float64 `json:"durable_lag_elapsed_seconds"`
	QueryTimeoutSecs      float64 `json:"query_timeout_seconds"`
	QueryMaxMemory        int64   `json:"query_max_memory_bytes"`
	Error                 string  `json:"error,omitempty"`
	ElapsedSeconds        float64 `json:"elapsed_seconds"`
	PreexistingRunRows    uint64  `json:"preexisting_run_rows"`
	// MissingRanges lists maximal runs of consecutive requests of one outcome
	// class that each miss at least one span, in request order (at most
	// maxMissingRanges; the rest are counted as omitted).
	MissingRanges             []MissingRange `json:"missing_ranges"`
	MissingRangesOmitted      int64          `json:"missing_ranges_omitted"`
	MissingRangesOmittedSpans uint64         `json:"missing_ranges_omitted_spans"`
	// FaultLoss counts fully acknowledged missing spans by the fault reporting
	// windows, independently of MissingRanges. Absent in older checkpoints;
	// counts are conclusive only when Complete is true.
	FaultLoss []FaultLossWindow `json:"fault_loss_windows,omitempty"`
	// DurableLag is the server-side insert lag of every stored span.
	DurableLag DurableLagResult `json:"durable_lag"`
}

// MissingRange is a run of requests [FirstRequest, LastRequest] of one outcome
// class, each missing at least one span; MissingSpans is their total. Scheduled
// offsets are seconds from load start (request k is due at k*batch/rate).
type MissingRange struct {
	Class           string  `json:"class"`
	FirstRequest    int64   `json:"first_request"`
	LastRequest     int64   `json:"last_request"`
	FirstScheduledS float64 `json:"first_scheduled_offset_seconds"`
	LastScheduledS  float64 `json:"last_scheduled_offset_seconds"`
	MissingSpans    uint64  `json:"missing_spans"`
}

// DurableLagResult is, for every distinct stored span of the run, ClickHouse's
// own insert timestamp (created_at, DEFAULT now64(6) evaluated when the server
// processes the INSERT, not set by the collector) minus the scheduled send of
// the span's request, corrected by the measured server-minus-generator clock
// offset. created_at is taken before the part is committed, so this is a lower
// bound on scheduled-send-to-commit latency; duplicates count once, with their
// first insert.
type DurableLagResult struct {
	Status string `json:"status"`
	Method string `json:"method"`
	// Rows is the stored spans measured: Lag.Count plus NegativeRows. With a
	// complete verification it equals the distinct stored attempted spans.
	Rows         uint64            `json:"stored_spans_measured"`
	NegativeRows uint64            `json:"spans_inserted_before_scheduled_send"`
	Lag          Percentiles       `json:"insert_lag_from_scheduled_send"`
	Buckets      []HistogramBucket `json:"insert_lag_buckets"`
	// The clock correction applied (server minus generator) and its
	// uncertainty; lags below the uncertainty are not resolved.
	ClockOffsetMS      float64 `json:"clock_offset_applied_ms"`
	ClockUncertaintyMS float64 `json:"clock_offset_uncertainty_ms"`
	ClockMeasured      bool    `json:"clock_offset_measured"`
}

// ScheduleResult relates the open-loop schedule to what was started.
type ScheduleResult struct {
	Method string `json:"method"`
	// ScheduledSpans: spans whose scheduled send passed while the producer
	// ran; NotStartedSpans of them were never sent (k6 calls these dropped
	// iterations).
	ScheduledSpans          int64       `json:"scheduled_spans"`
	NotStartedSpans         int64       `json:"scheduled_but_not_started_spans"`
	LateStartThresholdMS    float64     `json:"late_start_threshold_ms"`
	LateStartRequests       int64       `json:"late_start_requests"`
	LateStartRequestsOver1s int64       `json:"late_start_requests_over_1s"`
	StartDelay              Percentiles `json:"start_delay_after_scheduled_send"`
}

type Percentiles struct {
	Count int64   `json:"count"`
	P50   float64 `json:"p50_ms"`
	P95   float64 `json:"p95_ms"`
	P99   float64 `json:"p99_ms"`
	P999  float64 `json:"p999_ms"`
	Max   float64 `json:"max_ms"`
}

// Latency histograms are log-linear over whole microseconds (the HdrHistogram
// layout): exact below 256 us, then 128 sub-buckets per power of two, so from
// 256 us up every bucket is at most 1/128 (0.79%) as wide as its lower edge.
// They span 0 to 2^37 us (38 h); larger values share the last bucket, whose
// quantiles are then capped at the exact maximum.
const (
	subBucketBits    = 7
	subBuckets       = 1 << subBucketBits
	histogramMaxExp  = 37
	histogramBuckets = 2*subBuckets + (histogramMaxExp-subBucketBits-1)*subBuckets
	// histogramLastBucket is histogramBuckets-1 as SQL text.
	histogramLastBucket = "3967"
)

// HistogramMethod describes the latency histograms of a result.
var HistogramMethod = LatencyHistogram{
	Method:             "log-linear over whole microseconds: exact below 0.256 ms, then 128 sub-buckets per power of two through 2^37 us (38 h)",
	MaxRelativeWidth:   1.0 / subBuckets,
	ExactBelowMS:       2 * subBuckets / 1000.0,
	UpperSeriesQuantum: "upper edge of the quantile's bucket, capped at the exact maximum: never below the true value",
	LowerSeriesQuantum: "lower edge of the quantile's bucket: never above the true value",
	LowerBoundSeries: []string{"lag.read_visible_lag_lower_bound_from_scheduled_send", "verification.durable_lag.insert_lag_from_scheduled_send",
		"direct_clickhouse_reads[].censored_query_elapsed_lower_bound", "application_reads[].censored_response_elapsed_lower_bound"},
}

// LatencyHistogram states how quantiles are quantised: MaxRelativeWidth is the
// widest bucket relative to its lower edge from ExactBelowMS up.
type LatencyHistogram struct {
	Method             string   `json:"method"`
	MaxRelativeWidth   float64  `json:"max_relative_bucket_width"`
	ExactBelowMS       float64  `json:"exact_microsecond_buckets_below_ms"`
	UpperSeriesQuantum string   `json:"quantile_of_measured_and_upper_bound_series"`
	LowerSeriesQuantum string   `json:"quantile_of_lower_bound_series"`
	LowerBoundSeries   []string `json:"lower_bound_series"`
}

// latencyBucketMicros is the bucket of a duration in whole microseconds
// (negative counts as 0).
func latencyBucketMicros(us int64) int {
	if us < 2*subBuckets {
		return int(max(us, 0))
	}
	e := bits.Len64(uint64(us)) - 1
	b := 2*subBuckets + (e-subBucketBits-1)*subBuckets + int(us>>(e-subBucketBits)) - subBuckets
	return min(b, histogramBuckets-1)
}

// latencyBucket is the bucket of a duration given in milliseconds.
func latencyBucket(ms float64) int {
	return latencyBucketMicros(int64(math.Floor(ms * 1000)))
}

// bucketBoundsMicros is the half-open range [lo, hi) of whole microseconds a
// bucket holds.
func bucketBoundsMicros(i int) (lo, hi int64) {
	if i < 2*subBuckets {
		return int64(i), int64(i) + 1
	}
	k := i - 2*subBuckets
	shift := k / subBuckets
	m := int64(k%subBuckets + subBuckets)
	return m << (shift + 1), (m + 1) << (shift + 1)
}

// latencyEdge is the upper edge, in milliseconds, of a latency bucket.
func latencyEdge(i int) float64 {
	_, hi := bucketBoundsMicros(i)
	return float64(hi) / 1000
}

// latencyLowerEdge is the lower edge, in milliseconds, of a latency bucket.
func latencyLowerEdge(i int) float64 {
	lo, _ := bucketBoundsMicros(i)
	return float64(lo) / 1000
}

func durationMicros(d time.Duration) int64 {
	return max(0, int64(d/time.Microsecond))
}

// histogram has constant memory (about 31 KiB) even for day-long runs.
// Quantiles of a measured or upper-bound series are bucket upper edges capped at
// the maximum; a lower-bound series reads bucket lower edges (lowerBound).
type histogram struct {
	counts     [histogramBuckets]int64
	n          int64
	max        float64
	lowerBound bool
}

func (h *histogram) add(d time.Duration) {
	us := durationMicros(d)
	h.counts[latencyBucketMicros(us)]++
	h.n++
	h.max = math.Max(h.max, max(0, float64(d)/float64(time.Millisecond)))
}

func (h *histogram) summary() Percentiles {
	return percentiles(h.n, h.max, h.lowerBound, func(yield func(int, int64) bool) {
		for i, n := range h.counts {
			if n != 0 && !yield(i, n) {
				return
			}
		}
	})
}

// buckets lists the non-empty buckets, so a published result can carry its raw
// distribution, not only the percentiles.
func (h *histogram) buckets() []HistogramBucket {
	var out []HistogramBucket
	for i, n := range h.counts {
		if n != 0 {
			out = append(out, HistogramBucket{LowerMS: latencyLowerEdge(i), UpperMS: latencyEdge(i), Count: n})
		}
	}
	return out
}

// HistogramBucket is one non-empty bucket: Count values at least LowerMS and
// below UpperMS.
type HistogramBucket struct {
	LowerMS float64 `json:"lower_ms"`
	UpperMS float64 `json:"upper_ms"`
	Count   int64   `json:"count"`
}

// percentiles reads quantiles from buckets visited in increasing order. For a
// measured or upper-bound series a quantile is the upper edge of its bucket,
// capped at the exact maximum: never understated, never above the max. For a
// lower-bound series (lowerBound) it is the lower edge: never overstated.
func percentiles(n int64, maxMS float64, lowerBound bool, visit func(func(int, int64) bool)) Percentiles {
	p := Percentiles{Count: n, Max: maxMS}
	if n == 0 {
		return p
	}
	targets := []float64{.5, .95, .99, .999}
	out := []*float64{&p.P50, &p.P95, &p.P99, &p.P999}
	next := 0
	var sum int64
	visit(func(i int, c int64) bool {
		sum += c
		for next < len(targets) && sum >= int64(math.Ceil(targets[next]*float64(n))) {
			if lowerBound {
				*out[next] = math.Min(latencyLowerEdge(i), maxMS)
			} else {
				*out[next] = math.Min(latencyEdge(i), maxMS)
			}
			next++
		}
		return next < len(targets)
	})
	return p
}

// sparseHistogram is the latency histogram in a form that serialises compactly
// (a verification checkpoint carries it): bucket index -> count.
type sparseHistogram struct {
	Counts map[int]int64 `json:"counts"`
	N      int64         `json:"n"`
	MaxMS  float64       `json:"max_ms"`
}

func (h *sparseHistogram) addN(d time.Duration, n int64) {
	if n <= 0 {
		return
	}
	if h.Counts == nil {
		h.Counts = map[int]int64{}
	}
	us := durationMicros(d)
	h.Counts[latencyBucketMicros(us)] += n
	h.N += n
	h.MaxMS = math.Max(h.MaxMS, max(0, float64(d)/float64(time.Millisecond)))
}

// addBucket adds n values already bucketed (by the server) to bucket b; maxMS
// is the largest of them.
func (h *sparseHistogram) addBucket(b int, n int64, maxMS float64) {
	if n <= 0 {
		return
	}
	if h.Counts == nil {
		h.Counts = map[int]int64{}
	}
	h.Counts[min(max(b, 0), histogramBuckets-1)] += n
	h.N += n
	h.MaxMS = math.Max(h.MaxMS, maxMS)
}

func (h *sparseHistogram) merge(o sparseHistogram) {
	for b, n := range o.Counts {
		h.addBucket(b, n, 0)
	}
	h.MaxMS = math.Max(h.MaxMS, o.MaxMS)
}

func (h *sparseHistogram) sortedKeys() []int {
	keys := make([]int, 0, len(h.Counts))
	for k := range h.Counts {
		keys = append(keys, k)
	}
	sort.Ints(keys)
	return keys
}

func (h *sparseHistogram) buckets() []HistogramBucket {
	keys := h.sortedKeys()
	out := make([]HistogramBucket, 0, len(keys))
	for _, k := range keys {
		out = append(out, HistogramBucket{LowerMS: latencyLowerEdge(k), UpperMS: latencyEdge(k), Count: h.Counts[k]})
	}
	return out
}

// summary reads the quantiles as upper edges, or as lower edges for a
// lower-bound series.
func (h *sparseHistogram) summary(lowerBound bool) Percentiles {
	keys := h.sortedKeys()
	return percentiles(h.N, h.MaxMS, lowerBound, func(yield func(int, int64) bool) {
		for _, k := range keys {
			if !yield(k, h.Counts[k]) {
				return
			}
		}
	})
}

type ProbeResult struct {
	Name      string `json:"name"`
	Kind      string `json:"kind"`
	Status    string `json:"status"`
	Successes int64  `json:"successes"`
	Censored  int64  `json:"window_censored_requests"`
	// CensoredElapsed: how long each censored request had run when its window
	// closed, a right-censored lower bound on its latency (see DirectReadResult).
	CensoredElapsed Percentiles   `json:"censored_response_elapsed_lower_bound"`
	Errors          int64         `json:"errors"`
	HTTPStatuses    map[int]int64 `json:"http_statuses"`
	Latency         Percentiles   `json:"successful_response_latency"`
	FailureLatency  Percentiles   `json:"failed_response_latency"`
	ResponseBytes   int64         `json:"response_bytes"`
}

// RetryHints summarises server-requested delays on retryable rejections.
type RetryHints struct {
	Requests int64       `json:"requests_with_retry_hint"`
	Delay    Percentiles `json:"requested_delay"`
}

type Result struct {
	SchemaVersion   string           `json:"schema_version"`
	Status          string           `json:"status"`
	Config          Config           `json:"config"`
	Provenance      Provenance       `json:"provenance" receipt:"optional"`
	Started         time.Time        `json:"started_at"`
	LoadSeconds     float64          `json:"load_elapsed_seconds"`
	StopReason      string           `json:"stop_reason"`
	PlannedSpans    int64            `json:"planned_spans"`
	Counters        Counters         `json:"counters"`
	AccountingError string           `json:"accounting_error,omitempty"`
	Statuses        map[string]int64 `json:"response_statuses"`
	RetryHints      RetryHints       `json:"retry_hints"`
	RequestLatency  Percentiles      `json:"otlp_request_latency"`
	// RequestLatencyFromScheduled is the same requests timed from their
	// scheduled send (coordinated-omission corrected).
	RequestLatencyFromScheduled Percentiles        `json:"otlp_request_latency_from_scheduled_send"`
	Schedule                    ScheduleResult     `json:"schedule"`
	Workload                    WorkloadStats      `json:"workload" receipt:"optional"`
	Verification                Verification       `json:"verification"`
	Lag                         LagResult          `json:"lag"`
	Backlog                     BacklogResult      `json:"collector_backlog"`
	Clock                       ClockResult        `json:"clock"`
	Recovery                    *RecoveryResult    `json:"recovery,omitempty"`
	LatencyHistogram            LatencyHistogram   `json:"latency_histogram"`
	Reads                       []ProbeResult      `json:"application_reads"`
	DirectReads                 []DirectReadResult `json:"direct_clickhouse_reads"`
	Resources                   []ResourceSnapshot `json:"resources"`
	Warnings                    []string           `json:"warnings"`
	Limitations                 []string           `json:"limitations"`
}

func newResult(c Config) Result {
	r := Result{SchemaVersion: SchemaVersion, Status: "dry-run", Config: c, LatencyHistogram: HistogramMethod,
		PlannedSpans: c.PlannedSpans(), Statuses: map[string]int64{}, Warnings: []string{},
		Limitations: []string{
			"Synthetic payload profiles are a seeded model, not measured customer payload distributions.",
			"Accepted/acknowledged means the collector's OTLP response claims acceptance; neither means durable storage.",
			"No client retries. Ambiguous exports may still be persisted; partial success does not identify rejected IDs, so loss inside partial-success requests is a lower bound.",
			"Read-visible lag is measured on sentinel spans of the load (the first span of every k-th acknowledged request) and bracketed by the polls around it; its upper bound includes polling/query delay. It is not storage commit latency.",
			"Durable lag uses ClickHouse's created_at, evaluated when the server processes the INSERT and before the part is committed, corrected by a measured clock offset: a lower bound on commit latency.",
			"Duplicate counts cover rows visible during verification, not duplicates already removed by MergeTree merges.",
			"Verification is chunked by trace range and time window, not a transactional cross-shard snapshot; quiesce the run and record replica routing/consistency.",
			"Direct ClickHouse reads measure database latency for fixed probe queries, not application API or browser latency.",
			"A target profile is not evidence of reaching that rate; full-scale sustained and burst acceptance remain pending until measured.",
		}}
	for _, name := range []string{"trace-list", "attribute-filter", "session-view", "dashboard"} {
		r.Reads = append(r.Reads, ProbeResult{Name: name, Kind: "authenticated_application_http", Status: "not-configured", HTTPStatuses: map[int]int64{}})
	}
	return r
}

func WriteResult(w io.Writer, r Result) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(r)
}

// Report is a plain-text, unit-explicit report, not a performance certification.
func Report(w io.Writer, r Result) error {
	if err := ValidateResult(r); err != nil {
		return err
	}
	c := r.Counters
	fmt.Fprintf(w, "OTLP ingestion measurement (%s)\nRun: %s  Environment: %s  Topology: %s\n", r.Status, r.Config.RunID, r.Config.Environment, r.Config.Topology)
	fmt.Fprintf(w, "Wire path: %s%s\n", r.Config.Encoding, map[bool]string{true: " + gzip", false: ""}[r.Config.Gzip])
	sut := r.Provenance.SUT
	fmt.Fprintf(w, "System under test revision: %s (source: %s)\n", orUnknown(sut.Revision), orUnknown(sut.RevisionSource))
	chInfo := r.Provenance.ClickHouse
	fmt.Fprintf(w, "ClickHouse: version %s; server memory %s; cgroup CPU limit %s; max_threads %s\n", orUnknown(chInfo.Version),
		reportedBytes(chInfo.Hardware.OSMemoryBytes), reportedNumber(chInfo.Hardware.CGroupMaxCPU), orUnknown(chInfo.Hardware.MaxThreads))
	if rt := r.Provenance.Collector.Runtime; rt != nil {
		fmt.Fprintf(w, "Collector runtime: num_cpu %v, gomaxprocs %v\n", rt["num_cpu"], rt["gomaxprocs"])
	}
	fmt.Fprintf(w, "Target: %d spans/s for %.3fs = %d spans (not traces)\n", r.Config.Rate, r.Config.Duration.Seconds(), r.PlannedSpans)
	fmt.Fprintf(w, "Generated: %d  Attempted: %d  Acknowledged: %d\n", c.Generated, c.Attempted, c.Acknowledged)
	fmt.Fprintf(w, "Partial rejected: %d  Whole-request rejected: %d (retryable %d, non-retryable %d)  Ambiguous: %d\n",
		c.PartialRejected, c.WholeRequestRejected, c.RejectedRetryable, c.RejectedNonRetryable, c.Ambiguous)
	o := c.RequestOutcomes
	fmt.Fprintf(w, "Requests: %d (acknowledged %d, partial %d, rejected retryable %d [with retry hint %d], rejected non-retryable %d, ambiguous %d)\n",
		c.Requests, o.Acknowledged, o.PartialRejection, o.RejectedRetryable, o.RejectedWithRetryHint, o.RejectedNonRetryable, o.Ambiguous)
	fmt.Fprintf(w, "Body bytes: %d on the wire, %d uncompressed\n", c.Bytes, c.UncompressedBytes)
	if r.AccountingError != "" {
		fmt.Fprintf(w, "ACCOUNTING ERROR: %s\n", r.AccountingError)
	}
	if r.LoadSeconds > 0 {
		fmt.Fprintf(w, "Measured attempted rate: %.3f spans/s over %.3fs (includes final export completion)\n", float64(c.Attempted)/r.LoadSeconds, r.LoadSeconds)
	}
	v := r.Verification
	fmt.Fprintf(w, "Verification complete: %t; scanned rows: %d; distinct attempted: %d; missing: %d; visible duplicate rows: %d; unexpected: %d; outside namespace: %d\n",
		v.Complete, v.Rows, v.DistinctExpected, v.Missing, v.DuplicateRows, v.UnexpectedRows, v.OutsideNamespace)
	m := v.MissingBy
	if !v.Complete {
		fmt.Fprintf(w, "Verification stopped after %d of %d attempted spans; %d unscanned spans are counted as missing and the decomposition below covers the scanned part only.\n", v.ScannedAttempted, v.ScannedAttempted+v.Unscanned, v.Unscanned)
	}
	fmt.Fprintf(w, "Missing by acknowledgement: acknowledged-but-missing (DATA LOSS) %d [of which partial-request lower bound %d]; rejected-and-missing (expected) %d; partial-rejected-and-missing (expected) %d; ambiguous-and-missing %d\n",
		m.AcknowledgedMissing, m.PartialBeyondRejected, m.RejectedMissing, m.PartialRejectedMissing, m.AmbiguousMissing)
	p := v.PersistedBy
	fmt.Fprintf(w, "Persisted without acknowledgement: rejected-but-persisted %d; partial excess %d; ambiguous-but-persisted %d\n", p.RejectedButPersisted, p.PartialExcess, p.AmbiguousPersisted)
	if !v.Complete {
		fmt.Fprintln(w, "No zero-loss conclusion: independent verification is incomplete.")
	}
	for i, m := range v.MissingRanges {
		if i == 20 {
			fmt.Fprintf(w, "... %d more missing ranges in the result JSON\n", len(v.MissingRanges)-20)
			break
		}
		fmt.Fprintf(w, "Missing range: requests %d-%d (%s), scheduled at %.3f-%.3fs, %d spans missing\n", m.FirstRequest, m.LastRequest, m.Class, m.FirstScheduledS, m.LastScheduledS, m.MissingSpans)
	}
	if v.MissingRangesOmitted > 0 {
		fmt.Fprintf(w, "Missing ranges beyond the first %d: %d ranges, %d spans\n", maxMissingRanges, v.MissingRangesOmitted, v.MissingRangesOmittedSpans)
	}
	l := r.Lag
	if l.FromScheduled.Count == 0 {
		fmt.Fprintf(w, "Read-visible lag: not measured; seen 0 of %d acknowledged sentinels (%d registered)\n", l.Sampled, l.Registered)
	} else {
		fmt.Fprintf(w, "Read-visible lag from the scheduled send (sentinels: %d seen of %d acknowledged, %d never seen; poll %.0f ms): upper bound p50/p95/p99/p99.9/max %g/%g/%g/%g/%g ms; lower bound p50/p99 %g/%g ms\n",
			l.FromScheduled.Count, l.Sampled, l.Unseen, l.PollIntervalMS, l.FromScheduled.P50, l.FromScheduled.P95, l.FromScheduled.P99, l.FromScheduled.P999, l.FromScheduled.Max, l.LowerFromScheduled.P50, l.LowerFromScheduled.P99)
	}
	d := v.DurableLag
	if d.Lag.Count == 0 {
		fmt.Fprintf(w, "Durable (server insert) lag: %s; %d spans measured, %d inserted before their scheduled send\n", orUnknown(d.Status), d.Rows, d.NegativeRows)
	} else {
		fmt.Fprintf(w, "Durable (server insert) lag from the scheduled send, %s, %d stored spans (%d before their scheduled send): p50/p95/p99/p99.9/max %g/%g/%g/%g/%g ms; clock offset applied %.3f ms +/- %.3f ms\n",
			d.Status, d.Rows, d.NegativeRows, d.Lag.P50, d.Lag.P95, d.Lag.P99, d.Lag.P999, d.Lag.Max, d.ClockOffsetMS, d.ClockUncertaintyMS)
	}
	if r.Clock.Limited {
		fmt.Fprintln(w, "Clock-limited: the generator-to-ClickHouse offset or its uncertainty exceeds 10 ms; durable lags below that are not resolved.")
	}
	if k := r.RequestLatencyFromScheduled; k.Count > 0 {
		fmt.Fprintf(w, "OTLP request latency p50/p99/max: %g/%g/%g ms from the actual send, %g/%g/%g ms from the scheduled send\n",
			r.RequestLatency.P50, r.RequestLatency.P99, r.RequestLatency.Max, k.P50, k.P99, k.Max)
	}
	if sc := r.Schedule; sc.ScheduledSpans > 0 {
		fmt.Fprintf(w, "Schedule: %d spans scheduled, %d never started; %d requests started more than %.0f ms late (%d more than 1 s)\n",
			sc.ScheduledSpans, sc.NotStartedSpans, sc.LateStartRequests, sc.LateStartThresholdMS, sc.LateStartRequestsOver1s)
	}
	b := r.Backlog
	if b.Status == "measured" || b.Status == "measured-with-gaps" {
		fmt.Fprintf(w, "Collector backlog (%s, %d samples, %d failed): buffered rows p50/p99/max %d/%d/%d of %d; slope %.2f rows/s (second half %.2f); drained after load: %t (%.3fs); restarts seen %d; insert stalls %d\n",
			b.Status, b.Samples, b.FailedSamples, b.BufferedRows.P50, b.BufferedRows.P99, b.MaxBufferedRows, b.MaxPendingRows, b.SlopeRowsPerSecond, b.SecondHalfSlopeRowsPerSecond, b.Drained, b.DrainSeconds, b.CounterResets, len(b.InsertStalls))
	} else {
		fmt.Fprintf(w, "Collector backlog: %s\n", orUnknown(b.Status))
	}
	if rec := r.Recovery; rec != nil {
		fmt.Fprintf(w, "Recovery: verification complete %t; acknowledged-but-missing %d; visible duplicates %d; ambiguous-but-persisted %d\n", rec.Complete, rec.AcknowledgedMissing, rec.VisibleDuplicates, rec.AmbiguousPersisted)
		for _, f := range rec.Faults {
			recovery := "NOT recovered: " + f.NotRecoveredReason
			if f.RecoverySeconds != nil {
				recovery = fmt.Sprintf("recovered in %.3fs after the fault ended", *f.RecoverySeconds)
			}
			fmt.Fprintf(w, "Fault %s (%s) %.3f-%.3fs: %s; requests scheduled during it %+v\n", f.Name, f.Source, f.StartS, f.EndS, recovery, f.RequestsScheduledDuring)
		}
	}
	censored := func(n int64, p Percentiles) string {
		if n == 0 {
			return ""
		}
		return fmt.Sprintf("; %d censored queries had run at least p50/max %g/%g ms when cut off (lower bounds; excluding unfinished queries can bias successful-query latency low)", n, p.P50, p.Max)
	}
	for _, d := range r.DirectReads {
		if d.Latency.Count == 0 {
			fmt.Fprintf(w, "Direct ClickHouse %s/%s: %s; successes/errors/skipped/censored %d/%d/%d/%d; latency not measured%s\n", d.Phase, d.Name, d.Status, d.Successes, d.Errors, d.Skipped, d.Censored, censored(d.Censored, d.CensoredElapsed))
			continue
		}
		payload := ""
		if d.PayloadBytes != nil {
			payload = fmt.Sprintf("; payload text read per query p50/max %d/%d bytes", d.PayloadBytes.P50, d.PayloadBytes.Max)
		}
		fmt.Fprintf(w, "Direct ClickHouse %s/%s: %s; successes/errors/skipped/censored %d/%d/%d/%d; p50/p95/p99 %g/%g/%g ms; non-empty results %d of %d%s%s\n",
			d.Phase, d.Name, d.Status, d.Successes, d.Errors, d.Skipped, d.Censored, d.Latency.P50, d.Latency.P95, d.Latency.P99, d.NonEmpty, d.Successes, payload, censored(d.Censored, d.CensoredElapsed))
	}
	reads := append([]ProbeResult(nil), r.Reads...)
	sort.Slice(reads, func(i, j int) bool { return reads[i].Name < reads[j].Name })
	for _, p := range reads {
		if p.Latency.Count == 0 {
			fmt.Fprintf(w, "App %s: %s; successes/errors/censored %d/%d/%d; successful latency not measured%s\n", p.Name, p.Status, p.Successes, p.Errors, p.Censored, censored(p.Censored, p.CensoredElapsed))
			continue
		}
		fmt.Fprintf(w, "App %s: %s; successes/errors/censored %d/%d/%d; successful p50/p95/p99 %g/%g/%g ms%s\n", p.Name, p.Status, p.Successes, p.Errors, p.Censored, p.Latency.P50, p.Latency.P95, p.Latency.P99, censored(p.Censored, p.CensoredElapsed))
	}
	wl := r.Workload
	if wl.Spans > 0 {
		fmt.Fprintf(w, "Workload (realised): %d spans, %d traces, %d sessions; error fraction %.4f; variant cohort fraction %.4f; input bytes p50/p99 %d/%d; output bytes p50/p99 %d/%d\n",
			wl.Spans, wl.Traces, wl.Sessions, wl.ErrorFraction, wl.VariantFraction, wl.InputBytes.P50, wl.InputBytes.P99, wl.OutputBytes.P50, wl.OutputBytes.P99)
	}
	for _, warning := range r.Warnings {
		fmt.Fprintln(w, "WARNING: "+warning)
	}
	fmt.Fprintln(w, "Full-scale 15,000 spans/s x 24h = 1,296,000,000 spans; 60,000 spans/s x 60s = 3,600,000 spans. Neither is certified by a smoke/mock run.")
	for _, note := range r.Limitations {
		fmt.Fprintln(w, "- "+note)
	}
	return nil
}

func reportedBytes(n uint64) string {
	if n == 0 {
		return "not reported"
	}
	return fmt.Sprintf("%d bytes", n)
}

func reportedNumber(v float64) string {
	if v == 0 {
		return "not reported"
	}
	return fmt.Sprintf("%g", v)
}

func orUnknown(s string) string {
	if strings.TrimSpace(s) == "" {
		return "unknown"
	}
	return s
}

// Sizing uses caller-supplied MEASURED physical bytes per logical span per
// replica (including indexes/rollups). Wire bytes are not substituted for it.
type SizingInput struct {
	SpansPerDay                    float64 `json:"spans_per_day"`
	RetentionDays                  float64 `json:"retention_days"`
	PhysicalBytesPerSpanPerReplica float64 `json:"physical_bytes_per_span_per_replica"`
	Replicas                       int     `json:"replicas"`
	DiskBytesPerNode               float64 `json:"disk_bytes_per_node"`
	UsableFraction                 float64 `json:"usable_disk_fraction"`
	MeasuredSpansPerSecondPerShard float64 `json:"measured_spans_per_second_per_shard"`
	PeakSpansPerSecond             float64 `json:"peak_spans_per_second"`
}

type SizingResult struct {
	Kind                  string  `json:"kind"`
	RetainedPhysicalBytes float64 `json:"retained_physical_bytes_all_replicas"`
	StorageNodes          int64   `json:"storage_nodes"`
	ThroughputShards      int64   `json:"throughput_shards"`
	MinimumNodes          int64   `json:"minimum_nodes"`
}

func Size(in SizingInput) (SizingResult, error) {
	for _, n := range []float64{in.SpansPerDay, in.RetentionDays, in.PhysicalBytesPerSpanPerReplica, in.DiskBytesPerNode, in.UsableFraction, in.MeasuredSpansPerSecondPerShard, in.PeakSpansPerSecond} {
		if n <= 0 || math.IsInf(n, 0) || math.IsNaN(n) {
			return SizingResult{}, fmt.Errorf("all sizing inputs must be finite and positive; missing measurements are not zero")
		}
	}
	if in.Replicas < 1 || in.UsableFraction > 1 {
		return SizingResult{}, fmt.Errorf("replicas >= 1; usable disk fraction <= 1")
	}
	physical := in.SpansPerDay * in.RetentionDays * in.PhysicalBytesPerSpanPerReplica * float64(in.Replicas)
	storage := math.Ceil(physical / (in.DiskBytesPerNode * in.UsableFraction))
	shards := math.Ceil(in.PeakSpansPerSecond / in.MeasuredSpansPerSecondPerShard)
	minimum := math.Ceil(math.Max(storage, shards*float64(in.Replicas))/float64(in.Replicas)) * float64(in.Replicas)
	if math.IsInf(physical, 0) || minimum >= math.MaxInt64 || storage >= math.MaxInt64 || shards >= math.MaxInt64 {
		return SizingResult{}, fmt.Errorf("sizing inputs overflow")
	}
	return SizingResult{Kind: "extrapolation_not_measured_capacity", RetainedPhysicalBytes: physical, StorageNodes: int64(storage), ThroughputShards: int64(shards), MinimumNodes: int64(minimum)}, nil
}
