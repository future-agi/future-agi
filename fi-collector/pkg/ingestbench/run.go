package ingestbench

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"runtime"
	"sort"
	"sync"
	"time"
)

type Options struct {
	OTLPHeaders       http.Header
	ClickHouseHeaders http.Header
	ResourceWriter    io.Writer
	// CommandLine is recorded after redaction (RedactArgs).
	CommandLine []string
	// Warn receives loud human-readable warnings (for example stderr).
	Warn io.Writer
}

type recentAck struct {
	seq int64
	at  time.Time
}

type runState struct {
	mu             sync.Mutex
	counters       Counters
	statuses       map[string]int64
	requestLatency histogram
	retryDelay     histogram
	samples        map[int64]lagSample
	lagErrors      int64
	ledger         *outcomeLedger
	ledgerErr      error
	workload       *workloadAccumulator
	recent         [64]recentAck
	recentN        int
}

func newRunState(c Config) *runState {
	return &runState{statuses: map[string]int64{}, samples: map[int64]lagSample{}, ledger: newOutcomeLedger(int64(c.BatchSize)), workload: newWorkloadAccumulator()}
}

func (r *Result) warn(options Options, message string) {
	r.Warnings = append(r.Warnings, message)
	if options.Warn != nil {
		fmt.Fprintf(options.Warn, "\n*** WARNING: %s ***\n\n", message)
	}
}

