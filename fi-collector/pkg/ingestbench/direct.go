package ingestbench

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
)

// DirectProbeConfig configures direct ClickHouse read probes. They measure the
// database alone, with fixed read-only queries shaped like the product's trace,
// session and filter reads, separately from application API probes.
type DirectProbeConfig struct {
	Enabled            bool          `json:"enabled"`
	Interval           time.Duration `json:"interval_ns"`
	MinAge             time.Duration `json:"min_age_ns"`
	AfterSettleSamples int           `json:"after_settle_samples"`
	QueryTimeout       time.Duration `json:"query_timeout_ns"`
}

func (d DirectProbeConfig) validate() error {
	if !d.Enabled {
		return nil
	}
	if d.Interval < 500*time.Millisecond || d.MinAge < 0 || d.MinAge > time.Hour || d.AfterSettleSamples < 0 || d.AfterSettleSamples > 1000 || d.QueryTimeout < 0 || d.QueryTimeout > 5*time.Minute {
		return fmt.Errorf("direct probes need interval >= 500ms, min-age 0..1h, after-settle samples 0..1000 and query timeout <= 5m")
	}
	return nil
}

// DirectReadResult is one probe in one phase.
type DirectReadResult struct {
	Name      string `json:"name"`
	Phase     string `json:"phase"`
	Kind      string `json:"kind"`
	Status    string `json:"status"`
	Query     string `json:"query"`
	Successes int64  `json:"successes"`
	Errors    int64  `json:"errors"`
	Skipped   int64  `json:"skipped_no_candidate"`
	// Censored counts queries cut off because their phase ended (the load
	// window closed or the run was cancelled): neither a success nor a failure.
	// Each had already run for its elapsed time when it was cut off, so
	// CensoredElapsed is a right-censored LOWER bound on those queries'
	// latency. They are the slowest queries of the phase, so Latency alone (the
	// successes) understates the database latency whenever Censored > 0.
	Censored        int64        `json:"window_censored_queries"`
	CensoredElapsed Percentiles  `json:"censored_query_elapsed_lower_bound"`
	NonEmpty        int64        `json:"non_empty_results"`
	Latency         Percentiles  `json:"successful_query_latency"`
	FailureLatency  Percentiles  `json:"failed_query_latency"`
	Rows            Distribution `json:"rows_returned"`
	// PayloadBytes is the input+output text the query read server-side, for the
	// probes that read span payloads the way a trace or session view does.
	PayloadBytes *Distribution `json:"payload_text_bytes_read,omitempty"`
	LastError    string        `json:"last_error,omitempty"`
}

type directProbe struct {
	name  string
	query string
	// payload: the query also returns payload_bytes, the input+output text it
	// read, so the probe pays for the span payload columns as a trace or session
	// view does instead of only for the key columns a count needs.
	payload bool
}

// Each probe is one query against the logical spans table; the parameters are
// bound by ClickHouse, never interpolated. Results are aggregated server-side so
// the response stays small; the payload columns are still read.
var directProbes = []directProbe{
	{"trace-by-id", "SELECT count() AS rows, sum(length(input) + length(output)) AS payload_bytes FROM %s WHERE project_id = {project:UUID} AND trace_id = {trace:String}", true},
	{"session-by-id", "SELECT count() AS rows, sum(length(input) + length(output)) AS payload_bytes FROM %s WHERE project_id = {project:UUID} AND trace_session_id = {session:UUID}", true},
	{"attribute-filter", "SELECT count() AS rows FROM %s WHERE project_id = {project:UUID} AND start_time >= {from:DateTime64(6, 'UTC')} AND attrs_string['benchmark.cohort'] = 'variant' AND status = 'ERROR'", false},
}

type directAccum struct {
	result              DirectReadResult
	good, bad, censored *histogram
	rows                *valueHistogram
	payload             *valueHistogram
}

type directRunner struct {
	ch    clickHouse
	start time.Time
	mu    sync.Mutex
	accum map[string]*directAccum // key phase/name
}

func newDirectRunner(ch clickHouse, start time.Time) *directRunner {
	return &directRunner{ch: ch, start: start, accum: map[string]*directAccum{}}
}

func (d *directRunner) acc(phase string, p directProbe) *directAccum {
	key := phase + "/" + p.name
	a := d.accum[key]
	if a == nil {
		a = &directAccum{result: DirectReadResult{Name: p.name, Phase: phase, Kind: "direct_clickhouse_sql", Query: fmt.Sprintf(p.query, d.ch.table())},
			good: &histogram{}, bad: &histogram{}, censored: &histogram{lowerBound: true}, rows: &valueHistogram{}, payload: &valueHistogram{}}
		d.accum[key] = a
	}
	return a
}

// candidate is an acknowledged span whose trace and session the probes read.
type candidate struct {
	seq int64
	ok  bool
}

