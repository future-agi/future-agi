package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"time"
)

type clickHouse struct {
	cfg     Config
	client  *http.Client
	headers http.Header
}

// queryLimits bound one read query on the server (max_execution_time,
// max_memory_usage) and on the client (deadline, response size).
type queryLimits struct {
	timeout     time.Duration
	maxMemory   int64
	maxResponse int64
	settings    map[string]string
}

// controlLimits bound small preflight, provenance, lag and resource queries:
// 30 seconds, or the verification query limit when that is longer.
func (c Config) controlLimits() queryLimits {
	return queryLimits{timeout: max(30*time.Second, c.VerifyQueryTimeout), maxMemory: 1 << 30, maxResponse: 1 << 20}
}

func (c Config) verifyLimits() queryLimits {
	return queryLimits{timeout: c.effectiveVerifyQueryTimeout(), maxMemory: c.effectiveVerifyMaxMemory(), maxResponse: 16 << 20}
}

// effectiveVerifyTimeout is the exact-ID verification budget. The default
// grows with the run (10 minutes plus one second per 50,000 attempted spans, at
// most 48 hours): a fixed 10 minutes cannot verify a 24-hour profile. Time
// spent on durable lag queries does not count against it.
func (c Config) effectiveVerifyTimeout(attempted int64) time.Duration {
	if c.VerifyTimeout > 0 {
		return c.VerifyTimeout
	}
	return min(10*time.Minute+time.Duration(attempted/50000)*time.Second, 48*time.Hour)
}

// effectiveDurableLagTimeout is the durable lag's own budget: one query per
// chunk, about as costly as the exact-ID query, so by default the same size
// as the exact-ID budget. When it runs out the durable lag stops (and says
// so); the exact-ID verification carries on.
func (c Config) effectiveDurableLagTimeout(attempted int64) time.Duration {
	if c.DurableLagTimeout > 0 {
		return c.DurableLagTimeout
	}
	return c.effectiveVerifyTimeout(attempted)
}

// effectiveVerifyQueryTimeout bounds one chunk query: by default 30 seconds or
// one second per 20,000 chunk spans, whichever is larger, at most 30 minutes.
func (c Config) effectiveVerifyQueryTimeout() time.Duration {
	if c.VerifyQueryTimeout > 0 {
		return c.VerifyQueryTimeout
	}
	return min(max(30*time.Second, time.Duration(c.chunkSpans()/20000)*time.Second), 30*time.Minute)
}

func (c Config) effectiveVerifyMaxMemory() int64 {
	if c.VerifyMaxMemory > 0 {
		return c.VerifyMaxMemory
	}
	return 1 << 30
}

// chunkSpans is the verified spans per chunk: a multiple of spans-per-trace,
// so chunk trace ranges tile the namespace without overlap, and at most 65,536
// requests, so one chunk's grouped response stays small.
func (c Config) chunkSpans() int64 {
	size := min(c.VerifyChunk, 65536*int64(c.BatchSize))
	size = size / c.SpansPerTrace * c.SpansPerTrace
	return max(size, c.SpansPerTrace)
}

// readClient is the ClickHouse read client. It has no overall client timeout
// (each query carries its own deadline) and the same proxy/redirect rules as
// the load client.
func (c Config) readClient() *http.Client {
	client := c.client()
	client.Timeout = 0
	return client
}

var chErrorCode = regexp.MustCompile(`Code: (\d+)\. .*\(([A-Z_]+)\)`)

func (ch clickHouse) query(ctx context.Context, query string, params map[string]string) ([]byte, error) {
	return ch.queryWith(ctx, query, params, ch.cfg.controlLimits())
}

func (ch clickHouse) queryWith(ctx context.Context, query string, params map[string]string, lim queryLimits) ([]byte, error) {
	u, _ := url.Parse(ch.cfg.ClickHouseURL)
	q := u.Query()
	q.Set("readonly", "2")
	q.Set("max_execution_time", strconv.FormatInt(int64(math.Ceil(lim.timeout.Seconds())), 10))
	q.Set("max_memory_usage", strconv.FormatInt(lim.maxMemory, 10))
	q.Set("output_format_json_quote_64bit_integers", "0")
	// Without this ClickHouse may send HTTP 200 before the query finishes and
	// report a later failure inside the body; buffering the (small) result
	// makes the status code authoritative.
	q.Set("wait_end_of_query", "1")
	for k, v := range lim.settings {
		q.Set(k, v)
	}
	for k, v := range params {
		q.Set("param_"+k, v)
	}
	u.RawQuery = q.Encode()
	// The client deadline leaves the server time to report its own timeout.
	ctx, cancel := context.WithTimeout(ctx, lim.timeout+10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), bytes.NewBufferString(query+" FORMAT JSONEachRow"))
	if err != nil {
		return nil, fmt.Errorf("create ClickHouse request")
	}
	req.Header = ch.headers.Clone()
	resp, err := ch.client.Do(req)
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, fmt.Errorf("ClickHouse request timed out after %s (URL/credentials omitted)", lim.timeout+10*time.Second)
		}
		return nil, fmt.Errorf("ClickHouse request failed (URL/credentials omitted)")
	}
	defer resp.Body.Close()
	body, err := readBounded(resp.Body, lim.maxResponse)
	if resp.StatusCode != http.StatusOK {
		detail := ""
		if m := chErrorCode.FindSubmatch(body); m != nil {
			detail = fmt.Sprintf(" (code %s %s)", m[1], m[2])
		}
		return nil, fmt.Errorf("ClickHouse response failed: HTTP %d%s", resp.StatusCode, detail)
	}
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, fmt.Errorf("ClickHouse response timed out after %s while reading", lim.timeout+10*time.Second)
		}
		return nil, fmt.Errorf("ClickHouse response unreadable or larger than %d bytes", lim.maxResponse)
	}
	return body, nil
}

