package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
)

func testConfig() Config {
	c := DefaultConfig()
	c.RunID = "0123456789abcdef"
	c.ProjectID = "11111111-1111-4111-8111-111111111111"
	c.ProjectName = "isolated-benchmark"
	c.Revision = "in-process-test-build"
	return c
}

func TestSafetyValidation(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*Config)
	}{
		{"remote", func(c *Config) { c.Endpoint = "https://example.invalid/v1/traces" }},
		{"remote-no-confirm", func(c *Config) {
			c.Execute = true
			c.Endpoint = "https://example.invalid/v1/traces"
			c.AllowHosts = []string{"example.invalid"}
		}},
		{"wildcard", func(c *Config) { c.Endpoint = "https://example.invalid/v1/traces"; c.AllowHosts = []string{"*"} }},
		{"userinfo", func(c *Config) { c.Endpoint = "http://user:secret@127.0.0.1:4318" }},
		{"token-query", func(c *Config) { c.Endpoint += "?api_key=secret" }},
		{"remote-ch", func(c *Config) { c.ClickHouseURL = "http://example.invalid:8123" }},
		{"remote-health", func(c *Config) { c.CollectorHealthURL = "http://example.invalid:9464/healthz" }},
		{"remote-node", func(c *Config) { c.Nodes = []Node{{Name: "n", URL: "http://example.invalid:8123"}} }},
		{"identifier", func(c *Config) { c.Table = "spans; DROP TABLE spans" }},
		{"zero-rate", func(c *Config) { c.Rate = 0 }},
		{"negative-duration", func(c *Config) { c.Duration = -time.Second }},
		{"max-duration", func(c *Config) { c.Duration = 25 * time.Hour; c.MaxDuration = c.Duration }},
		{"high-load-confirm", func(c *Config) { c.Execute = true; c.Rate = 15000 }},
		{"budget", func(c *Config) { c.MaxBytes = 0 }},
		{"oversized-batch", func(c *Config) { c.BatchSize = 10000; c.Payload = "large" }},
		{"zero-spans-per-trace", func(c *Config) { c.SpansPerTrace = 0 }},
		{"run-injection", func(c *Config) { c.RunID = "bad' OR 1=1 --" }},
		{"zero-run", func(c *Config) { c.RunID = "0000000000000000" }},
		{"encoding", func(c *Config) { c.Encoding = "thrift" }},
		{"grpc-with-path", func(c *Config) { c.Encoding = EncodingGRPC }},
		{"error-rate", func(c *Config) { c.ErrorRate = 1.5 }},
		{"error-rate-nan", func(c *Config) { c.ErrorRate = math.NaN() }},
		{"cohort", func(c *Config) { c.CohortVariantFraction = -0.1 }},
		{"tree-depth", func(c *Config) { c.TreeDepth = 0 }},
		{"fan-out", func(c *Config) { c.FanOut = 0 }},
		{"bytes-per-token", func(c *Config) { c.BytesPerToken = 0 }},
		{"pool", func(c *Config) { c.TextPoolBlocks = 0 }},
		{"verify-memory", func(c *Config) { c.VerifyMaxMemory = 1 }},
		{"verify-retries", func(c *Config) { c.VerifyQueryRetries = 11 }},
		{"verify-timeout", func(c *Config) { c.VerifyTimeout = 73 * time.Hour }},
		{"durable-lag-timeout", func(c *Config) { c.DurableLagTimeout = 73 * time.Hour }},
		{"direct-interval", func(c *Config) { c.DirectProbes.Interval = time.Millisecond }},
		{"ledger", func(c *Config) {
			c.Rate, c.Duration, c.MaxDuration, c.BatchSize = 1000000, 24*time.Hour, 24*time.Hour, 1
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := testConfig()
			tc.mutate(&c)
			if c.Validate() == nil {
				t.Fatal("unsafe configuration accepted")
			}
		})
	}
	c := testConfig()
	c.Execute, c.Rate = true, 15000
	c.Endpoint = "https://example.invalid/v1/traces"
	c.AllowHosts, c.ConfirmLoad, c.ConfirmRemote = []string{"example.invalid"}, c.RunID, c.RunID
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	c = testConfig()
	c.Encoding, c.Endpoint = EncodingGRPC, "http://127.0.0.1:4317"
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
}

