package ingestbench

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"sync"
	"time"
)

// The collector's backlog is sampled from its admin /healthz while the run goes
// on: buffered rows (acknowledged rows not yet in ClickHouse, which the
// byte leases account for) and the writer's cumulative insert counters. The
// summary separates backlog growth from throughput: a run whose backlog keeps
// growing has not reached a steady state, whatever its acknowledged rate.

// maxCollectorSamples bounds the samples kept in memory (about 28 hours at 1 s);
// every sample also goes to the telemetry stream.
const maxCollectorSamples = 100000

// CollectorSample is one /healthz reading. Counter fields are the writer's
// lifetime counters; they restart from zero when the collector restarts.
type CollectorSample struct {
	IngestBudgetBytes int64           `json:"ingest_budget_bytes,omitempty"`
	WorkUsedBytes     int64           `json:"work_used_bytes,omitempty"`
	WireUsedBytes     int64           `json:"wire_used_bytes,omitempty"`
	Ingest            json.RawMessage `json:"ingest,omitempty"`
	ElapsedS          float64         `json:"elapsed_seconds"`
	OK                bool            `json:"ok"`
	HTTPStatus        int             `json:"http_status,omitempty"`
	BufferedRows      int64           `json:"buffered_rows"`
	PendingRows       int64           `json:"pending_rows"`
	MaxPendingRows    int64           `json:"max_pending_rows"`
	RowsInserted      uint64          `json:"rows_inserted"`
	RowsDeadLettered  uint64          `json:"rows_dead_lettered"`
	BatchesRetried    uint64          `json:"batches_retried"`
	BatchesFailed     uint64          `json:"batches_failed"`
	Error             string          `json:"error,omitempty"`
}

// Window is a stretch of the run, in seconds from load start.
type Window struct {
	StartS  float64 `json:"start_offset_seconds"`
	EndS    float64 `json:"end_offset_seconds"`
	Samples int64   `json:"samples"`
}

type BacklogResult struct {
	BytePoolSamples   int64   `json:"byte_pool_samples,omitempty"`
	PeakWorkBytes     int64   `json:"peak_work_bytes,omitempty"`
	PeakWireBytes     int64   `json:"peak_wire_bytes,omitempty"`
	IngestBudgetBytes int64   `json:"ingest_budget_bytes,omitempty"`
	Status            string  `json:"status"`
	Method            string  `json:"method"`
	PollIntervalMS    float64 `json:"poll_interval_ms"`
	Samples           int64   `json:"samples"`
	FailedSamples     int64   `json:"failed_samples"`
	KeptSamples       int64   `json:"samples_kept_in_memory"`
	MaxPendingRows    int64   `json:"max_pending_rows"`
	// Buffered rows over the load phase.
	BufferedRows    Distribution `json:"buffered_rows_during_load"`
	MaxBufferedRows int64        `json:"max_buffered_rows"`
	// SlopeRowsPerSecond is the least-squares slope of buffered rows over the
	// whole load; SecondHalfSlope over its second half, where a ramp-up no
	// longer explains growth.
	SlopeRowsPerSecond           float64 `json:"buffered_rows_slope_rows_per_second"`
	SlopeSamples                 int64   `json:"slope_samples"`
	SecondHalfSlopeRowsPerSecond float64 `json:"second_half_slope_rows_per_second"`
	SecondHalfSamples            int64   `json:"second_half_samples"`
	// AtBudgetSamples applies only to historical positive row caps. Byte-pool
	// pressure cannot be inferred from row count; see PeakWorkBytes/PeakWireBytes.
	AtBudgetSamples int64 `json:"at_budget_samples"`
	// Drain after the load: the first sample at or after the end of the load
	// with no buffered rows.
	Drained          bool    `json:"drained_after_load"`
	DrainSeconds     float64 `json:"drain_seconds_after_load"`
	FinalBufferedRow int64   `json:"final_buffered_rows"`
	// The writer's counters over the run (restarts accounted for).
	RowsInserted          uint64   `json:"collector_rows_inserted"`
	RowsDeadLettered      uint64   `json:"collector_rows_dead_lettered"`
	BatchesRetried        uint64   `json:"collector_batches_retried"`
	BatchesFailed         uint64   `json:"collector_batches_failed"`
	InsertRateDuringLoad  float64  `json:"collector_insert_rows_per_second_during_load"`
	CounterResets         int64    `json:"collector_counter_resets_observed"`
	Unreachable           []Window `json:"unreachable_windows"`
	InsertStalls          []Window `json:"insert_stall_windows"`
	InsertStallThresholdS float64  `json:"insert_stall_threshold_seconds"`
	LastError             string   `json:"last_error,omitempty"`
}