func readBounded(r io.Reader, max int64) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, max+1))
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}
	if int64(len(b)) > max {
		return nil, fmt.Errorf("oversized response")
	}
	return b, nil
}

func decodeOne(body []byte, out any) error {
	d := json.NewDecoder(bytes.NewReader(body))
	if err := d.Decode(out); err != nil {
		return fmt.Errorf("invalid JSON response (body omitted)")
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		return fmt.Errorf("unexpected trailing response")
	}
	return nil
}

func (ch clickHouse) table() string {
	return "`" + ch.cfg.Database + "`.`" + ch.cfg.Table + "`"
}

func (ch clickHouse) scope() string {
	return " FROM " + ch.table() + " WHERE project_id = {project:UUID} AND attrs_string['benchmark.run_id'] = {run:String}"
}

func (ch clickHouse) params() map[string]string {
	return map[string]string{"project": ch.cfg.ProjectID, "run": ch.cfg.RunID, "spt": strconv.FormatInt(ch.cfg.SpansPerTrace, 10)}
}

// preflight checks the entire project/run tag, without trace or time pruning:
// an earlier execution may have delivered only its last request. LIMIT 1 stops
// on the first match; zero requires a complete search. The returned count is a
// lower bound (0 or 1), not the total number of old rows. Errors fail closed.
// This is not an atomic reservation against concurrent executions or a check
// for inserts still invisible on the chosen read target.
func (ch clickHouse) preflight(ctx context.Context) (uint64, error) {
	lim := ch.cfg.controlLimits()
	// A deployment's "break" overflow policy must not turn an incomplete scan
	// into a successful zero-row result.
	lim.settings = map[string]string{"timeout_overflow_mode": "throw", "read_overflow_mode": "throw", "read_overflow_mode_leaf": "throw", "result_overflow_mode": "throw"}
	body, err := ch.queryWith(ctx, "SELECT count() AS rows FROM (SELECT 1"+ch.scope()+" LIMIT 1)", ch.params(), lim)
	if err != nil {
		return 0, err
	}
	var row struct {
		Rows *uint64 `json:"rows"`
	}
	if err := decodeOne(body, &row); err != nil || row.Rows == nil {
		return 0, fmt.Errorf("invalid ClickHouse count response")
	}
	return *row.Rows, nil
}

const sequenceExpr = "toUInt64OrDefault(attrs_string['benchmark.seq'], toUInt64(18446744073709551615))"
const identityExpr = "(id = leftPad(lower(hex(seq + toUInt64(1))), 16, '0') AND replaceAll(trace_id, '-', '') = concat({run:String}, leftPad(lower(hex(intDiv(seq, {spt:UInt64}) + toUInt64(1))), 16, '0')))"
const sentinelRequest = uint64(math.MaxUint64)

// timeline records which sequence numbers were generated in each second of
// the run. Verification turns it into a start_time window per chunk, which is
// what lets ClickHouse prune by the toStartOfHour(start_time) primary key
// instead of scanning the whole project for every chunk.
type timeline struct {
	Start time.Time `json:"start"`
	First []int64   `json:"first_seq"`
	End   []int64   `json:"end_seq"`
}

func newTimeline(start time.Time) *timeline { return &timeline{Start: start.UTC()} }

func (t *timeline) add(at time.Time, seq, n int64) {
	sec := max(0, int(at.Sub(t.Start)/time.Second))
	for len(t.First) <= sec {
		t.First = append(t.First, -1)
		t.End = append(t.End, -1)
	}
	if t.First[sec] < 0 || seq < t.First[sec] {
		t.First[sec] = seq
	}
	t.End[sec] = max(t.End[sec], seq+n)
}

// windowSlack covers timestamp rounding around the per-second timeline.
const windowSlack = time.Minute

// window is the start_time range that holds every span of [lo, hi): spans are
// stamped start = generation time - duration, duration <= maxSpanDuration.
func (t *timeline) window(lo, hi int64) (time.Time, time.Time) {
	first, last := -1, -1
	for i := range t.First {
		if t.First[i] >= 0 && t.End[i] > lo && t.First[i] < hi {
			if first < 0 {
				first = i
			}
			last = i
		}
	}
	if first < 0 {
		first, last = 0, max(0, len(t.First)-1)
	}
	return t.Start.Add(time.Duration(first)*time.Second - maxSpanDuration - windowSlack), t.Start.Add(time.Duration(last+1)*time.Second + windowSlack)
}

func (t *timeline) span() (time.Time, time.Time) {
	return t.Start.Add(-maxSpanDuration - windowSlack), t.Start.Add(time.Duration(len(t.First))*time.Second + windowSlack)
}

type chunkSpec struct {
	Lo      int64     `json:"lo"`
	Hi      int64     `json:"hi"`
	TraceLo string    `json:"trace_lo"`
	TraceHi string    `json:"trace_hi"`
	Last    bool      `json:"last"`
	TimeLo  time.Time `json:"time_lo"`
	TimeHi  time.Time `json:"time_hi"`
}

type timeWindow struct {
	Lo time.Time `json:"lo"`
	Hi time.Time `json:"hi"`
}

type chunkSummary struct {
	Index      int64  `json:"index"`
	Rows       uint64 `json:"rows"`
	Distinct   uint64 `json:"distinct"`
	Unexpected uint64 `json:"unexpected"`
}