func TestDryRunMakesNoNetworkRequests(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer srv.Close()
	c := testConfig()
	c.Endpoint, c.ClickHouseURL, c.CollectorHealthURL = srv.URL, srv.URL, srv.URL
	r, err := Run(context.Background(), c, Options{CommandLine: []string{"otlpbench", "--rate", "100"}})
	if err != nil || calls.Load() != 0 || r.Status != "dry-run" || r.PlannedSpans != 500 || r.Counters.Attempted != 0 {
		t.Fatalf("dry run: %+v %v calls=%d", r.Counters, err, calls.Load())
	}
	if len(r.Provenance.CommandLine) != 3 || r.Provenance.Host.LogicalCPUs == 0 || r.Provenance.Generator.GoVersion == "" {
		t.Fatalf("provenance missing from the plan: %+v", r.Provenance)
	}
}

func spansOf(t *testing.T, traces ptrace.Traces) ptrace.SpanSlice {
	t.Helper()
	return traces.ResourceSpans().At(0).ScopeSpans().At(0).Spans()
}

func TestSpanIdentityTreeAndBatchDeterminism(t *testing.T) {
	c := testConfig()
	now := time.Unix(1700000000, 0)
	whole, plans := c.batch(0, 80, now)
	spans := spansOf(t, whole)
	seen := map[string]bool{}
	for i := 0; i < spans.Len(); i++ {
		sp := spans.At(i)
		if seen[sp.SpanID().String()] {
			t.Fatal("duplicate span ID")
		}
		seen[sp.SpanID().String()] = true
		root := i - i%8
		if sp.TraceID() != spans.At(root).TraceID() {
			t.Fatal("wrong trace grouping")
		}
		p := plans[i]
		if i%8 == 0 {
			if !sp.ParentSpanID().IsEmpty() || p.depth != 0 || p.kind != "AGENT" {
				t.Fatal("root has a parent or is not an agent span")
			}
		} else {
			// The parent is an earlier span of the same trace and the depth
			// never exceeds the configured tree depth.
			if sp.ParentSpanID() != spans.At(root+int(p.parent)).SpanID() || p.parent >= p.position || p.depth > c.TreeDepth || p.depth != plans[root+int(p.parent)].depth+1 {
				t.Fatalf("span %d: parent %d depth %d", i, p.parent, p.depth)
			}
		}
		part, _ := c.batch(int64(i), 1, now)
		single := spansOf(t, part).At(0)
		if sp.TraceID() != single.TraceID() || sp.SpanID() != single.SpanID() || sp.ParentSpanID() != single.ParentSpanID() {
			t.Fatal("IDs depend on batch boundary")
		}
		in, _ := sp.Attributes().Get("input.value")
		one, _ := single.Attributes().Get("input.value")
		if in.Str() != one.Str() {
			t.Fatal("text depends on batch boundary")
		}
	}
	other := c
	other.RunID = "1123456789abcdef"
	a, _ := c.ids(0)
	b, _ := other.ids(0)
	if a == b {
		t.Fatal("run collision")
	}
	session0, _ := spans.At(0).Attributes().Get("session.id")
	session31, _ := spans.At(31).Attributes().Get("session.id")
	session32, _ := spans.At(32).Attributes().Get("session.id")
	if session0.Str() != session31.Str() || session0.Str() == session32.Str() {
		t.Fatal("wrong session grouping")
	}
	// Depth cap: a chain (fan-out 1) deeper than the cap reattaches spans.
	chain := testConfig()
	chain.FanOut, chain.TreeDepth, chain.SpansPerTrace = 1, 3, 10
	for pos := int64(1); pos < 10; pos++ {
		if p := chain.plan(pos); p.depth > 3 || p.depth < 1 {
			t.Fatalf("depth cap violated at %d: %+v", pos, p)
		}
	}
}