type backlogPoller struct {
	url      string
	client   *http.Client
	interval time.Duration
	start    time.Time
	sink     *telemetrySink

	mu      sync.Mutex
	samples []CollectorSample
	total   int64
	failed  int64
}

func newBacklogPoller(c Config, client *http.Client, start time.Time, sink *telemetrySink) *backlogPoller {
	if c.CollectorHealthURL == "" {
		return nil
	}
	return &backlogPoller{url: c.CollectorHealthURL, client: client, interval: c.CollectorPollInterval, start: start, sink: sink}
}

func (p *backlogPoller) run(ctx context.Context) {
	if p == nil {
		return
	}
	ticker := time.NewTicker(p.interval)
	defer ticker.Stop()
	for {
		p.sample(ctx)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (p *backlogPoller) sample(ctx context.Context) {
	s := fetchCollectorSample(ctx, p.client, p.url, min(p.interval, 5*time.Second))
	if ctx.Err() != nil && !s.OK {
		// Cut off by the end of the observation: not an outage.
		return
	}
	s.ElapsedS = time.Since(p.start).Seconds()
	p.mu.Lock()
	p.total++
	if !s.OK {
		p.failed++
	}
	if len(p.samples) < maxCollectorSamples {
		p.samples = append(p.samples, s)
	}
	p.mu.Unlock()
	if p.sink != nil {
		_ = p.sink.write(telemetryEvent{Kind: "collector", Collector: &s})
	}
}

func fetchCollectorSample(ctx context.Context, client *http.Client, url string, timeout time.Duration) CollectorSample {
	ctx, cancel := context.WithTimeout(ctx, max(timeout, time.Second))
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return CollectorSample{Error: "invalid health URL"}
	}
	resp, err := client.Do(req)
	if err != nil {
		return CollectorSample{Error: "collector health request failed (URL omitted)"}
	}
	defer resp.Body.Close()
	body, err := readBounded(resp.Body, 1<<20)
	if err != nil {
		return CollectorSample{HTTPStatus: resp.StatusCode, Error: "collector health response unreadable or oversized"}
	}
	return ParseCollectorHealth(resp.StatusCode, body)
}

// ParseCollectorHealth reads one backlog sample from a collector /healthz
// response. A body without the admission and writer blocks (a collector that
// predates backlog reporting) is a failed sample, never a zero backlog.
func ParseCollectorHealth(status int, body []byte) CollectorSample {
	var health struct {
		Ingest json.RawMessage `json:"ingest"`
		Stats  *struct {
			RowsInserted     uint64
			RowsDeadLettered uint64
			BatchesRetried   uint64
			BatchesFailed    uint64
		} `json:"stats"`
		Admission *struct {
			BufferedRows   *int64 `json:"buffered_rows"`
			PendingRows    *int64 `json:"pending_rows"`
			MaxPendingRows *int64 `json:"max_pending_rows"`
		} `json:"admission"`
	}
	if err := json.Unmarshal(body, &health); err != nil {
		return CollectorSample{HTTPStatus: status, Error: fmt.Sprintf("collector health HTTP %d is not JSON", status)}
	}
	a := health.Admission
	if health.Stats == nil || a == nil || a.BufferedRows == nil || a.PendingRows == nil || a.MaxPendingRows == nil {
		return CollectorSample{HTTPStatus: status, Error: "collector /healthz does not report admission and writer stats (collector predates backlog reporting)"}
	}
	var ingest struct {
		BudgetBytes int64 `json:"budget_bytes"`
		Work        struct {
			InUseBytes int64 `json:"in_use_bytes"`
		} `json:"work"`
		Wire struct {
			InUseBytes int64 `json:"in_use_bytes"`
		} `json:"wire"`
	}
	if len(health.Ingest) > 0 && json.Unmarshal(health.Ingest, &ingest) != nil {
		return CollectorSample{HTTPStatus: status, Error: "malformed ingest budget telemetry"}
	}
	return CollectorSample{Ingest: health.Ingest, IngestBudgetBytes: ingest.BudgetBytes, WorkUsedBytes: ingest.Work.InUseBytes, WireUsedBytes: ingest.Wire.InUseBytes, OK: true, HTTPStatus: status, BufferedRows: *a.BufferedRows, PendingRows: *a.PendingRows, MaxPendingRows: *a.MaxPendingRows,
		RowsInserted: health.Stats.RowsInserted, RowsDeadLettered: health.Stats.RowsDeadLettered, BatchesRetried: health.Stats.BatchesRetried, BatchesFailed: health.Stats.BatchesFailed}
}

func (p *backlogPoller) kept() []CollectorSample {
	if p == nil {
		return nil
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]CollectorSample(nil), p.samples...)
}