// round runs each probe once. A trace or session probe without an
// acknowledged candidate old enough is skipped, never counted as a success.
func (d *directRunner) round(ctx context.Context, phase string, cand candidate) {
	cfg := d.ch.cfg
	lim := queryLimits{timeout: cfg.DirectProbes.QueryTimeout, maxMemory: 1 << 30, maxResponse: 1 << 16, settings: map[string]string{"use_query_cache": "0"}}
	if lim.timeout == 0 {
		lim.timeout = 10 * time.Second
	}
	from := d.start.Add(-maxSpanDuration - windowSlack)
	if lookback := time.Now().Add(-15 * time.Minute); lookback.After(from) {
		from = lookback
	}
	for _, p := range directProbes {
		if ctx.Err() != nil {
			// The phase is over: a query that never started is neither run nor
			// censored. (A ticker can fire together with the end of the load.)
			return
		}
		params := map[string]string{"project": cfg.ProjectID}
		switch p.name {
		case "trace-by-id":
			params["trace"] = cfg.traceString(cand.seq)
		case "session-by-id":
			session := cand.seq / (cfg.SpansPerTrace * cfg.TracesPerSession)
			params["session"] = detid.TraceSessionID(cfg.ProjectID, sessionName(cfg.RunID, session)).String()
		case "attribute-filter":
			params["from"] = formatCHTime(from)
		}
		d.mu.Lock()
		a := d.acc(phase, p)
		d.mu.Unlock()
		if !cand.ok && p.name != "attribute-filter" {
			d.mu.Lock()
			a.result.Skipped++
			d.mu.Unlock()
			continue
		}
		began := time.Now()
		body, err := d.ch.queryWith(ctx, fmt.Sprintf(p.query, d.ch.table()), params, lim)
		elapsed := time.Since(began)
		var row struct {
			Rows         *uint64 `json:"rows"`
			PayloadBytes *uint64 `json:"payload_bytes"`
		}
		if err == nil {
			if decodeOne(body, &row) != nil || row.Rows == nil || (p.payload && row.PayloadBytes == nil) {
				err = fmt.Errorf("invalid probe response")
			}
		}
		if err != nil && ctx.Err() != nil {
			// The phase ended while the query ran: not a database failure.
			d.mu.Lock()
			a.result.Censored++
			a.censored.add(elapsed)
			d.mu.Unlock()
			return
		}
		d.mu.Lock()
		if err != nil {
			a.result.Errors++
			a.result.LastError = err.Error()
			a.bad.add(elapsed)
		} else {
			a.result.Successes++
			a.good.add(elapsed)
			a.rows.add(int64(*row.Rows))
			if p.payload {
				a.payload.add(int64(*row.PayloadBytes))
			}
			if *row.Rows > 0 {
				a.result.NonEmpty++
			}
		}
		d.mu.Unlock()
	}
}

func (d *directRunner) results() []DirectReadResult {
	d.mu.Lock()
	defer d.mu.Unlock()
	var out []DirectReadResult
	for _, phase := range []string{"during-load", "after-settle"} {
		for _, p := range directProbes {
			a := d.accum[phase+"/"+p.name]
			if a == nil {
				out = append(out, DirectReadResult{Name: p.name, Phase: phase, Kind: "direct_clickhouse_sql", Status: "not-executed", Query: fmt.Sprintf(p.query, d.ch.table())})
				continue
			}
			r := a.result
			r.Latency, r.FailureLatency, r.CensoredElapsed, r.Rows = a.good.summary(), a.bad.summary(), a.censored.summary(), a.rows.summary()
			if p.payload {
				payload := a.payload.summary()
				r.PayloadBytes = &payload
			}
			switch {
			case r.Errors > 0:
				r.Status = "errors"
			case r.Successes > 0 && r.Censored > 0:
				// As for application probes: measured, and one query was cut
				// off by the end of its phase.
				r.Status = "measured-with-censoring"
			case r.Successes > 0:
				r.Status = "measured"
			case r.Censored > 0:
				// Every query that ran was cut off by the end of its phase.
				r.Status = "window-censored"
			case r.Skipped > 0:
				// Every round lacked an acknowledged request old enough to read.
				r.Status = "skipped-no-candidate"
			default:
				r.Status = "not-executed"
			}
			out = append(out, r)
		}
	}
	return out
}

// idleDirectReads reports the probes as not run, with the reason as status
// ("not-configured" when disabled, "not-executed" for a dry run).
func idleDirectReads(ch clickHouse, status string) []DirectReadResult {
	var out []DirectReadResult
	for _, phase := range []string{"during-load", "after-settle"} {
		for _, p := range directProbes {
			out = append(out, DirectReadResult{Name: p.name, Phase: phase, Kind: "direct_clickhouse_sql", Status: status, Query: fmt.Sprintf(p.query, ch.table())})
		}
	}
	return out
}