// B7: the same seed gives the same bytes; another seed gives other bytes;
// span texts are distinct and drawn from a size distribution, not one string.
func TestSeededWorkloadIsReproducibleAndVaried(t *testing.T) {
	c := testConfig()
	c.Encoding = EncodingProtobuf
	now := time.Unix(1700000000, 0)
	a, _, err := c.buildRequest(0, 200, now)
	if err != nil {
		t.Fatal(err)
	}
	b, _, _ := c.buildRequest(0, 200, now)
	if !bytes.Equal(a.body, b.body) {
		t.Fatal("same seed produced different bytes")
	}
	other := c
	other.Seed++
	d, _, _ := other.buildRequest(0, 200, now)
	if bytes.Equal(a.body, d.body) {
		t.Fatal("seed has no effect")
	}
	traces, plans := c.batch(0, 2000, now)
	spans := spansOf(t, traces)
	texts := map[string]bool{}
	sizes := map[int]bool{}
	var errorsSeen, variant int
	acc := newWorkloadAccumulator()
	acc.addSpans(c, plans)
	for i := 0; i < spans.Len(); i++ {
		in, _ := spans.At(i).Attributes().Get("input.value")
		texts[in.Str()] = true
		sizes[len(in.Str())] = true
		if len(in.Str()) != plans[i].inputBytes || int64(len(in.Str())) > maxSpanTextBytes(c.Payload) {
			t.Fatalf("span %d text %d bytes, planned %d", i, len(in.Str()), plans[i].inputBytes)
		}
		if spans.At(i).Status().Code() == ptrace.StatusCodeError {
			errorsSeen++
		}
		if plans[i].cohort == "variant" {
			variant++
		}
	}
	if len(texts) < 1990 || len(sizes) < 50 {
		t.Fatalf("payload text is not varied: %d distinct texts, %d distinct sizes", len(texts), len(sizes))
	}
	if errorsSeen == 0 || errorsSeen > 60 {
		t.Fatalf("error rate 1%% realised as %d of 2000", errorsSeen)
	}
	if variant < 500 || variant > 1500 {
		t.Fatalf("cohort split 50%% realised as %d of 2000", variant)
	}
	stats := acc.stats(c)
	if stats.Spans != 2000 || stats.ErrorSpans != int64(errorsSeen) || stats.SpansByKind["AGENT"] != 250 || stats.InputBytes.P99 <= stats.InputBytes.P50 || stats.Traces != 250 || stats.Sessions != 63 {
		t.Fatalf("realised distribution wrong: %+v", stats)
	}
	if stats.SpansByPayloadClass["small"] < stats.SpansByPayloadClass["medium"] || stats.SpansByPayloadClass["large"] == 0 {
		t.Fatalf("mixed classes: %+v", stats.SpansByPayloadClass)
	}
	// Changing the error rate changes errors only, not sizes or structure.
	noErrors := c
	noErrors.ErrorRate = 0
	for seq := int64(0); seq < 200; seq++ {
		p, q := c.plan(seq), noErrors.plan(seq)
		if q.isError || p.inputBytes != q.inputBytes || p.kind != q.kind {
			t.Fatalf("error rate leaks into other draws at %d", seq)
		}
	}
}

func TestValueHistogramBoundsAreUpperEdges(t *testing.T) {
	h := &valueHistogram{}
	for v := int64(1); v <= 1000; v++ {
		h.add(v)
	}
	d := h.summary()
	if d.Count != 1000 || d.Min != 1 || d.Max != 1000 || d.P50 < 500 || d.P50 > 532 || d.P99 < 990 || d.P99 > 1000 || d.Mean != 500.5 {
		t.Fatalf("distribution %+v", d)
	}
	for _, v := range []int64{0, 15, 16, 17, 1 << 20, 1<<40 + 12345} {
		if up := histogramUpper(histogramBucket(v)); up < v || float64(up-v) > float64(v)/16+1 {
			t.Fatalf("bucket of %d has upper edge %d", v, up)
		}
	}
}

func classifyFixture(t *testing.T, status int, header http.Header, body []byte, contentType string, n int) exportOutcome {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for k, v := range header {
			w.Header()[k] = v
		}
		w.Header().Set("Content-Type", contentType)
		w.WriteHeader(status)
		_, _ = w.Write(body)
	}))
	defer srv.Close()
	c := testConfig()
	c.Endpoint = srv.URL
	if strings.Contains(contentType, "json") {
		c.Encoding = EncodingJSON
	}
	e := &httpExporter{cfg: c, client: c.client()}
	o, _ := e.export(context.Background(), encodedBatch{body: []byte("{}"), uncompressed: 2}, n)
	return o
}