// verifyAccum is the resumable running state of a verification.
type verifyAccum struct {
	Rows             uint64          `json:"rows"`
	Duplicates       uint64          `json:"duplicates"`
	Unexpected       uint64          `json:"unexpected"`
	OutsideNamespace uint64          `json:"outside_namespace"`
	Expected         [6]uint64       `json:"expected_by_class"`
	Present          [6]uint64       `json:"present_by_class"`
	PartialOpen      map[int64]int64 `json:"partial_open"`
	PartialLoss      uint64          `json:"partial_loss"`
	PartialRejected  uint64          `json:"partial_rejected_missing"`
	PartialExcess    uint64          `json:"partial_excess"`
	// Missing-span ranges by request (closed ranges, the open one and what
	// did not fit).
	Ranges             []MissingRange `json:"missing_ranges"`
	RangeOpen          *MissingRange  `json:"missing_range_open,omitempty"`
	RangesOmitted      int64          `json:"missing_ranges_omitted"`
	RangesOmittedSpans uint64         `json:"missing_ranges_omitted_spans"`
	// FaultLoss is accumulated per request before the ranges are compacted.
	FaultLoss []FaultLossWindow `json:"fault_loss_windows,omitempty"`
	// Durable (server insert) lag of every distinct stored span so far.
	DurableLag      sparseHistogram `json:"durable_lag"`
	DurableNegative uint64          `json:"durable_lag_negative_rows"`
	DurableError    string          `json:"durable_lag_error,omitempty"`
}

// maxMissingRanges bounds the missing-span ranges a result lists.
const maxMissingRanges = 1000

// trackMissing extends or starts the missing range for request r, whose part
// in the chunk being scanned misses `missing` spans. Requests arrive in
// increasing order; a request split across two chunks arrives twice.
func (a *verifyAccum) trackMissing(r int64, class outcomeClass, missing uint64, batch, rate int64) {
	open := a.RangeOpen
	if missing == 0 {
		if open != nil && open.LastRequest < r {
			a.closeRange()
		}
		return
	}
	scheduled := float64(r*batch) / float64(rate)
	if class == outcomeAcknowledged {
		a.trackFaultLoss(scheduled, missing)
	}
	if open != nil && open.Class == class.String() && open.LastRequest >= r-1 {
		open.LastRequest, open.LastScheduledS = r, scheduled
		open.MissingSpans += missing
		return
	}
	a.closeRange()
	a.RangeOpen = &MissingRange{Class: class.String(), FirstRequest: r, LastRequest: r, FirstScheduledS: scheduled, LastScheduledS: scheduled, MissingSpans: missing}
}

func (a *verifyAccum) closeRange() {
	if a.RangeOpen == nil {
		return
	}
	if len(a.Ranges) < maxMissingRanges {
		a.Ranges = append(a.Ranges, *a.RangeOpen)
	} else {
		a.RangesOmitted++
		a.RangesOmittedSpans += a.RangeOpen.MissingSpans
	}
	a.RangeOpen = nil
}

// lagOrigin places each request's scheduled send on the server's clock, so the
// server can compute created_at minus the scheduled send for every stored span.
type lagOrigin struct {
	StartUnixMicro   int64 `json:"load_start_unix_micro"`
	Rate             int64 `json:"rate"`
	OffsetMicro      int64 `json:"server_minus_generator_micro"`
	UncertaintyMicro int64 `json:"offset_uncertainty_micro"`
	OffsetMeasured   bool  `json:"offset_measured"`
}

// durableLagOrigin is the lag origin of a run that started at start, or nil
// when durable lag is disabled. Without a measured clock offset no
// correction is applied and the result says so.
func (c Config) durableLagOrigin(start time.Time, clock ClockSample) *lagOrigin {
	if !c.DurableLag {
		return nil
	}
	o := &lagOrigin{StartUnixMicro: start.UnixMicro(), Rate: c.Rate}
	if clock.Measured {
		o.OffsetMicro = int64(math.Round(clock.OffsetMS * 1000))
		o.UncertaintyMicro = int64(math.Ceil(clock.UncertaintyMS * 1000))
		o.OffsetMeasured = true
	}
	return o
}

// latencyBucketSQL is latencyBucketMicros in ClickHouse SQL, for a
// non-negative Int64 lag_us: exact below 256 us, else 128 sub-buckets per power
// of two, clamped to the last bucket. The exponent e is floor(log2(lag_us)),
// taken from log2 and then corrected by one either way with exact shifts, so
// floating-point rounding can never move a value to a neighbouring bucket.
const latencyBucketSQL = "if(lag_us < 256, lag_us, least(toInt64(" + histogramLastBucket + "), 256 + (e - 8) * 128 + toInt64(bitShiftRight(lag_us, greatest(e - 7, 0))) - 128))"

const latencyExponentSQL = "toInt64(floor(log2(greatest(lag_us, 1)))) AS e0, e0 - toInt64(bitShiftRight(lag_us, e0) = 0) + toInt64(bitShiftRight(lag_us, e0 + 1) > 0) AS e"

// durableLagQuery buckets, per distinct stored span of a chunk, its first
// created_at minus its request's scheduled send (microseconds, offset
// corrected) into the latency histogram's buckets; bucket -1 holds negative
// lags (inserted before the scheduled send: clock error or a forged value).
const durableLagQuery = "WITH " + latencyExponentSQL + " SELECT if(lag_us < 0, toInt64(-1), " + latencyBucketSQL + ") AS b," +
	" count() AS n, max(lag_us) AS max_us FROM (WITH " + sequenceExpr + " AS seq, (" + identityExpr + " AND seq >= {lo:UInt64} AND seq < {hi:UInt64}) AS valid" +
	" SELECT seq, toInt64(min(toUnixTimestamp64Micro(created_at))) - {offset:Int64} - ({origin:Int64} + toInt64(intDiv(intDiv(seq, {batch:UInt64}) * {batch:UInt64} * 1000000, {rate:UInt64}))) AS lag_us"