// Run sends real OTLP only with Execute=true. Verification always uses a
// separate read connection. It refuses reuse of a run ID before sending load.
func Run(ctx context.Context, c Config, options Options) (Result, error) {
	r := newResult(c)
	r.Provenance.CommandLine = RedactArgs(options.CommandLine)
	r.Provenance.Generator = generatorBuild(c.GeneratorRevision)
	r.Provenance.Host = hostInfo(ctx)
	r.Provenance.SUT, _ = resolveSUT(c.Revision, CollectorInfo{}, c.SUTNotes)
	if err := c.Validate(); err != nil {
		return r, err
	}
	// Generate one bounded preview during dry-run, but make NO network requests.
	preview, _, err := c.buildRequest(0, min(c.BatchSize, int(c.PlannedSpans())), time.Unix(1700000000, 0))
	if err != nil || preview.uncompressed > 16<<20 {
		return r, fmt.Errorf("batch exceeds the collector's 16MiB request cap; reduce batch-size")
	}
	r.Provenance.Collector.DeclaredMaxPendingRows = c.CollectorMaxPendingRows
	r.Provenance.Collector.Budget = CheckBudget(c.Rate, c.BatchSize, c.Concurrency, nil, c.CollectorMaxPendingRows)
	if !c.Execute {
		for _, w := range r.Provenance.Collector.Budget.Warnings() {
			r.warn(options, w)
		}
		r.DirectReads = disabledDirectReads(clickHouse{cfg: c})
		return r, nil
	}
	if !c.AllowUnauth && len(options.OTLPHeaders) == 0 {
		return r, fmt.Errorf("OTLP auth headers required; --allow-unauthenticated-fixture is only for an isolated test server")
	}
	probeHeaders := make(map[string]http.Header)
	for _, p := range c.ReadProbes {
		h, err := HeadersFromEnv(p.HeadersEnv)
		if err != nil {
			return r, err
		}
		probeHeaders[p.Name] = h
	}
	if c.VerifyCheckpoint != "" {
		if _, err := os.Stat(c.VerifyCheckpoint); err == nil {
			return r, fmt.Errorf("verification checkpoint already exists; use a new path (no data was sent)")
		}
	}
	client := c.client()
	defer client.CloseIdleConnections()
	readClient := c.readClient()
	defer readClient.CloseIdleConnections()
	ch := clickHouse{cfg: c, client: readClient, headers: options.ClickHouseHeaders}
	r.Status = "preflight-failed"

	// Provenance of the system under test, before any load.
	if c.CollectorHealthURL != "" {
		info := FetchCollectorHealth(ctx, readClient, c.CollectorHealthURL)
		info.DeclaredMaxPendingRows = c.CollectorMaxPendingRows
		r.Provenance.Collector = info
		if info.Error != "" {
			r.warn(options, "collector health: "+info.Error)
		}
	}
	r.Provenance.Collector.Budget = CheckBudget(c.Rate, c.BatchSize, c.Concurrency, r.Provenance.Collector.Limits, c.CollectorMaxPendingRows)
	for _, w := range r.Provenance.Collector.Budget.Warnings() {
		r.warn(options, w)
	}
	sut, warnings := resolveSUT(c.Revision, r.Provenance.Collector, c.SUTNotes)
	r.Provenance.SUT = sut
	for _, w := range warnings {
		r.warn(options, w)
	}
	if sut.Revision == "" {
		return r, fmt.Errorf("the revision of the system under test is unknown: pass --revision or a --collector-health-url whose /healthz reports one (no data was sent)")
	}
	r.Provenance.ClickHouse = clickHouseInfo(ctx, ch)
	if r.Provenance.ClickHouse.Error != "" {
		r.warn(options, "ClickHouse provenance incomplete: "+r.Provenance.ClickHouse.Error)
	}
	preexisting, err := ch.preflight(ctx)
	r.Verification.PreexistingRunRows = preexisting
	if err != nil {
		return r, fmt.Errorf("verification preflight: %w", err)
	}
	if preexisting != 0 {
		return r, fmt.Errorf("run-id already has %d rows; choose a new run-id (no data was sent)", preexisting)
	}
	exp, err := c.newExporter(client, options.OTLPHeaders)
	if err != nil {
		return r, err
	}
	defer exp.close()

	r.Resources = resourceSnapshots(ctx, ch)
	r.Started = time.Now().UTC()
	start := time.Now()
	tl := newTimeline(start)
	loadCtx, stopLoad := context.WithTimeout(ctx, c.Duration)
	defer stopLoad()
	monitorCtx, stopMonitor := context.WithCancel(ctx)
	s := newRunState(c)
	var monitorWG sync.WaitGroup
	monitorWG.Add(1)
	go func() { defer monitorWG.Done(); s.monitor(monitorCtx, ch) }()
	stopProgress, waitTelemetry := startTelemetry(monitorCtx, c, ch, s, options.ResourceWriter)
	reads := make(chan ProbeResult, len(c.ReadProbes))
	var readWG sync.WaitGroup
	for _, p := range c.ReadProbes {
		readWG.Add(1)
		go func(p ReadProbe) {
			defer readWG.Done()
			reads <- runReadProbe(loadCtx, client, p, probeHeaders[p.Name])
		}(p)
	}
	direct := newDirectRunner(ch, start)
	if c.DirectProbes.Enabled {
		readWG.Add(1)
		go func() {
			defer readWG.Done()
			ticker := time.NewTicker(c.DirectProbes.Interval)
			defer ticker.Stop()
			for {
				select {
				case <-loadCtx.Done():
					return
				case <-ticker.C:
					direct.round(loadCtx, "during-load", s.candidate(c.DirectProbes.MinAge))
				}
			}
		}()
	}
	sem := make(chan struct{}, c.Concurrency)
	var sendWG sync.WaitGroup
	var generated, dispatched, uncompressedBytes int64
	stride := max(int64(1), (c.PlannedSpans()+int64(c.BatchSize)*4096-1)/(int64(c.BatchSize)*4096))
	pipeCtx, stopPipe := context.WithCancel(loadCtx)
	defer stopPipe()
	pipe, workers := c.buildPipeline(pipeCtx, start)
	r.Workload.BuildWorkers = workers
	var buildLatency, dispatchDelay histogram
	r.StopReason = "planned-span-count"
produce:
	for generated < c.PlannedSpans() {
		due := start.Add(time.Duration(float64(dispatched) / float64(c.Rate) * float64(time.Second)))
		if !waitUntil(loadCtx, due) {
			r.StopReason = "duration-or-cancel"
			break
		}
		select {
		case <-loadCtx.Done():
			r.StopReason = "duration-or-cancel"
			break produce
		case sem <- struct{}{}:
		}
		var b builtBatch
		select {
		case next, ok := <-pipe:
			if !ok {
				<-sem
				r.StopReason = "duration-or-cancel"
				break produce
			}
			select {
			case b = <-next:
			case <-loadCtx.Done():
				<-sem
				r.StopReason = "duration-or-cancel"
				break produce
			}
		case <-loadCtx.Done():
			<-sem
			r.StopReason = "duration-or-cancel"
			break produce
		}
		generated += int64(b.n)
		s.mu.Lock()
		s.counters.Generated = generated
		s.mu.Unlock()
		if b.err != nil || b.seq != generated-int64(b.n) || b.batch.uncompressed > 16<<20 {
			<-sem
			r.StopReason = "invalid-or-oversized-batch"
			break
		}
		if int64(b.batch.uncompressed) > c.MaxBytes-uncompressedBytes {
			<-sem
			r.StopReason = "byte-budget"
			break
		}
		if loadCtx.Err() != nil {
			<-sem
			r.StopReason = "duration-or-cancel"
			break
		}
		buildLatency.add(b.buildTime)
		dispatchDelay.add(time.Since(due))
		uncompressedBytes += int64(b.batch.uncompressed)
		dispatched += int64(b.n)
		tl.add(b.now, b.seq, int64(b.n))
		s.mu.Lock()
		s.workload.addSpans(c, b.plans)
		s.mu.Unlock()
		sample := (b.seq/int64(c.BatchSize))%stride == 0
		sendWG.Add(1)
		go func(seq int64, n int, batch encodedBatch, sample bool) {
			defer sendWG.Done()
			defer func() { <-sem }()
			s.send(ctx, exp, seq, n, batch, sample)
		}(b.seq, b.n, b.batch, sample)
	}
	stopPipe()
	r.Workload.BuildLatency, r.Workload.DispatchDelay = buildLatency.summary(), dispatchDelay.summary()
	if r.StopReason == "planned-span-count" {
		waitUntil(loadCtx, start.Add(c.Duration))
	}
	sendWG.Wait()
	stopProgress()
	r.LoadSeconds = time.Since(start).Seconds()
	stopLoad()
	readWG.Wait()
	close(reads)
	for p := range reads {
		for i := range r.Reads {
			if r.Reads[i].Name == p.Name {
				r.Reads[i] = p
			}
		}
	}
	// Always give asynchronously acknowledged rows the configured quiet settle
	// window. Polling remains active; no writes or destructive cluster commands.
	waitUntil(ctx, time.Now().Add(c.Settle))
	stopMonitor()
	monitorWG.Wait()
	resourceErr := waitTelemetry()
	if c.DirectProbes.Enabled {
		for i := 0; i < c.DirectProbes.AfterSettleSamples && ctx.Err() == nil; i++ {
			direct.round(ctx, "after-settle", s.settledCandidate(i, c.DirectProbes.AfterSettleSamples))
		}
		r.DirectReads = direct.results()
	} else {
		r.DirectReads = disabledDirectReads(ch)
	}
	s.mu.Lock()
	r.Counters = s.counters
	r.Statuses = s.statuses
	r.RequestLatency = s.requestLatency.summary()
	r.RetryHints = RetryHints{Requests: s.counters.RequestOutcomes.RejectedWithRetryHint, Delay: s.retryDelay.summary()}
	workers, buildLat, delay := r.Workload.BuildWorkers, r.Workload.BuildLatency, r.Workload.DispatchDelay
	r.Workload = s.workload.stats(c)
	r.Workload.BuildWorkers, r.Workload.BuildLatency, r.Workload.DispatchDelay = workers, buildLat, delay
	ledger, ledgerErr := s.ledger, s.ledgerErr
	r.Lag = s.lagResult()
	s.mu.Unlock()
	r.Counters.Generated = generated
	if err := r.Counters.CheckIdentity(); err != nil {
		r.AccountingError = err.Error()
	} else if ledgerErr != nil {
		r.AccountingError = ledgerErr.Error()
	}
	r.Resources = append(r.Resources, resourceSnapshots(ctx, ch)...)
	r.Verification = verifyRun(ctx, ch, ledger, tl, r.Counters.Attempted, preexisting)
	r.Status = "incomplete"
	if r.Verification.Complete && r.AccountingError == "" {
		r.Status = "measured-with-errors"
		v := r.Verification
		if v.Missing == 0 && v.DuplicateRows == 0 && v.UnexpectedRows == 0 && r.Counters.Acknowledged == r.Counters.Attempted && r.Counters.Attempted == r.PlannedSpans && r.StopReason == "planned-span-count" {
			r.Status = "measured-profile-complete"
		}
	}
	for _, read := range r.Reads {
		if read.Status == "errors" {
			r.Status = downgrade(r.Status)
		}
	}
	for _, read := range r.DirectReads {
		if read.Status == "errors" {
			r.Status = downgrade(r.Status)
		}
	}
	if r.Verification.DataLossDetected {
		r.warn(options, fmt.Sprintf("%d acknowledged spans are missing from storage (data loss)", r.Verification.MissingBy.AcknowledgedMissing))
	}
	if r.Verification.ContractViolation {
		r.warn(options, "spans of rejected requests (or beyond a partial acknowledgement) were stored: the collector's rejection contract was violated")
	}
	if resourceErr != nil {
		return r, resourceErr
	}
	if ctx.Err() != nil {
		return r, ctx.Err()
	}
	return r, nil
}