// B2: every response maps to exactly one outcome class.
func TestHTTPResponsesClassifyIntoOneOutcome(t *testing.T) {
	partial := ptraceotlp.NewExportResponse()
	partial.PartialSuccess().SetRejectedSpans(3)
	partial.PartialSuccess().SetErrorMessage("queue full")
	partialProto, _ := partial.MarshalProto()
	for _, tc := range []struct {
		name        string
		status      int
		header      http.Header
		body        string
		contentType string
		class       outcomeClass
		rejected    int64
		hint        bool
	}{
		{"success", 200, nil, `{}`, "application/json", outcomeAcknowledged, 0, false},
		{"partial", 200, nil, `{"partialSuccess":{"rejectedSpans":"3","errorMessage":"queue full"}}`, "application/json", outcomePartial, 3, false},
		{"partial-protobuf", 200, nil, string(partialProto), "application/x-protobuf", outcomePartial, 3, false},
		{"all-rejected", 200, nil, `{"partialSuccess":{"rejectedSpans":10}}`, "application/json", outcomePartial, 10, false},
		{"warning", 200, nil, `{"partialSuccess":{"errorMessage":"warning"}}`, "application/json", outcomeAcknowledged, 0, false},
		{"invalid-rejected", 200, nil, `{"partialSuccess":{"rejectedSpans":"11"}}`, "application/json", outcomeAmbiguous, 0, false},
		{"negative-rejected", 200, nil, `{"partialSuccess":{"rejectedSpans":"-1"}}`, "application/json", outcomeAmbiguous, 0, false},
		{"malformed", 200, nil, `<html>login</html>`, "application/json", outcomeAmbiguous, 0, false},
		{"wrong-media-type", 200, nil, `{}`, "text/html", outcomeAmbiguous, 0, false},
		{"accepted-202", 202, nil, `{}`, "application/json", outcomeAmbiguous, 0, false},
		{"throttle-with-retry-after", 429, http.Header{"Retry-After": {"2"}}, `{}`, "application/json", outcomeRejectedRetryable, 0, true},
		{"busy-with-retry-after-date", 503, http.Header{"Retry-After": {time.Now().Add(time.Minute).UTC().Format(http.TimeFormat)}}, `{}`, "application/json", outcomeRejectedRetryable, 0, true},
		{"busy-without-hint", 503, nil, `{}`, "application/json", outcomeRejectedRetryable, 0, false},
		{"oversize", 413, nil, `{}`, "application/json", outcomeRejectedNonRetryable, 0, false},
		{"bad-request", 400, nil, `{}`, "application/json", outcomeRejectedNonRetryable, 0, false},
		{"unauthorized", 401, nil, `{}`, "application/json", outcomeRejectedNonRetryable, 0, false},
		{"internal", 500, nil, `{}`, "application/json", outcomeAmbiguous, 0, false},
		{"bad-gateway", 502, nil, `{}`, "application/json", outcomeAmbiguous, 0, false},
		{"gateway-timeout", 504, nil, `{}`, "application/json", outcomeAmbiguous, 0, false},
		{"redirect", 307, http.Header{"Location": {"http://127.0.0.1:1/"}}, ``, "application/json", outcomeAmbiguous, 0, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			o := classifyFixture(t, tc.status, tc.header, []byte(tc.body), tc.contentType, 10)
			if o.class != tc.class || o.rejected != tc.rejected || o.retryHint != tc.hint {
				t.Fatalf("got class %s rejected %d hint %t", o.class, o.rejected, o.retryHint)
			}
			s := newRunState(testConfig(), time.Now(), false)
			s.counters.Attempted, s.counters.Requests = 10, 1
			s.finish(0, 10, 2, 2, o, time.Millisecond, time.Millisecond, time.Now())
			if err := s.counters.CheckIdentity(); err != nil {
				t.Fatalf("%v: %+v", err, s.counters)
			}
		})
	}
	// A transport failure is ambiguous: the request may have been processed.
	c := testConfig()
	c.Endpoint = "http://127.0.0.1:1/v1/traces"
	e := &httpExporter{cfg: c, client: c.client()}
	if o, _ := e.export(context.Background(), encodedBatch{body: []byte("{}")}, 1); o.class != outcomeAmbiguous || o.statusLabel != "transport-error" {
		t.Fatalf("transport error: %+v", o)
	}
}

func TestGRPCStatusesClassifyIntoOneOutcome(t *testing.T) {
	retry, _ := status.New(codes.ResourceExhausted, "busy").WithDetails(&errdetails.RetryInfo{RetryDelay: durationpb.New(time.Second)})
	for _, tc := range []struct {
		name  string
		err   error
		class outcomeClass
		hint  bool
	}{
		{"ok", nil, outcomeAcknowledged, false},
		{"busy-with-retry-info", retry.Err(), outcomeRejectedRetryable, true},
		{"quota-without-retry-info", status.Error(codes.ResourceExhausted, "quota"), outcomeRejectedNonRetryable, false},
		{"oversize", status.Error(codes.InvalidArgument, "too big"), outcomeRejectedNonRetryable, false},
		{"unauthenticated", status.Error(codes.Unauthenticated, "no key"), outcomeRejectedNonRetryable, false},
		{"unavailable", status.Error(codes.Unavailable, "conn refused"), outcomeAmbiguous, false},
		{"deadline", status.Error(codes.DeadlineExceeded, "slow"), outcomeAmbiguous, false},
		{"internal", status.Error(codes.Internal, "boom"), outcomeAmbiguous, false},
		{"not-a-status", errors.New("eof"), outcomeAmbiguous, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			o := classifyGRPC(tc.err, 0, false, 10)
			if o.class != tc.class || o.retryHint != tc.hint {
				t.Fatalf("got %s hint %t", o.class, o.retryHint)
			}
			if tc.hint && o.retryAfter != time.Second {
				t.Fatalf("retry delay %s", o.retryAfter)
			}
		})
	}
	if o := classifyGRPC(nil, 4, true, 10); o.class != outcomePartial || o.rejected != 4 {
		t.Fatalf("partial: %+v", o)
	}
	if o := classifyGRPC(nil, 11, false, 10); o.class != outcomeAmbiguous {
		t.Fatalf("impossible partial count accepted: %+v", o)
	}
}