const checkpointSchema = "fi.otlpbench.verify-checkpoint.v3"

// verifyCheckpoint is everything a verification needs to resume in another
// process: the plan, the request outcomes and the progress. It holds no
// credentials.
type verifyCheckpoint struct {
	Schema      string         `json:"schema"`
	Config      Config         `json:"config"`
	Attempted   int64          `json:"attempted_spans"`
	ChunkSpans  int64          `json:"chunk_spans"`
	Chunks      []chunkSpec    `json:"chunks"`
	Windows     []timeWindow   `json:"namespace_windows"`
	Ledger      ledgerSnapshot `json:"ledger"`
	NextChunk   int64          `json:"next_chunk"`
	NextWindow  int64          `json:"next_window"`
	Accum       verifyAccum    `json:"accumulated"`
	Interesting []chunkSummary `json:"rechecked_chunk_summaries"`
	Complete    bool           `json:"complete"`
	Preexisting uint64         `json:"preexisting_run_rows"`
	// DurableLagOrigin is nil when durable lag is not measured.
	DurableLagOrigin *lagOrigin `json:"durable_lag_origin"`
}

func newCheckpoint(c Config, attempted int64, ledger *outcomeLedger, tl *timeline) *verifyCheckpoint {
	cp := &verifyCheckpoint{Schema: checkpointSchema, Config: c, Attempted: attempted, ChunkSpans: c.chunkSpans(),
		Ledger: ledger.snapshot(), Accum: verifyAccum{PartialOpen: map[int64]int64{}}}
	nsLo, nsHi := c.namespaceBounds()
	for lo := int64(0); lo < attempted; lo += cp.ChunkSpans {
		spec := chunkSpec{Lo: lo, Hi: min(lo+cp.ChunkSpans, attempted), TraceLo: c.traceString(lo), TraceHi: c.traceString(lo + cp.ChunkSpans)}
		if lo == 0 {
			spec.TraceLo = nsLo
		}
		if spec.Hi == attempted {
			spec.Last, spec.TraceHi = true, nsHi
		}
		spec.TimeLo, spec.TimeHi = tl.window(spec.Lo, spec.Hi)
		cp.Chunks = append(cp.Chunks, spec)
	}
	from, to := tl.span()
	cp.Windows = hourWindows(from, to)
	return cp
}

// hourWindows tiles [from, to) with windows that each lie inside one UTC clock
// hour. The spans primary key contains toStartOfHour(start_time), so a window
// inside one hour reads one hour of granules; a window crossing an hour
// boundary would read two.
func hourWindows(from, to time.Time) []timeWindow {
	var out []timeWindow
	for at := from.UTC(); at.Before(to); {
		next := minTime(at.Truncate(time.Hour).Add(time.Hour), to)
		out = append(out, timeWindow{Lo: at, Hi: next})
		at = next
	}
	return out
}

func minTime(a, b time.Time) time.Time {
	if a.Before(b) {
		return a
	}
	return b
}

func formatCHTime(t time.Time) string { return t.UTC().Format("2006-01-02 15:04:05.000000") }

type verifier struct {
	ch       clickHouse
	cfg      Config
	ledger   *outcomeLedger
	cp       *verifyCheckpoint
	path     string
	v        *Verification
	lastSave time.Time
	// saveCost is how long the last checkpoint write took.
	saveCost time.Duration
	// exactDeadline is when the exact-ID budget ends, not counting
	// durableSpent, the time durable lag queries took: exact-ID queries run
	// until exactDeadline + durableSpent, so the durable lag never spends the
	// exact-ID budget. durableBudget bounds durableSpent.
	exactDeadline time.Time
	durableSpent  time.Duration
	durableBudget time.Duration
}

// exactContext bounds one exact-ID verification query by the exact-ID budget.
func (vf *verifier) exactContext(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithDeadline(ctx, vf.exactDeadline.Add(vf.durableSpent))
}

// checkpointEvery bounds how often progress is saved between chunks (the
// checkpoint embeds the outcome ledger). Failures and completion always save,
// so only a killed process can lose up to this much progress.
const checkpointEvery = 10 * time.Second

// checkpointOverhead caps the share of verification time spent writing
// checkpoints: a day-long run whose outcomes alternate under backpressure has a
// ledger of millions of runs, so saves are spaced at least this many times their
// own duration apart.
const checkpointOverhead = 20

func (vf *verifier) saveProgress() error {
	if time.Since(vf.lastSave) < max(checkpointEvery, checkpointOverhead*vf.saveCost) {
		return nil
	}
	return vf.save()
}