// buildLookahead is how far ahead of its scheduled dispatch a request may be
// built. Span timestamps are taken when the request is built, so they stay
// within about this much of the send.
const buildLookahead = time.Second

type builtBatch struct {
	seq       int64
	n         int
	now       time.Time
	batch     encodedBatch
	plans     []spanPlan
	err       error
	buildTime time.Duration
}

// buildPipeline builds export requests on GOMAXPROCS workers, in order, each
// starting no earlier than buildLookahead before its scheduled dispatch.
// Serialisation and compression are the generator's dominant cost; doing them
// on one goroutine would make the generator, not the collector, the limit.
func (c Config) buildPipeline(ctx context.Context, start time.Time) (<-chan chan builtBatch, int) {
	workers := max(1, runtime.GOMAXPROCS(0))
	pipe := make(chan chan builtBatch, 2*workers)
	slots := make(chan struct{}, workers)
	go func() {
		defer close(pipe)
		for seq := int64(0); seq < c.PlannedSpans(); seq += int64(c.BatchSize) {
			n := int(min(int64(c.BatchSize), c.PlannedSpans()-seq))
			out := make(chan builtBatch, 1)
			select {
			case pipe <- out:
			case <-ctx.Done():
				return
			}
			due := start.Add(time.Duration(float64(seq) / float64(c.Rate) * float64(time.Second)))
			if !waitUntil(ctx, due.Add(-buildLookahead)) {
				out <- builtBatch{seq: seq, n: n, err: ctx.Err()}
				return
			}
			select {
			case slots <- struct{}{}:
			case <-ctx.Done():
				out <- builtBatch{seq: seq, n: n, err: ctx.Err()}
				return
			}
			go func(seq int64, n int, out chan builtBatch) {
				defer func() { <-slots }()
				began := time.Now()
				batch, plans, err := c.buildRequest(seq, n, began)
				out <- builtBatch{seq: seq, n: n, now: began, batch: batch, plans: plans, err: err, buildTime: time.Since(began)}
			}(seq, n, out)
		}
	}()
	return pipe, workers
}

