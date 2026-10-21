package ingestbench

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"sort"
	"time"
)

type Counters struct {
	Generated        int64 `json:"generated_spans"`
	Attempted        int64 `json:"attempted_spans"`
	HTTP200          int64 `json:"http_200_spans"`
	Accepted         int64 `json:"accepted_spans"`
	Acknowledged     int64 `json:"acknowledged_spans"`
	PartialRejected  int64 `json:"otlp_partial_rejected_spans"`
	PartialResponses int64 `json:"otlp_partial_responses"`
	Unacknowledged   int64 `json:"unacknowledged_spans"`
	Requests         int64 `json:"requests"`
	RequestErrors    int64 `json:"request_errors"`
	Bytes            int64 `json:"attempted_otlp_body_bytes"`
}

type Verification struct {
	Complete           bool    `json:"complete"`
	Rows               uint64  `json:"persisted_rows"`
	DistinctExpected   uint64  `json:"persisted_distinct_attempted_spans"`
	DuplicateRows      uint64  `json:"duplicate_expected_rows_visible"`
	UnexpectedRows     uint64  `json:"unexpected_rows"`
	Missing            uint64  `json:"missing_attempted_spans"`
	MissingFraction    float64 `json:"missing_attempted_fraction"`
	Chunks             int64   `json:"verified_chunks"`
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

type Result struct {
	SchemaVersion  string             `json:"schema_version"`
	Status         string             `json:"status"`
	Config         Config             `json:"config"`
	Started        time.Time          `json:"started_at"`
	LoadSeconds    float64            `json:"load_elapsed_seconds"`
	StopReason     string             `json:"stop_reason"`
	PlannedSpans   int64              `json:"planned_spans"`
	Counters       Counters           `json:"counters"`
	HTTPStatuses   map[int]int64      `json:"otlp_http_statuses"`
	RequestLatency Percentiles        `json:"otlp_request_latency"`
	Verification   Verification       `json:"verification"`
	Lag            LagResult          `json:"lag"`
	Reads          []ProbeResult      `json:"application_reads"`
	Resources      []ResourceSnapshot `json:"resources"`
	Limitations    []string           `json:"limitations"`
}

func newResult(c Config) Result {
	r := Result{SchemaVersion: SchemaVersion, Status: "dry-run", Config: c,
		PlannedSpans: c.PlannedSpans(), HTTPStatuses: map[int]int64{},
		Limitations: []string{
			"Synthetic payload profiles are assumptions, not measured customer payload distributions.",
			"Accepted/acknowledged means the collector's OTLP response claims acceptance; neither means durable storage.",
			"No client retries. Unacknowledged exports may still be persisted; partial success does not identify rejected IDs.",
			"Visibility lag is sampled send-to-first-query-observation, an upper bound including polling/query delay, not storage commit latency.",
			"Duplicate counts cover rows visible during verification, not duplicates already removed by MergeTree merges.",
			"Verification is chunked, not a transactional cross-shard snapshot; quiesce the run and record replica routing/consistency.",
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
	fmt.Fprintf(w, "OTLP ingestion measurement (%s)\nRun: %s  Environment: %s  Topology: %s\n", r.Status, r.Config.RunID, r.Config.Environment, r.Config.Topology)
	fmt.Fprintf(w, "Target: %d spans/s for %.3fs = %d spans (not traces)\n", r.Config.Rate, r.Config.Duration.Seconds(), r.PlannedSpans)
	fmt.Fprintf(w, "Generated: %d  Attempted: %d  Accepted/acknowledged: %d/%d\nPartial rejected: %d  Unacknowledged: %d  OTLP body bytes: %d\n", r.Counters.Generated, r.Counters.Attempted, r.Counters.Accepted, r.Counters.Acknowledged, r.Counters.PartialRejected, r.Counters.Unacknowledged, r.Counters.Bytes)
	if r.LoadSeconds > 0 {
		fmt.Fprintf(w, "Measured attempted rate: %.3f spans/s over %.3fs (includes final export completion)\n", float64(r.Counters.Attempted)/r.LoadSeconds, r.LoadSeconds)
	}
	v := r.Verification
	fmt.Fprintf(w, "Verification complete: %t; stored rows: %d; distinct attempted: %d; missing: %d; visible duplicate rows: %d; unexpected: %d\n", v.Complete, v.Rows, v.DistinctExpected, v.Missing, v.DuplicateRows, v.UnexpectedRows)
	if !v.Complete {
		fmt.Fprintln(w, "No zero-loss conclusion: independent verification is incomplete.")
	}
	if r.Lag.Latency.Count == 0 {
		fmt.Fprintf(w, "Sampled visibility lag: not measured; seen 0 of %d samples\n", r.Lag.Sampled)
	} else {
		fmt.Fprintf(w, "Sampled visibility lag upper bound p50/p95/p99: %.3f/%.3f/%.3f ms; seen %d of %d\n", r.Lag.Latency.P50, r.Lag.Latency.P95, r.Lag.Latency.P99, r.Lag.Latency.Count, r.Lag.Sampled)
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
	fmt.Fprintln(w, "Full-scale 15,000 spans/s x 24h = 1,296,000,000 spans; 60,000 spans/s x 60s = 3,600,000 spans. Neither is certified by a smoke/mock run.")
	for _, note := range r.Limitations {
		fmt.Fprintln(w, "- "+note)
	}
	return nil
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
