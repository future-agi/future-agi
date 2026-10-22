package ingestbench

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
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
	if c.Attempted != c.Acknowledged+c.PartialRejected+c.WholeRequestRejected+c.Ambiguous {
		return fmt.Errorf("span accounting identity broken: attempted %d != acknowledged %d + partial rejected %d + whole-request rejected %d + ambiguous %d",
			c.Attempted, c.Acknowledged, c.PartialRejected, c.WholeRequestRejected, c.Ambiguous)
	}
	if c.WholeRequestRejected != c.RejectedRetryable+c.RejectedNonRetryable || c.Unacknowledged != c.WholeRequestRejected+c.Ambiguous {
		return fmt.Errorf("rejection sub-totals do not add up")
	}
	if c.Requests != c.RequestOutcomes.total() || c.RequestOutcomes.RejectedWithRetryHint > c.RequestOutcomes.RejectedRetryable {
		return fmt.Errorf("request accounting identity broken: %d requests, %d classified", c.Requests, c.RequestOutcomes.total())
	}
	if c.Accepted != c.Acknowledged {
		return fmt.Errorf("accepted and acknowledged differ")
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
	NamespaceWindows   int64   `json:"verified_namespace_windows"`
	RecheckedChunks    int64   `json:"rechecked_chunks"`
	ResumedAtChunk     int64   `json:"resumed_at_chunk"`
	Queries            int64   `json:"queries"`
	QueryRetries       int64   `json:"query_retries"`
	TimeoutSeconds     float64 `json:"timeout_seconds"`
	QueryTimeoutSecs   float64 `json:"query_timeout_seconds"`
	QueryMaxMemory     int64   `json:"query_max_memory_bytes"`
	Error              string  `json:"error,omitempty"`
	ElapsedSeconds     float64 `json:"elapsed_seconds"`
	PreexistingRunRows uint64  `json:"preexisting_run_rows"`
}

type Percentiles struct {
	Count int64   `json:"count"`
	P50   float64 `json:"p50_ms"`
	P95   float64 `json:"p95_ms"`
	P99   float64 `json:"p99_ms"`
	Max   float64 `json:"max_ms"`
}

// histogram has constant memory even for day-long runs. Bucket upper edges are
// 1ms up to 10s and 1s thereafter (through 24h); values are rounded up, not down.
type histogram struct {
	counts [96401]int64
	n      int64
	max    float64
}

func (h *histogram) add(d time.Duration) {
	ms := math.Max(0, float64(d)/float64(time.Millisecond))
	bucket := int(math.Ceil(ms))
	if bucket > 10000 {
		bucket = 10000 + int(math.Ceil((ms-10000)/1000))
	}
	if bucket >= len(h.counts) {
		bucket = len(h.counts) - 1
	}
	h.counts[bucket]++
	h.n++
	h.max = math.Max(h.max, ms)
}

func (h *histogram) summary() Percentiles {
	p := Percentiles{Count: h.n, Max: h.max}
	quantile := func(q float64) float64 {
		if h.n == 0 {
			return 0
		}
		target := int64(math.Ceil(q * float64(h.n)))
		var sum int64
		for i, n := range h.counts {
			sum += n
			if sum >= target {
				if i > 10000 {
					return float64(10000 + (i-10000)*1000)
				}
				return float64(i)
			}
		}
		return h.max
	}
	p.P50, p.P95, p.P99 = quantile(.5), quantile(.95), quantile(.99)
	return p
}

type ProbeResult struct {
	Name           string        `json:"name"`
	Kind           string        `json:"kind"`
	Status         string        `json:"status"`
	Successes      int64         `json:"successes"`
	Censored       int64         `json:"window_censored_requests"`
	Errors         int64         `json:"errors"`
	HTTPStatuses   map[int]int64 `json:"http_statuses"`
	Latency        Percentiles   `json:"successful_response_latency"`
	FailureLatency Percentiles   `json:"failed_response_latency"`
	ResponseBytes  int64         `json:"response_bytes"`
}

type LagResult struct {
	Method      string      `json:"method"`
	Sampled     int         `json:"sampled_spans"`
	Unseen      int         `json:"unseen_sampled_spans"`
	QueryErrors int64       `json:"query_errors"`
	Latency     Percentiles `json:"visibility_lag_upper_bound"`
}

// RetryHints summarises server-requested delays on retryable rejections.
type RetryHints struct {
	Requests int64       `json:"requests_with_retry_hint"`
	Delay    Percentiles `json:"requested_delay"`
}