// verify runs or resumes a verification. It never certifies anything it did
// not finish: a timeout, query failure, invalid response or a recheck that
// sees different counts leaves Complete false, and the checkpoint (when
// configured) keeps the progress for a later resume.
func (vf *verifier) run(ctx context.Context) Verification {
	start := time.Now()
	c := vf.cp.Config
	v := Verification{Method: "chunked-exact-id-per-request-outcome", TotalChunks: int64(len(vf.cp.Chunks)), ChunkSpans: vf.cp.ChunkSpans,
		ResumedAtChunk: vf.cp.NextChunk, TimeoutSeconds: c.effectiveVerifyTimeout(vf.cp.Attempted).Seconds(),
		QueryTimeoutSecs: c.effectiveVerifyQueryTimeout().Seconds(), QueryMaxMemory: c.effectiveVerifyMaxMemory(), PreexistingRunRows: vf.cp.Preexisting}
	if vf.cp.DurableLagOrigin != nil {
		v.DurableLagTimeoutSecs = c.effectiveDurableLagTimeout(vf.cp.Attempted).Seconds()
	}
	vf.v = &v
	vf.exactDeadline, vf.durableBudget = start.Add(c.effectiveVerifyTimeout(vf.cp.Attempted)), c.effectiveDurableLagTimeout(vf.cp.Attempted)
	fail := func(err error) Verification {
		vf.fill(&v)
		v.Chunks, v.NamespaceWindows = vf.cp.NextChunk, vf.cp.NextWindow
		v.Complete = false
		v.Error = err.Error()
		v.ElapsedSeconds = time.Since(start).Seconds()
		v.DurableLagElapsedSecs = vf.durableSpent.Seconds()
		_ = vf.save()
		return v
	}
	requests := (vf.cp.Attempted + int64(c.BatchSize) - 1) / int64(c.BatchSize)
	for r := int64(0); r < requests; r++ {
		if vf.ledger.class(r) == outcomeNotSent {
			return fail(fmt.Errorf("request %d has no recorded outcome; accounting is incomplete", r))
		}
	}
	for vf.cp.NextChunk < int64(len(vf.cp.Chunks)) {
		idx := vf.cp.NextChunk
		summary, err := vf.chunk(ctx, idx, true)
		if err != nil {
			return fail(fmt.Errorf("chunk %d of %d: %w", idx+1, len(vf.cp.Chunks), err))
		}
		if summary.Distinct != uint64(vf.cp.Chunks[idx].Hi-vf.cp.Chunks[idx].Lo) || summary.Rows != summary.Distinct || idx == int64(len(vf.cp.Chunks))-1 {
			if len(vf.cp.Interesting) < 15 || idx == int64(len(vf.cp.Chunks))-1 {
				vf.cp.Interesting = append(vf.cp.Interesting, summary)
			}
		}
		vf.cp.NextChunk++
		if err := vf.saveProgress(); err != nil {
			return fail(err)
		}
	}
	for vf.cp.NextWindow < int64(len(vf.cp.Windows)) {
		if err := vf.namespaceWindow(ctx, vf.cp.Windows[vf.cp.NextWindow]); err != nil {
			return fail(fmt.Errorf("namespace window %d of %d: %w", vf.cp.NextWindow+1, len(vf.cp.Windows), err))
		}
		vf.cp.NextWindow++
		if err := vf.saveProgress(); err != nil {
			return fail(err)
		}
	}
	// Late inserts, merges or replica lag during the scan must not certify a
	// result: rescan the last chunk and every chunk that looked incomplete and
	// require the same counts.
	for _, want := range vf.cp.Interesting {
		got, err := vf.chunk(ctx, want.Index, false)
		if err != nil {
			return fail(fmt.Errorf("recheck of chunk %d: %w", want.Index+1, err))
		}
		v.RecheckedChunks++
		if got != want {
			return fail(fmt.Errorf("chunk %d changed between passes (rows %d->%d, distinct %d->%d); rerun against a quiescent consistent read target", want.Index+1, want.Rows, got.Rows, want.Distinct, got.Distinct))
		}
	}
	if len(vf.cp.Accum.PartialOpen) != 0 {
		return fail(fmt.Errorf("%d partial-success requests were not fully scanned", len(vf.cp.Accum.PartialOpen)))
	}
	vf.fill(&v)
	v.Chunks, v.NamespaceWindows = vf.cp.NextChunk, vf.cp.NextWindow
	if err := vf.checkIdentity(&v); err != nil {
		return fail(err)
	}
	v.conclude()
	v.Complete = true
	v.DurableLag = vf.durableLag(v.DistinctExpected, true)
	vf.cp.Complete = true
	v.ElapsedSeconds = time.Since(start).Seconds()
	v.DurableLagElapsedSecs = vf.durableSpent.Seconds()
	if err := vf.save(); err != nil {
		v.Complete = false
		v.Error = err.Error()
	}
	return v
}

func (vf *verifier) fill(v *Verification) {
	a := vf.cp.Accum
	var expected, present uint64
	for class := outcomeAcknowledged; class <= outcomeAmbiguous; class++ {
		expected += a.Expected[class]
		present += a.Present[class]
	}
	v.Rows, v.DuplicateRows, v.UnexpectedRows, v.OutsideNamespace = a.Rows, a.Duplicates, a.Unexpected, a.OutsideNamespace
	v.DistinctExpected = present
	v.ScannedAttempted = expected
	v.Unscanned = uint64(vf.cp.Attempted) - expected
	v.Missing = uint64(vf.cp.Attempted) - present
	if vf.cp.Attempted > 0 {
		v.MissingFraction = float64(v.Missing) / float64(vf.cp.Attempted)
	}
	rejectedExpected := a.Expected[outcomeRejectedRetryable] + a.Expected[outcomeRejectedNonRetryable]
	rejectedPresent := a.Present[outcomeRejectedRetryable] + a.Present[outcomeRejectedNonRetryable]
	v.MissingBy = MissingByAcknowledgement{
		AcknowledgedMissing:    a.Expected[outcomeAcknowledged] - a.Present[outcomeAcknowledged] + a.PartialLoss,
		PartialBeyondRejected:  a.PartialLoss,
		PartialRejectedMissing: a.PartialRejected,
		RejectedMissing:        rejectedExpected - rejectedPresent,
		AmbiguousMissing:       a.Expected[outcomeAmbiguous] - a.Present[outcomeAmbiguous],
	}
	v.PersistedBy = PersistedByAcknowledgement{RejectedButPersisted: rejectedPresent, PartialExcess: a.PartialExcess, AmbiguousPersisted: a.Present[outcomeAmbiguous]}
	v.MissingRanges = append([]MissingRange{}, a.Ranges...)
	if a.RangeOpen != nil {
		v.MissingRanges = append(v.MissingRanges, *a.RangeOpen)
	}
	v.MissingRangesOmitted, v.MissingRangesOmittedSpans = a.RangesOmitted, a.RangesOmittedSpans
	v.FaultLoss = append([]FaultLossWindow(nil), a.FaultLoss...)
	v.DurableLag = vf.durableLag(v.DistinctExpected, false)
	// Only a finished, rechecked verification may conclude anything; run()
	// sets these after the recheck succeeds.
	v.DataLossDetected, v.ContractViolation = false, false
}

