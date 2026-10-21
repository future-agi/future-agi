package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

func testConfig() Config {
	c := DefaultConfig()
	c.RunID = "0123456789abcdef"
	c.ProjectID = "11111111-1111-4111-8111-111111111111"
	c.ProjectName = "isolated-benchmark"
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
}

func TestDryRunMakesNoNetworkRequests(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer srv.Close()
	c := testConfig()
	c.Endpoint, c.ClickHouseURL = srv.URL, srv.URL
	r, err := Run(context.Background(), c, Options{})
	if err != nil || calls.Load() != 0 || r.Status != "dry-run" || r.PlannedSpans != 500 || r.Counters.Attempted != 0 {
		t.Fatalf("dry run: %+v %v calls=%d", r.Counters, err, calls.Load())
	}
}

func TestSpanIdentityPayloadAndBatchDeterminism(t *testing.T) {
	c := testConfig()
	now := time.Unix(1700000000, 0)
	whole, err := c.batch(0, 80, now)
	if err != nil {
		t.Fatal(err)
	}
	req := ptraceotlp.NewExportRequest()
	if err := req.UnmarshalJSON(whole); err != nil {
		t.Fatal(err)
	}
	spans := req.Traces().ResourceSpans().At(0).ScopeSpans().At(0).Spans()
	seen := map[string]bool{}
	for i := 0; i < spans.Len(); i++ {
		sp := spans.At(i)
		if seen[sp.SpanID().String()] {
			t.Fatal("duplicate span ID")
		}
		seen[sp.SpanID().String()] = true
		if i%8 == 0 && !sp.ParentSpanID().IsEmpty() {
			t.Fatal("root has parent")
		}
		if i%8 != 0 && sp.ParentSpanID() != spans.At(i-i%8).SpanID() {
			t.Fatal("wrong root parent")
		}
		if sp.TraceID() != spans.At(i-i%8).TraceID() {
			t.Fatal("wrong trace grouping")
		}
		part, _ := c.batch(int64(i), 1, now)
		one := ptraceotlp.NewExportRequest()
		if err := one.UnmarshalJSON(part); err != nil {
			t.Fatal(err)
		}
		single := one.Traces().ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0)
		if sp.TraceID() != single.TraceID() || sp.SpanID() != single.SpanID() || sp.ParentSpanID() != single.ParentSpanID() {
			t.Fatal("IDs depend on batch boundary")
		}
	}
	if len(c.payload(0)) != 512 || len(c.payload(14)) != 4096 || len(c.payload(19)) != 32768 {
		t.Fatal("wrong mixed distribution")
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
}

func TestOTLPResponseAccounting(t *testing.T) {
	for _, tc := range []struct {
		name                           string
		status                         int
		body                           string
		ack, rejected, errors, partial int64
	}{
		{"success", 200, `{}`, 10, 0, 0, 0},
		{"partial", 200, `{"partialSuccess":{"rejectedSpans":"3","errorMessage":"queue full"}}`, 7, 3, 0, 1},
		{"all-rejected", 200, `{"partialSuccess":{"rejectedSpans":10}}`, 0, 10, 0, 1},
		{"warning", 200, `{"partialSuccess":{"errorMessage":"warning"}}`, 10, 0, 0, 1},
		{"invalid-rejected", 200, `{"partialSuccess":{"rejectedSpans":"11"}}`, 0, 0, 1, 0},
		{"negative-rejected", 200, `{"partialSuccess":{"rejectedSpans":"-1"}}`, 0, 0, 1, 0},
		{"malformed", 200, `<html>login</html>`, 0, 0, 1, 0},
		{"empty", 200, ``, 0, 0, 1, 0},
		{"throttle", 429, `{}`, 0, 0, 1, 0},
		{"server-error", 503, `{}`, 0, 0, 1, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()
			s := &runState{statuses: map[int]int64{}, samples: map[int64]lagSample{}}
			s.send(context.Background(), testConfig().client(), srv.URL, nil, 0, 10, []byte(`{}`), true)
			if s.counters.Attempted != 10 || s.counters.Acknowledged != tc.ack || s.counters.Accepted != tc.ack || s.counters.PartialRejected != tc.rejected || s.counters.RequestErrors != tc.errors || s.counters.PartialResponses != tc.partial {
				t.Fatalf("wrong accounting: %+v", s.counters)
			}
		})
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
	for _, phrase := range []string{"No zero-loss conclusion", "not-configured", "not traces", "Neither is certified"} {
		if !strings.Contains(out.String(), phrase) {
			t.Fatalf("missing honesty qualifier %q", phrase)
		}
	}
	r.SchemaVersion = "future-version"
	if Report(&out, r) == nil {
		t.Fatal("accepted unsupported schema")
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
	if p.P50 != 50 || p.P95 != 95 || p.P99 != 99 || p.Max != 100 {
		t.Fatalf("quantiles: %+v", p)
	}
	h = &histogram{}
	h.add(10001 * time.Millisecond)
	if h.summary().P99 != 11000 {
		t.Fatal("coarse buckets must be upper bounds")
	}
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