// result summarises the samples. loadS is the load phase length; batch is the
// request size (for "one more batch would not fit"); stall is how long the
// writer may make no progress with rows buffered before it counts as a stall.
func (p *backlogPoller) result(loadS float64, batch int64, stall time.Duration) BacklogResult {
	if p == nil {
		return BacklogResult{Status: "not-configured", Method: "pass --collector-health-url to sample the collector backlog"}
	}
	samples := p.kept()
	p.mu.Lock()
	total, failed := p.total, p.failed
	p.mu.Unlock()
	return summariseBacklog(samples, total, failed, p.interval, loadS, batch, stall)
}

func summariseBacklog(samples []CollectorSample, total, failed int64, interval time.Duration, loadS float64, batch int64, stall time.Duration) BacklogResult {
	r := BacklogResult{Status: "measured", Method: "collector /healthz admission.buffered_rows and writer stats, sampled",
		PollIntervalMS: float64(interval) / float64(time.Millisecond), Samples: total, FailedSamples: failed, KeptSamples: int64(len(samples)),
		InsertStallThresholdS: stall.Seconds(), Unreachable: []Window{}, InsertStalls: []Window{}}
	if total == 0 {
		r.Status = "no-samples"
		return r
	}
	var buffered valueHistogram
	var all, half regression
	var prev *CollectorSample
	var loadFirstT, loadLastT float64
	var loadFirstInserted, loadLastInserted uint64
	loadSamples := 0
	var cumulative struct{ inserted, dead, retried, failed uint64 }
	var down, flat *Window
	var flatSince float64
	r.Drained = false
	for i := range samples {
		s := samples[i]
		if !s.OK {
			r.LastError = s.Error
			if down == nil {
				down = &Window{StartS: s.ElapsedS}
			}
			down.Samples++
			continue
		}
		if down != nil {
			down.EndS = s.ElapsedS
			r.Unreachable = append(r.Unreachable, *down)
			down = nil
		}
		if s.IngestBudgetBytes > 0 {
			r.BytePoolSamples++
			r.IngestBudgetBytes = s.IngestBudgetBytes
			r.PeakWorkBytes = max(r.PeakWorkBytes, s.WorkUsedBytes)
			r.PeakWireBytes = max(r.PeakWireBytes, s.WireUsedBytes)
		}
		r.MaxPendingRows = s.MaxPendingRows
		if prev != nil {
			if s.RowsInserted < prev.RowsInserted {
				// Counters restarted with the collector.
				r.CounterResets++
				cumulative.inserted += s.RowsInserted
				cumulative.dead += s.RowsDeadLettered
				cumulative.retried += s.BatchesRetried
				cumulative.failed += s.BatchesFailed
			} else {
				cumulative.inserted += s.RowsInserted - prev.RowsInserted
				cumulative.dead += sub(s.RowsDeadLettered, prev.RowsDeadLettered)
				cumulative.retried += sub(s.BatchesRetried, prev.BatchesRetried)
				cumulative.failed += sub(s.BatchesFailed, prev.BatchesFailed)
			}
			// An insert stall: rows buffered and no insert completed.
			if s.BufferedRows > 0 && prev.BufferedRows > 0 && s.RowsInserted == prev.RowsInserted && s.RowsDeadLettered == prev.RowsDeadLettered {
				if flat == nil {
					flat, flatSince = &Window{StartS: prev.ElapsedS}, prev.ElapsedS
				}
				flat.EndS, flat.Samples = s.ElapsedS, flat.Samples+1
			} else if flat != nil {
				if flat.EndS-flatSince >= stall.Seconds() {
					r.InsertStalls = append(r.InsertStalls, *flat)
				}
				flat = nil
			}
		}
		if s.ElapsedS <= loadS {
			buffered.add(s.BufferedRows)
			r.MaxBufferedRows = max(r.MaxBufferedRows, s.BufferedRows)
			all.add(s.ElapsedS, float64(s.BufferedRows))
			if s.ElapsedS >= loadS/2 {
				half.add(s.ElapsedS, float64(s.BufferedRows))
			}
			if s.MaxPendingRows > 0 && s.BufferedRows+batch > s.MaxPendingRows {
				r.AtBudgetSamples++
			}
			if loadSamples == 0 {
				loadFirstT, loadFirstInserted = s.ElapsedS, cumulative.inserted
			}
			loadSamples++
			loadLastT, loadLastInserted = s.ElapsedS, cumulative.inserted
		} else if !r.Drained && s.BufferedRows == 0 {
			r.Drained, r.DrainSeconds = true, s.ElapsedS-loadS
		}
		r.FinalBufferedRow = s.BufferedRows
		prev = &samples[i]
	}
	if down != nil {
		down.EndS = samples[len(samples)-1].ElapsedS
		r.Unreachable = append(r.Unreachable, *down)
	}
	if flat != nil && flat.EndS-flatSince >= stall.Seconds() {
		r.InsertStalls = append(r.InsertStalls, *flat)
	}
	r.BufferedRows = buffered.summary()
	r.SlopeRowsPerSecond, r.SlopeSamples = all.slope(), all.n
	r.SecondHalfSlopeRowsPerSecond, r.SecondHalfSamples = half.slope(), half.n
	r.RowsInserted, r.RowsDeadLettered, r.BatchesRetried, r.BatchesFailed = cumulative.inserted, cumulative.dead, cumulative.retried, cumulative.failed
	if loadLastT > loadFirstT {
		r.InsertRateDuringLoad = float64(loadLastInserted-loadFirstInserted) / (loadLastT - loadFirstT)
	}
	if failed == total {
		r.Status = "errors"
	} else if failed > 0 {
		r.Status = "measured-with-gaps"
	}
	return r
}

