package ingestbench

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"mime"
	"net/http"
	"sort"
	"sync"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

type Options struct {
	OTLPHeaders       http.Header
	ClickHouseHeaders http.Header
	ResourceWriter    io.Writer
}

type runState struct {
	mu             sync.Mutex
	counters       Counters
	statuses       map[int]int64
	requestLatency histogram
	samples        map[int64]lagSample
	lagErrors      int64
}

// Run sends real OTLP/HTTP JSON only with Execute=true. Verification always uses
// a separate read connection. It refuses reuse of a run ID before sending load.
func Run(ctx context.Context, c Config, options Options) (Result, error) {
	r := newResult(c)
	if err := c.Validate(); err != nil {
		return r, err
	}
	// Generate one bounded preview during dry-run, but make NO network requests.
	preview, err := c.batch(0, min(c.BatchSize, int(c.PlannedSpans())), time.Unix(1700000000, 0))
	if err != nil || len(preview) > 16<<20 {
		return r, fmt.Errorf("batch exceeds the collector's 16MiB request cap; reduce batch-size")
	}
	if !c.Execute {
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
	client := c.client()
	defer client.CloseIdleConnections()
	ch := clickHouse{cfg: c, client: client, headers: options.ClickHouseHeaders}
	r.Status = "preflight-failed"
	preexisting, err := ch.count(ctx)
	r.Verification.PreexistingRunRows = preexisting
	if err != nil {
		return r, fmt.Errorf("verification preflight: %w", err)
	}
	if preexisting != 0 {
		return r, fmt.Errorf("run-id already has %d rows; choose a new run-id (no data was sent)", preexisting)
	}
	r.Resources = resourceSnapshots(ctx, ch)
	r.Started = time.Now().UTC()
	start := time.Now()
	loadCtx, stopLoad := context.WithTimeout(ctx, c.Duration)
	defer stopLoad()
	monitorCtx, stopMonitor := context.WithCancel(ctx)
	s := &runState{statuses: map[int]int64{}, samples: map[int64]lagSample{}}
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
	sem := make(chan struct{}, c.Concurrency)
	var sendWG sync.WaitGroup
	var generated, dispatched, wireBytes int64
	stride := max(int64(1), (c.PlannedSpans()+int64(c.BatchSize)*4096-1)/(int64(c.BatchSize)*4096))
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
		n := min(c.BatchSize, int(c.PlannedSpans()-generated))
		seq := generated
		body, err := c.batch(seq, n, time.Now())
		generated += int64(n)
		s.mu.Lock()
		s.counters.Generated = generated
		s.mu.Unlock()
		if err != nil || len(body) > 16<<20 {
			<-sem
			r.StopReason = "invalid-or-oversized-batch"
			break
		}
		if int64(len(body)) > c.MaxBytes-wireBytes {
			<-sem
			r.StopReason = "byte-budget"
			break
		}
		if loadCtx.Err() != nil {
			<-sem
			r.StopReason = "duration-or-cancel"
			break
		}
		wireBytes += int64(len(body))
		dispatched += int64(n)
		sample := (seq/int64(c.BatchSize))%stride == 0
		sendWG.Add(1)
		go func(seq int64, n int, body []byte, sample bool) {
			defer sendWG.Done()
			defer func() { <-sem }()
			s.send(ctx, client, c.Endpoint, options.OTLPHeaders, seq, n, body, sample)
		}(seq, n, body, sample)
	}
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
	r.Counters = s.counters
	r.Counters.Generated = generated
	r.Counters.Unacknowledged = r.Counters.Attempted - r.Counters.Acknowledged - r.Counters.PartialRejected
	r.HTTPStatuses = s.statuses
	r.RequestLatency = s.requestLatency.summary()
	r.Lag = s.lagResult()
	r.Resources = append(r.Resources, resourceSnapshots(ctx, ch)...)
	verifyCtx, cancelVerify := context.WithTimeout(ctx, 10*time.Minute)
	defer cancelVerify()
	r.Verification = ch.verify(verifyCtx, r.Counters.Attempted)
	r.Status = "incomplete"
	if r.Verification.Complete {
		r.Status = "measured-with-errors"
		if r.Verification.Missing == 0 && r.Verification.DuplicateRows == 0 && r.Verification.UnexpectedRows == 0 && r.Counters.Acknowledged == r.Counters.Attempted && r.Counters.Attempted == r.PlannedSpans && r.StopReason == "planned-span-count" {
			r.Status = "measured-profile-complete"
		}
	}
	for _, read := range r.Reads {
		if read.Status == "errors" {
			r.Status = "measured-with-errors"
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

func (s *runState) send(ctx context.Context, client *http.Client, endpoint string, headers http.Header, seq int64, n int, body []byte, sample bool) {
	start := time.Now()
	s.mu.Lock()
	s.counters.Attempted += int64(n)
	s.counters.Requests++
	s.counters.Bytes += int64(len(body))
	if sample {
		s.samples[seq] = lagSample{Seq: seq, Sent: start}
	}
	s.mu.Unlock()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header = headers.Clone()
	if req.Header == nil {
		req.Header = http.Header{}
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	statusCode := 0
	var acknowledged, rejected int64
	partial, failed := false, true
	if err == nil {
		statusCode = resp.StatusCode
		b, readErr := readBounded(resp.Body, 1<<20)
		resp.Body.Close()
		mediaType, _, _ := mime.ParseMediaType(resp.Header.Get("Content-Type"))
		if readErr == nil && statusCode == http.StatusOK && mediaType == "application/json" {
			otlp := ptraceotlp.NewExportResponse()
			if otlp.UnmarshalJSON(b) == nil {
				rejected = otlp.PartialSuccess().RejectedSpans()
				if rejected >= 0 && rejected <= int64(n) {
					acknowledged = int64(n) - rejected
					partial = rejected > 0 || otlp.PartialSuccess().ErrorMessage() != ""
					failed = false
				} else {
					rejected = 0
				}
			}
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.statuses[statusCode]++
	s.requestLatency.add(time.Since(start))
	if statusCode == http.StatusOK {
		s.counters.HTTP200 += int64(n)
	}
	s.counters.Accepted += acknowledged
	s.counters.Acknowledged += acknowledged
	s.counters.PartialRejected += rejected
	if partial {
		s.counters.PartialResponses++
	}
	if failed {
		s.counters.RequestErrors++
	}
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
				sample := s.samples[seq]
				sample.Seen = at
				s.samples[seq] = sample
			}
			s.mu.Unlock()
		}
	}
}

func (s *runState) lagResult() LagResult {
	r := LagResult{Method: "sampled_send_to_first_query_visibility_upper_bound", Sampled: len(s.samples), QueryErrors: s.lagErrors}
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