func (v *Verification) conclude() {
	v.DataLossDetected = v.MissingBy.AcknowledgedMissing > 0
	v.ContractViolation = v.PersistedBy.RejectedButPersisted > 0 || v.PersistedBy.PartialExcess > 0
}

func (vf *verifier) checkIdentity(v *Verification) error {
	a := vf.cp.Accum
	var expected uint64
	for class := outcomeAcknowledged; class <= outcomeAmbiguous; class++ {
		expected += a.Expected[class]
	}
	if expected != uint64(vf.cp.Attempted) {
		return fmt.Errorf("scanned chunks cover %d spans, not the %d attempted", expected, vf.cp.Attempted)
	}
	m := v.MissingBy
	if v.Unscanned != 0 || m.AcknowledgedMissing+m.PartialRejectedMissing+m.RejectedMissing+m.AmbiguousMissing != v.Missing {
		return fmt.Errorf("missing spans do not decompose by acknowledgement outcome")
	}
	if v.DistinctExpected+v.DuplicateRows+v.UnexpectedRows != v.Rows {
		return fmt.Errorf("scanned rows do not add up")
	}
	return nil
}

func (vf *verifier) save() error {
	if vf.path == "" {
		return nil
	}
	began := time.Now()
	b, err := json.Marshal(vf.cp)
	if err != nil {
		return fmt.Errorf("encode verification checkpoint: %w", err)
	}
	tmp, err := os.CreateTemp(filepath.Dir(vf.path), ".otlpbench-checkpoint-*")
	if err != nil {
		return fmt.Errorf("write verification checkpoint: %w", err)
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(b); err != nil {
		tmp.Close()
		return fmt.Errorf("write verification checkpoint: %w", err)
	}
	if err := tmp.Chmod(0600); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmp.Name(), vf.path); err != nil {
		return err
	}
	vf.lastSave = time.Now()
	vf.saveCost = vf.lastSave.Sub(began)
	return nil
}

// withRetries runs a read-only, idempotent query, retrying a failure a bounded
// number of times. Each attempt has its own server and client deadline.
func (vf *verifier) withRetries(ctx context.Context, query string, params map[string]string) ([]byte, error) {
	var lastErr error
	for attempt := 0; attempt <= vf.cp.Config.VerifyQueryRetries; attempt++ {
		if attempt > 0 {
			vf.v.QueryRetries++
			if !waitUntil(ctx, time.Now().Add(min(time.Duration(1<<attempt)*time.Second, 30*time.Second))) {
				break
			}
		}
		vf.v.Queries++
		body, err := vf.ch.queryWith(ctx, query, params, vf.cp.Config.verifyLimits())
		if err == nil {
			return body, nil
		}
		lastErr = err
		if ctx.Err() != nil {
			break
		}
	}
	if ctx.Err() != nil {
		return nil, fmt.Errorf("verification budget exhausted: %w", lastErr)
	}
	return nil, lastErr
}