// stallThreshold is how long rows may stay buffered with no completed insert
// before the backlog summary reports an insert stall: twice the collector's
// flush age (5 s when unknown), at least 5 s.
func stallThreshold(limits *CollectorLimits) time.Duration {
	age := 5 * time.Second
	if limits != nil && limits.BatchMaxAgeMS > 0 {
		age = time.Duration(limits.BatchMaxAgeMS) * time.Millisecond
	}
	return max(5*time.Second, 2*age)
}

func sub(a, b uint64) uint64 {
	if a < b {
		return a
	}
	return a - b
}

// regression accumulates a least-squares line through (t, y) points.
type regression struct {
	n                      int64
	st, sy, stt, sty, base float64
	based                  bool
}

func (g *regression) add(t, y float64) {
	if !g.based {
		g.base, g.based = t, true
	}
	t -= g.base // centre for numerical stability over a day of samples
	g.n++
	g.st += t
	g.sy += y
	g.stt += t * t
	g.sty += t * y
}

// slope is rows per second; 0 when fewer than two distinct times.
func (g *regression) slope() float64 {
	n := float64(g.n)
	den := n*g.stt - g.st*g.st
	if g.n < 2 || den <= 0 || math.IsNaN(den) {
		return 0
	}
	return (n*g.sty - g.st*g.sy) / den
}

// firstAtOrBelow is the elapsed time of the first OK sample at or after from
// whose buffered rows are at most limit.
func firstAtOrBelow(samples []CollectorSample, from float64, limit int64) (float64, bool) {
	for _, s := range samples {
		if s.OK && s.ElapsedS >= from && s.BufferedRows <= limit {
			return s.ElapsedS, true
		}
	}
	return 0, false
}

// bufferedQuantile is the q-quantile of buffered rows of OK samples in [from, to).
func bufferedQuantile(samples []CollectorSample, from, to, q float64) (int64, int) {
	var v []int64
	for _, s := range samples {
		if s.OK && s.ElapsedS >= from && s.ElapsedS < to {
			v = append(v, s.BufferedRows)
		}
	}
	if len(v) == 0 {
		return 0, 0
	}
	sort.Slice(v, func(i, j int) bool { return v[i] < v[j] })
	return v[min(len(v)-1, max(0, int(math.Ceil(q*float64(len(v))))-1))], len(v)
}

// peakBuffered is the largest buffered-rows value of OK samples in [from, to].
func peakBuffered(samples []CollectorSample, from, to float64) int64 {
	var peak int64
	for _, s := range samples {
		if s.OK && s.ElapsedS >= from && s.ElapsedS <= to {
			peak = max(peak, s.BufferedRows)
		}
	}
	return peak
}