// B2: the counters keep the accounting identity over a mix of outcomes and
// report whole-request rejections separately from partial rejections.
func TestCountersKeepTheAccountingIdentity(t *testing.T) {
	s := newRunState(testConfig(), time.Now(), false)
	outcomes := []exportOutcome{
		{class: outcomeAcknowledged, statusLabel: "http:200", successStatus: true},
		{class: outcomePartial, rejected: 30, statusLabel: "http:200", successStatus: true},
		{class: outcomeRejectedRetryable, retryHint: true, retryAfter: time.Second, statusLabel: "http:503"},
		{class: outcomeRejectedRetryable, statusLabel: "http:429"},
		{class: outcomeRejectedNonRetryable, statusLabel: "http:413"},
		{class: outcomeAmbiguous, statusLabel: "transport-error"},
	}
	for i, o := range outcomes {
		seq := int64(i * 100)
		s.counters.Attempted += 100
		s.counters.Requests++
		s.finish(seq, 100, 1000, 900, o, time.Millisecond, time.Millisecond, time.Now())
	}
	c := s.counters
	if err := c.CheckIdentity(); err != nil {
		t.Fatal(err)
	}
	if c.Acknowledged != 170 || c.PartialRejected != 30 || c.WholeRequestRejected != 300 || c.RejectedRetryable != 200 || c.RejectedNonRetryable != 100 || c.Ambiguous != 100 || c.Unacknowledged != 400 {
		t.Fatalf("counters %+v", c)
	}
	if c.RequestOutcomes != (RequestOutcomes{Acknowledged: 1, PartialRejection: 1, RejectedRetryable: 2, RejectedWithRetryHint: 1, RejectedNonRetryable: 1, Ambiguous: 1}) || c.RequestErrors != 4 || c.SuccessStatus != 200 {
		t.Fatalf("request outcomes %+v errors %d", c.RequestOutcomes, c.RequestErrors)
	}
	broken := c
	broken.Ambiguous--
	if broken.CheckIdentity() == nil {
		t.Fatal("broken identity accepted")
	}
	snap := s.ledger.snapshot()
	back, err := ledgerFromSnapshot(snap)
	if err != nil || back.class(1) != outcomePartial || back.partial[1] != 30 || back.class(5) != outcomeAmbiguous {
		t.Fatalf("ledger round trip: %v %+v", err, snap)
	}
	if s.ledger.record(0, exportOutcome{class: outcomeAcknowledged}) == nil {
		t.Fatal("a request recorded twice")
	}
}

// A build interrupted by the end of the load window is the end of the load, not
// an invalid batch; a real build failure or an out-of-order batch still is.
func TestBatchStopNamesTheRealReason(t *testing.T) {
	open := context.Background()
	closed, cancel := context.WithCancel(context.Background())
	cancel()
	for _, tc := range []struct {
		name      string
		ctx       context.Context
		b         builtBatch
		generated int64
		want      string
	}{
		{"in-order", open, builtBatch{seq: 8, n: 4}, 8, ""},
		{"cancelled-build", closed, builtBatch{seq: 8, n: 4, err: context.Canceled}, 8, "duration-or-cancel"},
		{"failed-build", open, builtBatch{seq: 8, n: 4, err: errors.New("marshal")}, 8, "invalid-or-oversized-batch"},
		{"out-of-order", open, builtBatch{seq: 12, n: 4}, 8, "invalid-or-oversized-batch"},
	} {
		if got := batchStop(tc.ctx, tc.b, tc.generated); got != tc.want {
			t.Fatalf("%s: got %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestClientDoesNotFollowRedirectOrProxy(t *testing.T) {
	var contacted atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { contacted.Add(1) }))
	defer target.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, 307) }))
	defer redirect.Close()
	t.Setenv("HTTP_PROXY", target.URL)
	client := testConfig().client()
	defer client.CloseIdleConnections()
	resp, err := client.Get(redirect.URL)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 307 || contacted.Load() != 0 {
		t.Fatal("followed redirect or proxy")
	}
}