// chunk scans one chunk: exact distinct spans per request, duplicates and rows
// that do not match a deterministic identity. With accumulate it adds the
// result to the running state; without, it only recomputes it for a recheck.
func (vf *verifier) chunk(ctx context.Context, idx int64, accumulate bool) (chunkSummary, error) {
	spec := vf.cp.Chunks[idx]
	c := vf.cp.Config
	batch := int64(c.BatchSize)
	params := vf.ch.params()
	params["lo"], params["hi"] = strconv.FormatInt(spec.Lo, 10), strconv.FormatInt(spec.Hi, 10)
	params["batch"] = strconv.FormatInt(batch, 10)
	params["trace_lo"], params["trace_hi"] = spec.TraceLo, spec.TraceHi
	params["time_lo"], params["time_hi"] = formatCHTime(spec.TimeLo), formatCHTime(spec.TimeHi)
	upper := "<"
	if spec.Last {
		upper = "<="
	}
	filter := " AND trace_id >= {trace_lo:String} AND trace_id " + upper + " {trace_hi:String}" +
		" AND start_time >= {time_lo:DateTime64(6, 'UTC')} AND start_time < {time_hi:DateTime64(6, 'UTC')}"
	query := "WITH " + sequenceExpr + " AS seq, (" + identityExpr + " AND seq >= {lo:UInt64} AND seq < {hi:UInt64}) AS valid" +
		" SELECT if(valid, intDiv(seq, {batch:UInt64}), toUInt64(18446744073709551615)) AS r, uniqExactIf(seq, valid) AS d, count() AS n" +
		vf.ch.scope() + filter + " GROUP BY r ORDER BY r"
	qctx, cancel := vf.exactContext(ctx)
	body, err := vf.withRetries(qctx, query, params)
	cancel()
	if err != nil {
		return chunkSummary{}, err
	}
	summary := chunkSummary{Index: idx}
	firstReq, lastReq := spec.Lo/batch, (spec.Hi-1)/batch
	overlap := func(r int64) int64 {
		return min(spec.Hi, (r+1)*batch) - max(spec.Lo, r*batch)
	}
	type group struct{ r, d, n uint64 }
	var groups []group
	d := json.NewDecoder(bytes.NewReader(body))
	for {
		var row struct {
			R *uint64 `json:"r"`
			D *uint64 `json:"d"`
			N *uint64 `json:"n"`
		}
		err := d.Decode(&row)
		if err == io.EOF {
			break
		}
		if err != nil || row.R == nil || row.D == nil || row.N == nil || *row.D > *row.N {
			return chunkSummary{}, fmt.Errorf("invalid verification chunk response")
		}
		if *row.R != sentinelRequest {
			if *row.R < uint64(firstReq) || *row.R > uint64(lastReq) || *row.D > uint64(overlap(int64(*row.R))) {
				return chunkSummary{}, fmt.Errorf("invalid verification chunk response")
			}
		} else if *row.D != 0 {
			return chunkSummary{}, fmt.Errorf("invalid verification chunk response")
		}
		groups = append(groups, group{*row.R, *row.D, *row.N})
		summary.Rows += *row.N
		if *row.R == sentinelRequest {
			summary.Unexpected += *row.N
		} else {
			summary.Distinct += *row.D
		}
	}
	if !accumulate {
		return summary, nil
	}
	a := &vf.cp.Accum
	if a.PartialOpen == nil {
		a.PartialOpen = map[int64]int64{}
	}
	for r := firstReq; r <= lastReq; r++ {
		class := vf.ledger.class(r)
		a.Expected[class] += uint64(overlap(r))
		if class == outcomePartial {
			if _, open := a.PartialOpen[r]; !open {
				a.PartialOpen[r] = 0
			}
		}
	}
	present := map[int64]uint64{}
	for _, g := range groups {
		a.Rows += g.n
		if g.r == sentinelRequest {
			a.Unexpected += g.n
			continue
		}
		class := vf.ledger.class(int64(g.r))
		a.Present[class] += g.d
		a.Duplicates += g.n - g.d
		present[int64(g.r)] += g.d
		if class == outcomePartial {
			a.PartialOpen[int64(g.r)] += int64(g.d)
		}
	}
	for r := firstReq; r <= lastReq; r++ {
		a.trackMissing(r, vf.ledger.class(r), uint64(overlap(r))-present[r], batch, c.Rate)
	}
	if spec.Last {
		a.closeRange()
	}
	vf.durableChunk(ctx, idx, spec, params, filter)
	// A partial request is final once the scan has passed its last span.
	for r := firstReq; r <= lastReq; r++ {
		present, open := a.PartialOpen[r]
		end := min((r+1)*batch, vf.cp.Attempted)
		if !open || end > spec.Hi {
			continue
		}
		n := end - r*batch
		rejected := vf.ledger.partial[r]
		missing := n - present
		a.PartialLoss += uint64(max(0, missing-rejected))
		a.PartialRejected += uint64(min(missing, rejected))
		a.PartialExcess += uint64(max(0, present-(n-rejected)))
		delete(a.PartialOpen, r)
	}
	return summary, nil
}

// namespaceWindow counts run-tagged rows in one hour of the run whose trace ID
// is outside this run's namespace: such rows are not covered by any chunk.
func (vf *verifier) namespaceWindow(ctx context.Context, w timeWindow) error {
	params := vf.ch.params()
	params["ns_lo"], params["ns_hi"] = vf.cp.Config.namespaceBounds()
	params["time_lo"], params["time_hi"] = formatCHTime(w.Lo), formatCHTime(w.Hi)
	ctx, cancel := vf.exactContext(ctx)
	defer cancel()
	body, err := vf.withRetries(ctx, "SELECT count() AS rows"+vf.ch.scope()+
		" AND start_time >= {time_lo:DateTime64(6, 'UTC')} AND start_time < {time_hi:DateTime64(6, 'UTC')}"+
		" AND NOT (trace_id >= {ns_lo:String} AND trace_id <= {ns_hi:String})", params)
	if err != nil {
		return err
	}
	var row struct {
		Rows *uint64 `json:"rows"`
	}
	if err := decodeOne(body, &row); err != nil || row.Rows == nil {
		return fmt.Errorf("invalid namespace window response")
	}
	vf.cp.Accum.OutsideNamespace += *row.Rows
	vf.cp.Accum.Unexpected += *row.Rows
	vf.cp.Accum.Rows += *row.Rows
	return nil
}

// durableLag summarises the durable lag accumulated so far. complete says the
// verification finished, so the measured rows must equal the distinct stored
// spans.
func (vf *verifier) durableLag(distinct uint64, complete bool) DurableLagResult {
	o := vf.cp.DurableLagOrigin
	if o == nil {
		return DurableLagResult{Status: "not-measured", Method: "disabled (--durable-lag=false)"}
	}
	a := vf.cp.Accum
	r := DurableLagResult{Method: "per distinct stored span: first created_at (server, DEFAULT now64(6)) minus the scheduled send of its request, corrected by the measured clock offset",
		Rows: uint64(a.DurableLag.N) + a.DurableNegative, NegativeRows: a.DurableNegative, Lag: a.DurableLag.summary(true), Buckets: a.DurableLag.buckets(),
		ClockOffsetMS: float64(o.OffsetMicro) / 1000, ClockUncertaintyMS: float64(o.UncertaintyMicro) / 1000, ClockMeasured: o.OffsetMeasured}
	switch {
	case a.DurableError != "":
		r.Status = "errors"
		r.Method += "; stopped after a query failure: " + a.DurableError
	case !complete:
		r.Status = "partial"
	case r.Rows != distinct:
		// Rows arrived or merged between the identity and lag queries.
		r.Status = "inconsistent"
	default:
		r.Status = "measured"
	}
	return r
}