func downgrade(status string) string {
	if status == "measured-profile-complete" {
		return "measured-with-errors"
	}
	return status
}

func waitUntil(ctx context.Context, due time.Time) bool {
	if ctx.Err() != nil {
		return false
	}
	t := time.NewTimer(max(time.Duration(0), time.Until(due)))
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func (s *runState) send(ctx context.Context, exp exporter, seq int64, n int, b encodedBatch, sample bool) {
	start := time.Now()
	s.mu.Lock()
	s.counters.Attempted += int64(n)
	s.counters.Requests++
	s.counters.UncompressedBytes += int64(b.uncompressed)
	if sample {
		s.samples[seq] = lagSample{Seq: seq, Sent: start}
	}
	s.mu.Unlock()
	o, wire := exp.export(ctx, b, n)
	s.finish(seq, n, b.uncompressed, wire, o, time.Since(start))
}

// finish records one request's outcome. Every request lands in exactly one
// outcome class, so the counters keep the accounting identity.
func (s *runState) finish(seq int64, n int, uncompressed, wire int, o exportOutcome, elapsed time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	spans := int64(n)
	c := &s.counters
	s.statuses[o.statusLabel]++
	s.requestLatency.add(elapsed)
	c.Bytes += int64(wire)
	s.workload.addRequest(uncompressed, wire)
	if o.successStatus {
		c.SuccessStatus += spans
	}
	if o.partialMessage || o.class == outcomePartial {
		c.PartialResponses++
	}
	switch o.class {
	case outcomeAcknowledged:
		c.Acknowledged += spans
		c.RequestOutcomes.Acknowledged++
	case outcomePartial:
		c.Acknowledged += spans - o.rejected
		c.PartialRejected += o.rejected
		c.RequestOutcomes.PartialRejection++
	case outcomeRejectedRetryable:
		c.WholeRequestRejected += spans
		c.RejectedRetryable += spans
		c.RequestOutcomes.RejectedRetryable++
		if o.retryHint {
			c.RequestOutcomes.RejectedWithRetryHint++
			s.retryDelay.add(o.retryAfter)
		}
	case outcomeRejectedNonRetryable:
		c.WholeRequestRejected += spans
		c.RejectedNonRetryable += spans
		c.RequestOutcomes.RejectedNonRetryable++
	default:
		c.Ambiguous += spans
		c.RequestOutcomes.Ambiguous++
	}
	c.Accepted = c.Acknowledged
	c.Unacknowledged = c.WholeRequestRejected + c.Ambiguous
	if o.class != outcomeAcknowledged && o.class != outcomePartial {
		c.RequestErrors++
	}
	if o.class == outcomeAcknowledged || o.class == outcomePartial {
		s.recent[s.recentN%len(s.recent)] = recentAck{seq: seq, at: time.Now()}
		s.recentN++
	} else {
		// Lag is measured only for acknowledged spans; a rejected span is
		// expected never to appear.
		delete(s.samples, seq)
	}
	if err := s.ledger.record(seq, o); err != nil && s.ledgerErr == nil {
		s.ledgerErr = err
	}
}

// candidate is the newest acknowledged request at least minAge old.
func (s *runState) candidate(minAge time.Duration) candidate {
	s.mu.Lock()
	defer s.mu.Unlock()
	cutoff := time.Now().Add(-minAge)
	best := candidate{}
	var bestAt time.Time
	for i := 0; i < min(s.recentN, len(s.recent)); i++ {
		a := s.recent[i]
		if !a.at.After(cutoff) && a.at.After(bestAt) {
			best, bestAt = candidate{seq: a.seq, ok: true}, a.at
		}
	}
	return best
}

// settledCandidate spreads after-settle probes across the run: sample i of n
// reads the first acknowledged request at or after fraction i/n of the run.
func (s *runState) settledCandidate(i, n int) candidate {
	s.mu.Lock()
	defer s.mu.Unlock()
	requests := int64(len(s.ledger.classes))
	if requests == 0 || n == 0 {
		return candidate{}
	}
	for r := requests * int64(i) / int64(n); r < requests; r++ {
		if c := s.ledger.classes[r]; c == outcomeAcknowledged {
			return candidate{seq: r * s.ledger.batch, ok: true}
		}
	}
	return candidate{}
}

func (s *runState) monitor(ctx context.Context, ch clickHouse) {
	ticker := time.NewTicker(ch.cfg.PollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.mu.Lock()
			pending := []lagSample{}
			for _, sample := range s.samples {
				if sample.Seen.IsZero() {
					pending = append(pending, sample)
				}
			}
			s.mu.Unlock()
			sort.Slice(pending, func(i, j int) bool { return pending[i].Seq < pending[j].Seq })
			seen, err := ch.observe(ctx, pending)
			s.mu.Lock()
			if err != nil && ctx.Err() == nil {
				s.lagErrors++
			}
			for seq, at := range seen {
				if sample, ok := s.samples[seq]; ok {
					sample.Seen = at
					s.samples[seq] = sample
				}
			}
			s.mu.Unlock()
		}
	}
}

func (s *runState) lagResult() LagResult {
	r := LagResult{Method: "sampled_send_to_first_query_visibility_upper_bound_acknowledged_only", Sampled: len(s.samples), QueryErrors: s.lagErrors}
	h := &histogram{}
	for _, sample := range s.samples {
		if sample.Seen.IsZero() {
			r.Unseen++
		} else {
			h.add(sample.Seen.Sub(sample.Sent))
		}
	}
	r.Latency = h.summary()
	return r
}