func TestSizingUsesPhysicalBytesAndReplicaUnits(t *testing.T) {
	input := SizingInput{SpansPerDay: 86400, RetentionDays: 10, PhysicalBytesPerSpanPerReplica: 1000, Replicas: 2, DiskBytesPerNode: 1000000000, UsableFraction: .8, MeasuredSpansPerSecondPerShard: 10, PeakSpansPerSecond: 25}
	result, err := Size(input)
	if err != nil || result.RetainedPhysicalBytes != 1728000000 || result.StorageNodes != 3 || result.ThroughputShards != 3 || result.MinimumNodes != 6 {
		t.Fatalf("dimensionally wrong: %+v %v", result, err)
	}
	input.PhysicalBytesPerSpanPerReplica = 0
	if _, err := Size(input); err == nil {
		t.Fatal("fabricated missing measurement")
	}
	input.PhysicalBytesPerSpanPerReplica = math.Inf(1)
	if _, err := Size(input); err == nil {
		t.Fatal("accepted infinity")
	}
}

func TestReportDoesNotClaimUnverifiedLossOrAppLatency(t *testing.T) {
	r := newResult(testConfig())
	var out bytes.Buffer
	if err := Report(&out, r); err != nil {
		t.Fatal(err)
	}
	for _, phrase := range []string{"No zero-loss conclusion", "not-configured", "not traces", "Neither is certified", "Wire path: protobuf", "acknowledged-but-missing (DATA LOSS)", "Whole-request rejected", "server memory not reported"} {
		if !strings.Contains(out.String(), phrase) {
			t.Fatalf("missing honesty qualifier %q", phrase)
		}
	}
	r.SchemaVersion = "fi.otlpbench.v3"
	if Report(&out, r) == nil {
		t.Fatal("accepted an older schema whose counters mean something else")
	}
}

func TestHistogramBoundaries(t *testing.T) {
	h := &histogram{}
	if h.summary().Count != 0 {
		t.Fatal("empty histogram")
	}
	for i := 1; i <= 100; i++ {
		h.add(time.Duration(i) * time.Millisecond)
	}
	p := h.summary()
	within := func(got, want float64) bool { return got >= want && got <= want*(1+1.0/128) }
	if !within(p.P50, 50) || !within(p.P95, 95) || !within(p.P99, 99) || p.Max != 100 {
		t.Fatalf("quantiles: %+v", p)
	}
	// An upper quantile is never below the true value and never above the max;
	// the old 1 s buckets above 10 s reported 10,001 ms as 11,000 ms.
	h = &histogram{}
	h.add(10001 * time.Millisecond)
	if p := h.summary(); p.P99 < 10001 || p.P99 > p.Max || p.Max != 10001 {
		t.Fatalf("an upper quantile must be an upper bound no larger than the max: %+v", p)
	}
	h = &histogram{}
	for _, ms := range []int{10001, 10002, 10500} {
		h.add(time.Duration(ms) * time.Millisecond)
	}
	if p := h.summary(); !within(p.P50, 10002) || p.P99 != 10500 || p.Max != 10500 {
		t.Fatalf("upper quantiles: %+v", p)
	}
	// Buckets use whole microseconds, but capping at a truncated maximum
	// must not turn an upper bound into a value below the actual measurement.
	fractional := 1000*time.Microsecond + 999*time.Nanosecond
	h = &histogram{}
	h.add(fractional)
	var fractionalSparse sparseHistogram
	fractionalSparse.addN(fractional, 1)
	for _, p := range []Percentiles{h.summary(), fractionalSparse.summary(false)} {
		if p.P50 < 1.000999 || p.Max != 1.000999 {
			t.Fatalf("sub-microsecond maximum was truncated: %+v", p)
		}
	}
	// A lower-bound series reads lower edges: with lower bounds 10,200 and
	// 10,900 ms the median lower bound may not exceed 10,200 ms (the old rule
	// reported 10,900 ms, above the true lag it was meant to bound from below).
	lower := &histogram{lowerBound: true}
	lower.add(10200 * time.Millisecond)
	lower.add(10900 * time.Millisecond)
	if p := lower.summary(); p.P50 > 10200 || p.P50 < 10200*(1-1.0/128) || p.P99 > 10900 || p.Max != 10900 {
		t.Fatalf("lower-bound quantiles must not overstate: %+v", p)
	}
	var sparse sparseHistogram
	sparse.addN(10200*time.Millisecond, 1)
	sparse.addN(10900*time.Millisecond, 1)
	if got, want := sparse.summary(true), lower.summary(); got != want {
		t.Fatalf("sparse lower-bound quantiles %+v differ from %+v", got, want)
	}
}

