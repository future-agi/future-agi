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
	// Faults are disruptions a test harness that owns the system under test
	// injects during the load (recovery.go). The tool itself never stops,
	// pauses or restarts infrastructure.
	Faults []Fault
}

type runState struct {
	mu             sync.Mutex
	start          time.Time
	counters       Counters
	statuses       map[string]int64
	requestLatency histogram
	// fromScheduled is request latency from the scheduled send (corrected
	// for coordinated omission); requestLatency is from the actual send.
	fromScheduled histogram
	retryDelay    histogram
	lagErrors     int64
	ledger        *outcomeLedger
	ledgerErr     error
	workload      *workloadAccumulator
	candidates    ackCandidates
	// inFlightSpans/inFlightRequests are the requests started (send) and not
	// finished yet, counted on their own: telemetry checks them against
	// attempted minus the outcome classes, so a class that misses a request
	// breaks the identity on the next progress line.
	inFlightSpans    int64
	inFlightRequests int64
	// Freeze scheduled-span telemetry when the producer stops, even if
	// started requests are still draining. Final Schedule uses this horizon.
	produceEnd      time.Duration
	producerStopped bool
	// Read-visible sentinels (sentinel.go).
	sentinels           map[int64]*sentinel
	pending             map[int64]*sentinel
	nextPollSeq         int64 // round-robin cursor; missing old sentinels must not starve later ones
	seen                int64
	sentinelsRegistered int64
	sentinelsDropped    int64
	pollLatency         histogram
	// acks is the acknowledgement series recovery uses; nil without faults.
	acks *ackSeries
}