type Result struct {
	SchemaVersion   string             `json:"schema_version"`
	Status          string             `json:"status"`
	Config          Config             `json:"config"`
	Provenance      Provenance         `json:"provenance"`
	Started         time.Time          `json:"started_at"`
	LoadSeconds     float64            `json:"load_elapsed_seconds"`
	StopReason      string             `json:"stop_reason"`
	PlannedSpans    int64              `json:"planned_spans"`
	Counters        Counters           `json:"counters"`
	AccountingError string             `json:"accounting_error,omitempty"`
	Statuses        map[string]int64   `json:"response_statuses"`
	RetryHints      RetryHints         `json:"retry_hints"`
	RequestLatency  Percentiles        `json:"otlp_request_latency"`
	Workload        WorkloadStats      `json:"workload"`
	Verification    Verification       `json:"verification"`
	Lag             LagResult          `json:"lag"`
	Reads           []ProbeResult      `json:"application_reads"`
	DirectReads     []DirectReadResult `json:"direct_clickhouse_reads"`
	Resources       []ResourceSnapshot `json:"resources"`
	Warnings        []string           `json:"warnings"`
	Limitations     []string           `json:"limitations"`
}

func newResult(c Config) Result {
	r := Result{SchemaVersion: SchemaVersion, Status: "dry-run", Config: c,
		PlannedSpans: c.PlannedSpans(), Statuses: map[string]int64{}, Warnings: []string{},
		Limitations: []string{
			"Synthetic payload profiles are a seeded model, not measured customer payload distributions.",
			"Accepted/acknowledged means the collector's OTLP response claims acceptance; neither means durable storage.",
			"No client retries. Ambiguous exports may still be persisted; partial success does not identify rejected IDs, so loss inside partial-success requests is a lower bound.",
			"Visibility lag is sampled send-to-first-query-observation, an upper bound including polling/query delay, not storage commit latency.",
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
	if r.SchemaVersion != SchemaVersion {
		return fmt.Errorf("unsupported result schema %q", r.SchemaVersion)
	}
	c := r.Counters
	fmt.Fprintf(w, "OTLP ingestion measurement (%s)\nRun: %s  Environment: %s  Topology: %s\n", r.Status, r.Config.RunID, r.Config.Environment, r.Config.Topology)
	fmt.Fprintf(w, "Wire path: %s%s\n", r.Config.Encoding, map[bool]string{true: " + gzip", false: ""}[r.Config.Gzip])
	sut := r.Provenance.SUT
	fmt.Fprintf(w, "System under test revision: %s (source: %s)\n", orUnknown(sut.Revision), orUnknown(sut.RevisionSource))
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
	if r.Lag.Latency.Count == 0 {
		fmt.Fprintf(w, "Sampled visibility lag: not measured; seen 0 of %d samples\n", r.Lag.Sampled)
	} else {
		fmt.Fprintf(w, "Sampled visibility lag upper bound p50/p95/p99: %.3f/%.3f/%.3f ms; seen %d of %d\n", r.Lag.Latency.P50, r.Lag.Latency.P95, r.Lag.Latency.P99, r.Lag.Latency.Count, r.Lag.Sampled)
	}
	for _, d := range r.DirectReads {
		if d.Latency.Count == 0 {
			fmt.Fprintf(w, "Direct ClickHouse %s/%s: %s; successes/errors %d/%d; latency not measured\n", d.Phase, d.Name, d.Status, d.Successes, d.Errors)
			continue
		}
		fmt.Fprintf(w, "Direct ClickHouse %s/%s: %s; successes/errors %d/%d; p50/p95/p99 %.3f/%.3f/%.3f ms; non-empty results %d of %d\n",
			d.Phase, d.Name, d.Status, d.Successes, d.Errors, d.Latency.P50, d.Latency.P95, d.Latency.P99, d.NonEmpty, d.Successes)
	}
	reads := append([]ProbeResult(nil), r.Reads...)
	sort.Slice(reads, func(i, j int) bool { return reads[i].Name < reads[j].Name })
	for _, p := range reads {
		if p.Latency.Count == 0 {
			fmt.Fprintf(w, "App %s: %s; successes/errors/censored %d/%d/%d; successful latency not measured\n", p.Name, p.Status, p.Successes, p.Errors, p.Censored)
			continue
		}
		fmt.Fprintf(w, "App %s: %s; successes/errors/censored %d/%d/%d; successful p50/p95/p99 %.3f/%.3f/%.3f ms\n", p.Name, p.Status, p.Successes, p.Errors, p.Censored, p.Latency.P50, p.Latency.P95, p.Latency.P99)
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