// BM-T09: quantisation error at most 1% from 1 ms to 24 h. Every value lies in
// its bucket, consecutive buckets tile the axis, and both edges are within 1%
// (in fact 1/128) of the value.
func TestHistogramQuantisationIsWithinOnePercentFrom1msTo24h(t *testing.T) {
	values := []int64{0, 1, 255, 256, 257, 511, 512, 999, 1000, 1001, 1023, 1024, 65535, 65536, 10_000_000, 10_001_000, 86_400_000_000, 1<<37 - 1}
	for us := int64(1000); us <= 86_400_000_000; us += max(1, us/97) {
		values = append(values, us)
	}
	worst := 0.0
	for _, us := range values {
		b := latencyBucketMicros(us)
		lo, hi := bucketBoundsMicros(b)
		if us < lo || us >= hi {
			t.Fatalf("%d us not in its bucket %d [%d, %d)", us, b, lo, hi)
		}
		if us >= 1000 {
			if rel := math.Max(float64(hi-us)/float64(us), float64(us-lo)/float64(us)); rel > worst {
				worst = rel
			}
		}
	}
	if worst > 0.01 {
		t.Fatalf("worst relative quantisation error from 1 ms to 24 h is %.4f, above 1%%", worst)
	}
	for b := 0; b < histogramBuckets-1; b++ {
		_, hi := bucketBoundsMicros(b)
		if lo, _ := bucketBoundsMicros(b + 1); lo != hi || latencyBucketMicros(hi-1) != b || latencyBucketMicros(hi) != b+1 {
			t.Fatalf("buckets %d and %d do not tile: hi %d next lo %d", b, b+1, hi, lo)
		}
	}
	if latencyBucketMicros(1<<40) != histogramBuckets-1 || latencyBucketMicros(-5) != 0 || strconv.Itoa(histogramBuckets-1) != histogramLastBucket {
		t.Fatal("out-of-range values are not clamped, or the SQL bucket bound is stale")
	}
	if h := newResult(testConfig()).LatencyHistogram; h.MaxRelativeWidth != 1.0/128 || h.ExactBelowMS != 0.256 || len(h.LowerBoundSeries) != 4 {
		t.Fatalf("the result does not state its quantisation: %+v", h)
	}
	t.Logf("worst relative quantisation error from 1 ms to 24 h: %.4f%% over %d values", worst*100, len(values))
}

func TestCredentialHeadersAreNotPersisted(t *testing.T) {
	t.Setenv("BENCH_TEST_HEADERS", `{"X-API-Key":"do-not-store"}`)
	h, err := HeadersFromEnv("BENCH_TEST_HEADERS")
	if err != nil || h.Get("X-API-Key") != "do-not-store" {
		t.Fatal(err)
	}
	b, _ := json.Marshal(newResult(testConfig()))
	if bytes.Contains(b, []byte("do-not-store")) {
		t.Fatal("secret in result")
	}
	t.Setenv("BENCH_TEST_HEADERS", `{"Host":"attacker.invalid"}`)
	if _, err := HeadersFromEnv("BENCH_TEST_HEADERS"); err == nil {
		t.Fatal("Host override accepted")
	}
}

// B10: the recorded command line keeps flags and environment variable NAMES
// but never credential values.
func TestCommandLineIsRedacted(t *testing.T) {
	got := RedactArgs([]string{"otlpbench", "--otlp-headers-env", "BENCH_OTLP_HEADERS", "--api-key", "hunter2", "--password=hunter3",
		"--endpoint", "http://user:hunter4@127.0.0.1:4318/v1/traces?api_key=hunter5&x=1", "TOKEN=hunter6", "--rate", "100"})
	joined := strings.Join(got, " ")
	for _, secret := range []string{"hunter2", "hunter3", "hunter4", "hunter5", "hunter6"} {
		if strings.Contains(joined, secret) {
			t.Fatalf("secret %s recorded: %s", secret, joined)
		}
	}
	for _, kept := range []string{"BENCH_OTLP_HEADERS", "--rate 100", "127.0.0.1:4318", "x=1"} {
		if !strings.Contains(joined, kept) {
			t.Fatalf("%q lost: %s", kept, joined)
		}
	}
}

// B10: the system under test is the collector's reported build when known,
// never the generator's revision, and a conflicting declaration is loud.
func TestSystemUnderTestRevision(t *testing.T) {
	sut, warnings := resolveSUT("abc1234", CollectorInfo{Build: map[string]any{"vcs_revision": "abc1234def5678", "vcs_modified": "false"}}, "")
	if sut.Revision != "abc1234def5678" || sut.RevisionsAgree == nil || !*sut.RevisionsAgree || len(warnings) != 0 {
		t.Fatalf("agreeing revisions: %+v %v", sut, warnings)
	}
	sut, warnings = resolveSUT("fff0000", CollectorInfo{Build: map[string]any{"configured_revision": "abc1234def5678"}}, "")
	if sut.RevisionsAgree == nil || *sut.RevisionsAgree || len(warnings) != 1 || !strings.Contains(sut.RevisionSource, "CONFLICTS") {
		t.Fatalf("conflict not flagged: %+v %v", sut, warnings)
	}
	sut, _ = resolveSUT("", CollectorInfo{}, "")
	if sut.Revision != "" {
		t.Fatalf("revision invented: %+v", sut)
	}
}