func newRunState(c Config, start time.Time, faults bool) *runState {
	s := &runState{start: start, statuses: map[string]int64{}, ledger: newOutcomeLedger(int64(c.BatchSize)), workload: newWorkloadAccumulator(),
		sentinels: map[int64]*sentinel{}, pending: map[int64]*sentinel{}, candidates: newAckCandidates(c.DirectProbes.MinAge)}
	if faults {
		s.acks = &ackSeries{}
	}
	return s
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
	if len(options.Faults) > 16 {
		return r, fmt.Errorf("at most 16 injected faults")
	}
	for _, f := range options.Faults {
		if err := validateFault(f.Name, f.At, f.Duration, c.Duration); err != nil {
			return r, err
		}
		if f.Inject == nil || f.Restore == nil {
			return r, fmt.Errorf("fault %q needs Inject and Restore", f.Name)
		}
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
		status := "not-configured"
		if c.DirectProbes.Enabled {
			status = "not-executed"
		}
		r.DirectReads = idleDirectReads(clickHouse{cfg: c}, status)
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
		return r, fmt.Errorf("run-id already has at least %d rows; choose a new run-id (no data was sent)", preexisting)
	}
	exp, err := c.newExporter(client, options.OTLPHeaders)
	if err != nil {
		return r, err
	}
	defer exp.close()

	r.Resources = resourceSnapshots(ctx, ch)
	storage := tableStorage(ctx, ch)
	r.Provenance.ClickHouse.StorageBefore = &storage
	r.Clock = ClockResult{Method: clockMethod, Start: measureClock(ctx, ch)}
	r.Started = time.Now().UTC()
	start := time.Now()
	tl := newTimeline(start)
	loadCtx, stopLoad := context.WithTimeout(ctx, c.Duration)
	defer stopLoad()
	monitorCtx, stopMonitor := context.WithCancel(ctx)
	s := newRunState(c, start, len(options.Faults)+len(c.DeclaredFaults) > 0)
	sink := newTelemetrySink(options.ResourceWriter, c.RunID, start)
	var monitorWG sync.WaitGroup
	monitorWG.Add(2)
	go func() { defer monitorWG.Done(); s.monitor(monitorCtx, ch) }()
	backlog := newBacklogPoller(c, readClient, start, sink)
	go func() { defer monitorWG.Done(); backlog.run(monitorCtx) }()
	stopProgress, waitTelemetry := startTelemetry(monitorCtx, c, ch, s, sink)
	faults := &faultRecorder{}
	var faultWG sync.WaitGroup
	if len(options.Faults) > 0 {
		faultWG.Add(1)
		go func() { defer faultWG.Done(); runFaults(loadCtx, options.Faults, start, sink, faults) }()
	}
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
	stride := c.sentinelStride()
	pipeCtx, stopPipe := context.WithCancel(loadCtx)
	defer stopPipe()
	pipe, workers := c.buildPipeline(pipeCtx, start)
	r.Workload.BuildWorkers = workers
	var buildLatency, dispatchDelay histogram
	var lateOver10ms, lateOver1s int64
	r.StopReason = "planned-span-count"
produce:
	for generated < c.PlannedSpans() {
		due := start.Add(c.batchDue(dispatched))
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
		if stop := batchStop(loadCtx, b, generated); stop != "" {
			// A batch that was never built is not generated.
			<-sem
			r.StopReason = stop
			break
		}
		generated += int64(b.n)
		s.mu.Lock()
		s.counters.Generated = generated
		s.mu.Unlock()
		if b.batch.uncompressed > 16<<20 {
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
		// The start delay after the scheduled send: what a closed-loop
		// generator would silently omit.
		delay := time.Since(due)
		dispatchDelay.add(delay)
		if delay > lateStartThreshold {
			lateOver10ms++
		}
		if delay > time.Second {
			lateOver1s++
		}
		uncompressedBytes += int64(b.batch.uncompressed)
		dispatched += int64(b.n)
		tl.add(b.now, b.seq, int64(b.n))
		s.mu.Lock()
		s.workload.addSpans(c, b.plans)
		s.mu.Unlock()
		isSentinel := (b.seq/int64(c.BatchSize))%stride == 0
		sendWG.Add(1)
		go func(b builtBatch, sentinel bool, due time.Time) {
			defer sendWG.Done()
			defer func() { <-sem }()
			s.send(ctx, exp, b.seq, b.n, b.batch, sentinel, due, b.now)
		}(b, isSentinel, due)
	}
	produceEnd := s.endProduction()
	stopPipe()
	r.Workload.BuildLatency, r.Workload.DispatchDelay = buildLatency.summary(), dispatchDelay.summary()
	if r.StopReason == "planned-span-count" {
		waitUntil(loadCtx, start.Add(c.Duration))
	}
	sendWG.Wait()
	faultWG.Wait()
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
	// window. Sentinel and backlog polling remain active; no writes or
	// destructive cluster commands.
	waitUntil(ctx, time.Now().Add(c.Settle))
	stopMonitor()
	monitorWG.Wait()
	resourceErr := waitTelemetry()
	r.Clock.End = measureClock(ctx, ch)
	r.Clock.conclude()
	if c.DirectProbes.Enabled {
		for i := 0; i < c.DirectProbes.AfterSettleSamples && ctx.Err() == nil; i++ {
			direct.round(ctx, "after-settle", s.settledCandidate(i, c.DirectProbes.AfterSettleSamples))
		}
		r.DirectReads = direct.results()
	} else {
		r.DirectReads = idleDirectReads(ch, "not-configured")
	}
	s.mu.Lock()
	r.Counters = s.counters
	r.Statuses = s.statuses
	r.RequestLatency = s.requestLatency.summary()
	r.RequestLatencyFromScheduled = s.fromScheduled.summary()
	r.RetryHints = RetryHints{Requests: s.counters.RequestOutcomes.RejectedWithRetryHint, Delay: s.retryDelay.summary()}
	workers, buildLat, delay := r.Workload.BuildWorkers, r.Workload.BuildLatency, r.Workload.DispatchDelay
	r.Workload = s.workload.stats(c)
	r.Workload.BuildWorkers, r.Workload.BuildLatency, r.Workload.DispatchDelay = workers, buildLat, delay
	ledger, ledgerErr := s.ledger, s.ledgerErr
	r.Lag = s.lagResult(c, start)
	s.mu.Unlock()
	r.Counters.Generated = generated
	r.Schedule = scheduleResult(c, produceEnd, r.Counters.Attempted, delay, lateOver10ms, lateOver1s)
	if err := r.Counters.CheckIdentity(); err != nil {
		r.AccountingError = err.Error()
	} else if ledgerErr != nil {
		r.AccountingError = ledgerErr.Error()
	}
	r.Backlog = backlog.result(r.LoadSeconds, int64(c.BatchSize), stallThreshold(r.Provenance.Collector.Limits))
	r.Resources = append(r.Resources, resourceSnapshots(ctx, ch)...)
	// Preserve exact per-request loss in these reporting windows before the
	// verifier compacts its diagnostic ranges (which may cross fault ends).
	records := faults.all()
	for _, f := range c.DeclaredFaults {
		records = append(records, FaultResult{Name: f.Name, Source: "operator-declared", StartS: f.At.Seconds(), EndS: (f.At + f.Duration).Seconds()})
	}
	sort.Slice(records, func(i, j int) bool { return records[i].StartS < records[j].StartS })
	r.Verification = verifyRun(ctx, ch, ledger, tl, r.Counters.Attempted, preexisting, c.durableLagOrigin(start, r.Clock.Start), records...)
	storage = tableStorage(ctx, ch)
	r.Provenance.ClickHouse.StorageAfter = &storage
	if len(records) > 0 {
		s.mu.Lock()
		records = analyseRecovery(c, records, s, start, backlog.kept())
		s.mu.Unlock()
		rec := &RecoveryResult{Method: recoveryMethod, Faults: records}
		rec.attachVerification(c, r.Verification, r.Backlog, r.Provenance.Collector.Budget)
		r.Recovery = rec
	}
	r.Status = "incomplete"
	if r.Verification.Complete && r.AccountingError == "" {
		r.Status = "measured-with-errors"
		v := r.Verification
		profileDone := r.Counters.Attempted == r.PlannedSpans && r.StopReason == "planned-span-count"
		if r.Recovery == nil && profileDone && v.Missing == 0 && v.DuplicateRows == 0 && v.UnexpectedRows == 0 && r.Counters.Acknowledged == r.Counters.Attempted {
			r.Status = "measured-profile-complete"
		}
		if r.Recovery != nil && profileDone && r.Recovery.Passed() {
			r.Status = "measured-recovery-complete"
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
	if r.Lag.Unseen > 0 {
		r.warn(options, fmt.Sprintf("%d acknowledged sentinels never became visible by the end of the settle window", r.Lag.Unseen))
	}
	if r.Recovery != nil {
		for _, f := range r.Recovery.Faults {
			if f.Error != "" {
				r.warn(options, fmt.Sprintf("fault %s: %s", f.Name, f.Error))
			}
			if !f.Recovered {
				r.warn(options, fmt.Sprintf("fault %s: not recovered within the run: %s", f.Name, f.NotRecoveredReason))
			}
		}
	}
	if resourceErr != nil {
		return r, resourceErr
	}
	if ctx.Err() != nil {
		return r, ctx.Err()
	}
	return r, nil
}

// lateStartThreshold is the start delay after the scheduled send above which a
// request counts as a late start.
const lateStartThreshold = 10 * time.Millisecond

// scheduleResult relates what the open-loop schedule called for to what was
// started: spans whose scheduled send passed while the producer was running,
// how many of them never started, and how late the started ones were.
func scheduleResult(c Config, produceEnd time.Duration, attempted int64, delay Percentiles, over10ms, over1s int64) ScheduleResult {
	scheduled := c.scheduledSpans(produceEnd)
	return ScheduleResult{Method: "open-loop: request k is due at load start + k*batch_size/rate; delays are measured from that due time",
		ScheduledSpans: scheduled, NotStartedSpans: max(0, scheduled-attempted), LateStartThresholdMS: float64(lateStartThreshold) / float64(time.Millisecond),
		LateStartRequests: over10ms, LateStartRequestsOver1s: over1s, StartDelay: delay}
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
			due := start.Add(c.batchDue(seq))
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

// batchStop is why a batch taken from the build pipeline cannot be dispatched,
// or "" when it can. A build that failed because the load window closed (or the
// run was cancelled) is the end of the load, not an invalid batch.
func batchStop(loadCtx context.Context, b builtBatch, generated int64) string {
	switch {
	case b.err != nil && loadCtx.Err() != nil:
		return "duration-or-cancel"
	case b.err != nil || b.seq != generated:
		return "invalid-or-oversized-batch"
	}
	return ""
}

func downgrade(status string) string {
	if status == "measured-profile-complete" || status == "measured-recovery-complete" {
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

// send exports one request. due is its scheduled send, built the time its span
// timestamps were taken.
func (s *runState) send(ctx context.Context, exp exporter, seq int64, n int, b encodedBatch, sentinel bool, due, built time.Time) {
	start := time.Now()
	s.mu.Lock()
	s.counters.Attempted += int64(n)
	s.counters.Requests++
	s.counters.UncompressedBytes += int64(b.uncompressed)
	s.inFlightSpans += int64(n)
	s.inFlightRequests++
	if sentinel {
		s.registerSentinel(seq, due, start, built)
	}
	s.mu.Unlock()
	o, wire := exp.export(ctx, b, n)
	end := time.Now()
	s.finish(seq, n, b.uncompressed, wire, o, end.Sub(start), end.Sub(due), end)
}

// finish records one request's outcome. Every request lands in exactly one
// outcome class, so the counters keep the accounting identity. elapsed is the
// latency from the actual send, fromScheduled from the scheduled send.
func (s *runState) finish(seq int64, n int, uncompressed, wire int, o exportOutcome, elapsed, fromScheduled time.Duration, end time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	spans := int64(n)
	c := &s.counters
	s.inFlightSpans -= spans
	s.inFlightRequests--
	s.statuses[o.statusLabel]++
	s.requestLatency.add(elapsed)
	s.fromScheduled.add(fromScheduled)
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
		s.candidates.add(seq, end.Sub(s.start))
		if s.acks != nil {
			s.acks.add(end.Sub(s.start))
		}
	}
	// Lag is measured only for fully acknowledged requests: a rejected span is
	// expected never to appear, and a partial rejection does not say which.
	s.sentinelFinished(seq, o.class == outcomeAcknowledged)
	if err := s.ledger.record(seq, o); err != nil && s.ledgerErr == nil {
		s.ledgerErr = err
	}
}

// candidate is the newest retained acknowledgement at least minAge old.
// minAge is the run's configured DirectProbes.MinAge (the retention horizon).
func (s *runState) candidate(minAge time.Duration) candidate {
	s.mu.Lock()
	defer s.mu.Unlock()
	// Sample age after acquiring the lock: a stalled probe must not compare
	// newly retained acknowledgements with a clock captured before the stall.
	return s.candidates.selectAt(minAge, time.Since(s.start))
}

func (s *runState) candidateAt(minAge time.Duration, now time.Time) candidate {
	s.mu.Lock()
	defer s.mu.Unlock()
	// Sub uses Go's monotonic clock, as does finish's acknowledgement offset.
	return s.candidates.selectAt(minAge, now.Sub(s.start))
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