// durableChunk adds one chunk's durable lags. A failure stops further durable
// lag queries but never the exact-ID verification.
func (vf *verifier) durableChunk(ctx context.Context, idx int64, spec chunkSpec, params map[string]string, filter string) {
	o, a := vf.cp.DurableLagOrigin, &vf.cp.Accum
	if o == nil || a.DurableError != "" {
		return
	}
	exhausted := func() {
		a.DurableError = fmt.Sprintf("durable lag budget of %s exhausted at chunk %d of %d; the exact-ID verification continued without it", vf.durableBudget, idx+1, len(vf.cp.Chunks))
	}
	left := vf.durableBudget - vf.durableSpent
	if left <= 0 {
		exhausted()
		return
	}
	params["offset"], params["origin"], params["rate"] = strconv.FormatInt(o.OffsetMicro, 10), strconv.FormatInt(o.StartUnixMicro, 10), strconv.FormatInt(o.Rate, 10)
	dctx, cancel := context.WithTimeout(ctx, left)
	began := time.Now()
	body, err := vf.withRetries(dctx, durableLagQuery+vf.ch.scope()+filter+" AND valid GROUP BY seq) GROUP BY b ORDER BY b", params)
	vf.durableSpent += time.Since(began)
	budgetOver := dctx.Err() != nil
	cancel()
	if err != nil {
		switch {
		case ctx.Err() != nil:
			// The whole verification was cancelled; the exact-ID scan reports it.
		case budgetOver:
			exhausted()
		default:
			a.DurableError = err.Error()
		}
		return
	}
	d := json.NewDecoder(bytes.NewReader(body))
	var hist sparseHistogram
	var negative uint64
	for {
		var row struct {
			B   *int64  `json:"b"`
			N   *uint64 `json:"n"`
			Max *int64  `json:"max_us"`
		}
		err := d.Decode(&row)
		if err == io.EOF {
			break
		}
		if err != nil || row.B == nil || row.N == nil || row.Max == nil || *row.B < -1 || *row.N > uint64(spec.Hi-spec.Lo) {
			a.DurableError = "invalid durable lag response"
			return
		}
		if *row.B < 0 {
			negative += *row.N
			continue
		}
		hist.addBucket(int(min(*row.B, histogramBuckets-1)), int64(*row.N), float64(*row.Max)/1000)
	}
	a.DurableLag.merge(hist)
	a.DurableNegative += negative
}

// verifyRun plans and runs the verification of a finished load.
func verifyRun(ctx context.Context, ch clickHouse, ledger *outcomeLedger, tl *timeline, attempted int64, preexisting uint64, origin *lagOrigin, faults ...FaultResult) Verification {
	cp := newCheckpoint(ch.cfg, attempted, ledger, tl)
	for _, f := range faults {
		cp.Accum.FaultLoss = append(cp.Accum.FaultLoss, FaultLossWindow{Name: f.Name, EndS: f.EndS})
	}
	cp.Preexisting = preexisting
	cp.DurableLagOrigin = origin
	vf := &verifier{ch: ch, cfg: ch.cfg, ledger: ledger, cp: cp, path: ch.cfg.VerifyCheckpoint}
	return vf.run(ctx)
}

// ResumeVerification continues a verification from a checkpoint written by an
// earlier run or resume. Overrides may raise the budget; the plan, outcomes and
// run identity always come from the checkpoint.
func ResumeVerification(ctx context.Context, path string, headers http.Header, override func(*Config)) (Config, Verification, error) {
	f, err := os.Open(path)
	if err != nil {
		return Config{}, Verification{}, err
	}
	defer f.Close()
	var cp verifyCheckpoint
	d := json.NewDecoder(io.LimitReader(f, 1<<30))
	d.DisallowUnknownFields()
	if err := d.Decode(&cp); err != nil || cp.Schema != checkpointSchema {
		return Config{}, Verification{}, fmt.Errorf("not a verification checkpoint of schema %s", checkpointSchema)
	}
	if override != nil {
		override(&cp.Config)
	}
	if err := cp.Config.validateURL(cp.Config.ClickHouseURL); err != nil {
		return cp.Config, Verification{}, err
	}
	if !runIDPattern.MatchString(cp.Config.RunID) || cp.ChunkSpans < 1 || cp.Attempted < 0 || cp.NextChunk < 0 || cp.NextChunk > int64(len(cp.Chunks)) || cp.NextWindow < 0 || cp.NextWindow > int64(len(cp.Windows)) {
		return cp.Config, Verification{}, fmt.Errorf("invalid verification checkpoint")
	}
	ledger, err := ledgerFromSnapshot(cp.Ledger)
	if err != nil || ledger.batch != int64(cp.Config.BatchSize) {
		return cp.Config, Verification{}, fmt.Errorf("invalid verification checkpoint ledger")
	}
	client := cp.Config.readClient()
	defer client.CloseIdleConnections()
	vf := &verifier{ch: clickHouse{cfg: cp.Config, client: client, headers: headers}, cfg: cp.Config, ledger: ledger, cp: &cp, path: path}
	if cp.Complete {
		v := Verification{Complete: true, Method: "chunked-exact-id-per-request-outcome", TotalChunks: int64(len(cp.Chunks)), Chunks: int64(len(cp.Chunks)), ChunkSpans: cp.ChunkSpans, NamespaceWindows: int64(len(cp.Windows)), PreexistingRunRows: cp.Preexisting}
		vf.fill(&v)
		v.conclude()
		v.DurableLag = vf.durableLag(v.DistinctExpected, true)
		return cp.Config, v, nil
	}
	return cp.Config, vf.run(ctx), nil
}