// B5: the profiles need more than an explicitly configured 20,000-row
// budget; an unbounded default and a batch over the budget are called out.
func TestCollectorBudgetCheck(t *testing.T) {
	sustained := CheckBudget(15000, 500, 16, nil, 20000)
	if sustained.Sufficient || sustained.AvailablePendingRows != 20000 || sustained.RequiredPendingRows != 43000 || !strings.Contains(sustained.Source, "not observed") {
		t.Fatalf("sustained: %+v", sustained)
	}
	burst := CheckBudget(60000, 500, 32, &CollectorLimits{MaxPendingRows: 100000, BatchMaxRows: 5000, BatchMaxAgeMS: 5000}, 0)
	if burst.Sufficient || burst.RequiredPendingRows != 141000 || burst.Source != "collector /healthz" || len(burst.Warnings()) != 1 {
		t.Fatalf("burst: %+v", burst)
	}
	raised := CheckBudget(60000, 500, 32, &CollectorLimits{MaxPendingRows: 141000, BatchMaxRows: 5000, BatchMaxAgeMS: 5000}, 0)
	if !raised.Sufficient || len(raised.Warnings()) != 0 {
		t.Fatalf("raised budget: %+v", raised)
	}
	smoke := CheckBudget(100, 100, 2, nil, 0)
	if smoke.Sufficient || smoke.BatchFits || smoke.AvailablePendingRows != 0 || !strings.Contains(smoke.Source, "NOT observed") || !strings.Contains(strings.Join(smoke.Warnings(), " "), "byte admission is unknown") {
		t.Fatalf("smoke: %+v", smoke)
	}
	// Disabled admission must never invent a finite crash-loss bound, even
	// when exact verification is complete and no acknowledged rows were lost.
	recovery := RecoveryResult{Faults: []FaultResult{{Name: "restart"}}}
	recovery.attachVerification(testConfig(), Verification{Complete: true}, BacklogResult{}, smoke)
	if recovery.LossBound != 0 || recovery.LossWithinBound != nil || recovery.LossBoundSource != "" {
		t.Fatalf("unbounded buffer given a crash-loss bound: %+v", recovery)
	}
	oversize := CheckBudget(100, 30, 1, nil, 16)
	if oversize.BatchFits || !strings.Contains(strings.Join(oversize.Warnings(), " "), "admitted only while the collector's buffer is below the budget") {
		t.Fatalf("oversize: %+v", oversize)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"status":"ok","build":{"vcs_revision":"0123456"},"runtime":{"num_cpu":8,"gomaxprocs":4},"limits":{"max_pending_rows":64,"batch_max_rows":5000,"batch_max_age_ms":5000}}`))
	}))
	defer srv.Close()
	info := FetchCollectorHealth(context.Background(), testConfig().client(), srv.URL)
	if info.Error != "" || info.Limits == nil || info.Limits.MaxPendingRows != 64 || collectorRevision(info.Build) != "0123456" {
		t.Fatalf("health: %+v", info)
	}
	if info.Runtime["num_cpu"] != float64(8) || info.Runtime["gomaxprocs"] != float64(4) {
		t.Fatalf("collector runtime hardware not recorded: %+v", info.Runtime)
	}
}

// BenchmarkBuildRequest measures the generator's own cost per export request
// (span planning, text assembly, serialisation and compression), so a run can
// tell a generator bottleneck from a collector one.
func BenchmarkBuildRequest(b *testing.B) {
	for _, tc := range []struct {
		encoding string
		gzip     bool
	}{{EncodingProtobuf, false}, {EncodingProtobuf, true}, {EncodingJSON, true}, {EncodingGRPC, false}, {EncodingGRPC, true}} {
		b.Run(tc.encoding+map[bool]string{true: "-gzip", false: ""}[tc.gzip], func(b *testing.B) {
			c := testConfig()
			c.Encoding, c.Gzip = tc.encoding, tc.gzip
			c.BatchSize = 500
			now := time.Unix(1700000000, 0)
			var spans int64
			b.ReportAllocs()
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				if _, _, err := c.buildRequest(int64(i)*500, 500, now); err != nil {
					b.Fatal(err)
				}
				spans += 500
			}
			b.ReportMetric(float64(spans)/b.Elapsed().Seconds(), "spans/s")
		})
	}
}
