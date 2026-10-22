package ingestbench

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
)

// mockStorage stores REAL converter/writer output, but only emulates the
// verification and probe queries. It is deliberately not a ClickHouse SQL or
// performance test: the live tests run the same queries on real ClickHouse.
type mockStorage struct {
	mu        sync.Mutex
	rows      []map[string]any
	cfg       Config
	drop      bool
	duplicate bool
	// failChunk fails the Nth chunk query (1-based) once with HTTP 500.
	failChunk    int
	chunkQueries int
	inserts      atomic.Int64
}

func (m *mockStorage) matching(q map[string][]string) []map[string]any {
	project, run := first(q, "param_project"), first(q, "param_run")
	out := []map[string]any{}
	for _, row := range m.rows {
		attrs, _ := row["attrs_string"].(map[string]any)
		if row["project_id"] == project && (run == "" || attrs["benchmark.run_id"] == run) {
			out = append(out, row)
		}
	}
	return out
}

func first(q map[string][]string, k string) string {
	if v := q[k]; len(v) > 0 {
		return v[0]
	}
	return ""
}

func rowSeq(row map[string]any) (int64, bool) {
	attrs, _ := row["attrs_string"].(map[string]any)
	s, _ := attrs["benchmark.seq"].(string)
	seq, err := strconv.ParseInt(s, 10, 64)
	return seq, err == nil && seq >= 0
}

// rowPayload is length(input) + length(output) of a stored row.
func rowPayload(row map[string]any) int {
	in, _ := row["input"].(string)
	out, _ := row["output"].(string)
	return len(in) + len(out)
}

func rowTime(row map[string]any) time.Time {
	s, _ := row["start_time"].(string)
	t, _ := time.Parse("2006-01-02 15:04:05.000000", s)
	return t
}

func paramTime(q map[string][]string, k string) time.Time {
	t, _ := time.Parse("2006-01-02 15:04:05.000000", first(q, k))
	return t
}

func (m *mockStorage) serve(w http.ResponseWriter, r *http.Request) {
	m.mu.Lock()
	defer m.mu.Unlock()
	q := r.URL.Query()
	if strings.HasPrefix(q.Get("query"), "INSERT INTO spans ") {
		m.inserts.Add(1)
		d := json.NewDecoder(r.Body)
		for {
			var row map[string]any
			if err := d.Decode(&row); err == io.EOF {
				break
			} else if err != nil {
				http.Error(w, "invalid writer row", 400)
				return
			}
			if m.drop && row["id"] == spanID(3) {
				continue
			}
			m.rows = append(m.rows, row)
			if m.duplicate && row["id"] == spanID(2) {
				m.rows = append(m.rows, row)
			}
		}
		return
	}
	if strings.HasPrefix(q.Get("query"), "INSERT") {
		return
	}
	if q.Get("readonly") != "2" || q.Get("wait_end_of_query") != "1" {
		http.Error(w, "benchmark reads must be readonly=2 with wait_end_of_query=1", 400)
		return
	}
	body, _ := io.ReadAll(r.Body)
	query := string(body)
	w.Header().Set("Content-Type", "application/json")
	enc := json.NewEncoder(w)
	switch {
	case strings.Contains(query, "SELECT version()"):
		_ = enc.Encode(map[string]any{"version": "mock", "timezone": "UTC", "uptime": 1})
	case strings.Contains(query, "FROM system.tables"):
		_ = enc.Encode(map[string]any{"engine": "MockMergeTree", "sorting_key": "trace_id, id", "primary_key": "trace_id", "partition_key": ""})
	case strings.Contains(query, "FROM system.clusters"):
	case strings.Contains(query, "trace_id IN {traces:Array(String)}") && strings.Contains(query, "count() AS rows"):
		n := 0
		for _, row := range m.matching(q) {
			if strings.Contains(first(q, "param_traces"), "'"+row["trace_id"].(string)+"'") {
				n++
			}
		}
		_ = enc.Encode(map[string]any{"rows": n})
	case strings.Contains(query, "GROUP BY r"):
		m.chunkQueries++
		if m.chunkQueries == m.failChunk {
			http.Error(w, "Code: 159. DB::Exception: Timeout exceeded (TIMEOUT_EXCEEDED)", 500)
			return
		}
		m.chunk(q, query, enc)
	case strings.Contains(query, "NOT (trace_id >= {ns_lo:String}"):
		lo, hi := paramTime(q, "param_time_lo"), paramTime(q, "param_time_hi")
		n := 0
		for _, row := range m.matching(q) {
			tr, st := row["trace_id"].(string), rowTime(row)
			if !st.Before(lo) && st.Before(hi) && (tr < first(q, "param_ns_lo") || tr > first(q, "param_ns_hi")) {
				n++
			}
		}
		_ = enc.Encode(map[string]any{"rows": n})
	case strings.Contains(query, "SELECT DISTINCT id"):
		for _, row := range m.matching(q) {
			if strings.Contains(first(q, "param_ids"), row["id"].(string)) {
				_ = enc.Encode(map[string]any{"id": row["id"]})
			}
		}
	case strings.Contains(query, "trace_id = {trace:String}"):
		n, payload := 0, 0
		for _, row := range m.matching(q) {
			if row["trace_id"] == first(q, "param_trace") {
				n++
				payload += rowPayload(row)
			}
		}
		_ = enc.Encode(map[string]any{"rows": n, "payload_bytes": payload})
	case strings.Contains(query, "trace_session_id = {session:UUID}"):
		n, payload := 0, 0
		for _, row := range m.matching(q) {
			if row["trace_session_id"] == first(q, "param_session") {
				n++
				payload += rowPayload(row)
			}
		}
		_ = enc.Encode(map[string]any{"rows": n, "payload_bytes": payload})
	case strings.Contains(query, "attrs_string['benchmark.cohort'] = 'variant'"):
		n := 0
		for _, row := range m.matching(q) {
			attrs, _ := row["attrs_string"].(map[string]any)
			if attrs["benchmark.cohort"] == "variant" && row["status"] == "ERROR" {
				n++
			}
		}
		_ = enc.Encode(map[string]any{"rows": n})
	default:
		http.Error(w, "mock does not implement query", 400)
	}
}

// chunk emulates the grouped chunk query: rows in the trace range and time
// window, grouped by request when their identity is valid, else under the
// sentinel request.
func (m *mockStorage) chunk(q map[string][]string, query string, enc *json.Encoder) {
	lo, _ := strconv.ParseInt(first(q, "param_lo"), 10, 64)
	hi, _ := strconv.ParseInt(first(q, "param_hi"), 10, 64)
	batch, _ := strconv.ParseInt(first(q, "param_batch"), 10, 64)
	traceLo, traceHi := first(q, "param_trace_lo"), first(q, "param_trace_hi")
	inclusive := strings.Contains(query, "trace_id <= {trace_hi:String}")
	tlo, thi := paramTime(q, "param_time_lo"), paramTime(q, "param_time_hi")
	type group struct {
		d map[int64]bool
		n int
	}
	groups := map[uint64]*group{}
	for _, row := range m.matching(q) {
		tr := row["trace_id"].(string)
		if tr < traceLo || (inclusive && tr > traceHi) || (!inclusive && tr >= traceHi) {
			continue
		}
		if st := rowTime(row); st.Before(tlo) || !st.Before(thi) {
			continue
		}
		seq, ok := rowSeq(row)
		valid := ok && seq >= lo && seq < hi && row["trace_id"] == m.cfg.traceString(seq) && row["id"] == spanID(seq)
		key := sentinelRequest
		if valid {
			key = uint64(seq / batch)
		}
		g := groups[key]
		if g == nil {
			g = &group{d: map[int64]bool{}}
			groups[key] = g
		}
		g.n++
		if valid {
			g.d[seq] = true
		}
	}
	keys := make([]uint64, 0, len(groups))
	for k := range groups {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	for _, k := range keys {
		_ = enc.Encode(map[string]any{"r": k, "d": len(groups[k].d), "n": groups[k].n})
	}
}

// collectorFixture is the real in-process collector (receiver, converter,
// batch writer); only PostgreSQL authentication is bypassed.
type collectorFixture struct {
	httpURL  string
	grpcURL  string
	requests atomic.Int64
}

func freeAddr(t *testing.T) string {
	t.Helper()
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := lis.Addr().String()
	lis.Close()
	return addr
}

func startFixtureCollector(t *testing.T, chURL string) string {
	t.Helper()
	return startCollector(t, chwriter.Config{URL: chURL, Table: "spans", Database: "default"}, server.Config{}).httpURL
}

func startCollectorWithWriterConfig(t *testing.T, cfg chwriter.Config) string {
	t.Helper()
	return startCollector(t, cfg, server.Config{}).httpURL
}

func startCollector(t *testing.T, cfg chwriter.Config, sc server.Config) *collectorFixture {
	t.Helper()
	cfg.MaxRetries, cfg.InitialBackoff, cfg.MaxBackoff = 1, time.Millisecond, time.Millisecond
	if cfg.RequestTimeout == 0 {
		cfg.RequestTimeout = 5 * time.Second
	}
	cfg.DeadLetterFile = t.TempDir() + "/dl.jsonl"
	writer, err := chwriter.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	httpAddr, grpcAddr := freeAddr(t), freeAddr(t)
	sc.HTTPAddr, sc.GRPCAddr = httpAddr, grpcAddr
	if sc.BatchMaxRows == 0 {
		sc.BatchMaxRows, sc.BatchMaxAge = 8, 20*time.Millisecond
	}
	srv := server.New(sc, writer, nil, nil, nil, server.WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- srv.Run(ctx) }()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Error("collector did not stop")
		}
		writer.Close()
	})
	deadline := time.Now().Add(2 * time.Second)
	for _, addr := range []string{httpAddr, grpcAddr} {
		for {
			conn, err := net.DialTimeout("tcp", addr, 20*time.Millisecond)
			if err == nil {
				conn.Close()
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("collector did not start")
			}
			time.Sleep(5 * time.Millisecond)
		}
	}
	return &collectorFixture{httpURL: "http://" + httpAddr + "/v1/traces", grpcURL: "http://" + grpcAddr}
}

func (f *collectorFixture) endpoint(encoding string) string {
	if encoding == EncodingGRPC {
		return f.grpcURL
	}
	return f.httpURL
}

func mockRunConfig() Config {
	c := testConfig()
	c.Execute, c.AllowUnauth = true, true
	// 12 spans in three batches due at 0, 333 and 667 ms: a third of a second of
	// slack for the last batch on a loaded race-test runner.
	c.Rate, c.Duration, c.BatchSize, c.VerifyChunk = 12, time.Second, 4, 5
	c.Settle, c.PollInterval, c.Environment = 220*time.Millisecond, 100*time.Millisecond, "mock-ClickHouse-real-collector"
	c.DirectProbes = DirectProbeConfig{Enabled: true, Interval: 500 * time.Millisecond, MinAge: 0, AfterSettleSamples: 2, QueryTimeout: 2 * time.Second}
	return c
}

func TestMockCollectorPipelineAndIndependentLossDetection(t *testing.T) {
	for _, mode := range []string{"complete", "drop", "duplicate", "byte-budget"} {
		t.Run(mode, func(t *testing.T) {
			c := mockRunConfig()
			if mode == "byte-budget" {
				c.MaxBytes = 1
			}
			store := &mockStorage{cfg: c, drop: mode == "drop", duplicate: mode == "duplicate"}
			ch := httptest.NewServer(http.HandlerFunc(store.serve))
			defer ch.Close()
			c.ClickHouseURL, c.Endpoint = ch.URL, startFixtureCollector(t, ch.URL)
			store.cfg = c
			r, err := Run(context.Background(), c, Options{})
			if err != nil {
				t.Fatal(err)
			}
			if !r.Verification.Complete || r.AccountingError != "" {
				t.Fatalf("incomplete: %+v %s", r.Verification, r.AccountingError)
			}
			if mode == "byte-budget" {
				if r.Counters.Attempted != 0 || r.Counters.Generated != 4 || r.StopReason != "byte-budget" || r.Status == "measured-profile-complete" {
					t.Fatalf("budget failed: %+v", r.Counters)
				}
				return
			}
			if r.Counters.Attempted != 12 || r.Counters.Acknowledged != 12 || r.Counters.RequestOutcomes.Acknowledged != 3 {
				t.Fatalf("wrong exported count: %+v", r.Counters)
			}
			if mode == "drop" {
				v := r.Verification
				if v.Missing != 1 || v.DistinctExpected != 11 || v.MissingBy.AcknowledgedMissing != 1 || !v.DataLossDetected || r.Status == "measured-profile-complete" {
					t.Fatalf("missed dropped span: %+v", v)
				}
			}
			if mode == "duplicate" && (r.Verification.DuplicateRows != 1 || r.Verification.DistinctExpected != 12 || r.Status == "measured-profile-complete") {
				t.Fatalf("missed duplicate: %+v", r.Verification)
			}
			if mode == "complete" {
				if r.Status != "measured-profile-complete" || r.Verification.Missing != 0 || r.Verification.Rows != 12 || r.Lag.Latency.Count == 0 {
					t.Fatalf("complete fixture failed: %+v %+v", r.Verification, r.Lag)
				}
				if r.Verification.Chunks != 2 || r.Verification.TotalChunks != 2 || r.Verification.RecheckedChunks < 1 || r.Verification.PreexistingRunRows != 0 {
					t.Fatalf("chunking/recheck not exercised: %+v", r.Verification)
				}
				var after DirectReadResult
				for _, d := range r.DirectReads {
					if d.Phase == "after-settle" && d.Name == "trace-by-id" {
						after = d
					}
				}
				if after.Successes != 2 || after.NonEmpty != 2 || after.Status != "measured" {
					t.Fatalf("after-settle direct trace reads not measured: %+v", after)
				}
				// The trace probe reads the span payloads, not only key columns.
				if after.PayloadBytes == nil || after.PayloadBytes.Count != 2 || after.PayloadBytes.Min <= 0 {
					t.Fatalf("trace probe did not read span payloads: %+v", after.PayloadBytes)
				}
				for _, d := range r.DirectReads {
					if d.Name == "attribute-filter" && d.PayloadBytes != nil {
						t.Fatalf("count-only probe reports payload bytes: %+v", d)
					}
				}
				if r.Provenance.SUT.Revision != "in-process-test-build" || r.Provenance.ClickHouse.Version != "mock" || r.Workload.Spans != 12 {
					t.Fatalf("provenance/workload missing: %+v %+v", r.Provenance, r.Workload)
				}
				if path := os.Getenv("OTLPBENCH_MOCK_RESULT"); path != "" {
					f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
					if err != nil {
						t.Fatal(err)
					}
					if err := WriteResult(f, r); err != nil {
						t.Fatal(err)
					}
					f.Close()
				}
				// Existing IDs cannot make a second execution appear lossless,
				// and the refused run reports what it found (B6).
				again, err := Run(context.Background(), c, Options{})
				if err == nil || !strings.Contains(err.Error(), "already has") || again.Verification.PreexistingRunRows == 0 {
					t.Fatalf("reuse not rejected: %v %+v", err, again.Verification)
				}
			}
		})
	}
}

// Every wire path production SDKs use is measured, end to end, with the
// encoding recorded in the result.
func TestMockPipelineOverEveryEncoding(t *testing.T) {
	for _, tc := range []struct {
		encoding string
		gzip     bool
	}{{EncodingJSON, false}, {EncodingProtobuf, false}, {EncodingProtobuf, true}, {EncodingJSON, true}, {EncodingGRPC, false}, {EncodingGRPC, true}} {
		t.Run(fmt.Sprintf("%s-gzip-%t", tc.encoding, tc.gzip), func(t *testing.T) {
			c := mockRunConfig()
			c.Encoding, c.Gzip = tc.encoding, tc.gzip
			c.RunID = NewRunID()
			c.DirectProbes.Enabled = false
			store := &mockStorage{cfg: c}
			ch := httptest.NewServer(http.HandlerFunc(store.serve))
			defer ch.Close()
			fixture := startCollector(t, chwriter.Config{URL: ch.URL, Table: "spans", Database: "default"}, server.Config{})
			c.ClickHouseURL, c.Endpoint = ch.URL, fixture.endpoint(tc.encoding)
			store.cfg = c
			r, err := Run(context.Background(), c, Options{})
			if err != nil {
				t.Fatal(err)
			}
			if r.Status != "measured-profile-complete" || r.Config.Encoding != tc.encoding || r.Config.Gzip != tc.gzip {
				t.Fatalf("status %s, counters %+v, verification %+v", r.Status, r.Counters, r.Verification)
			}
			if r.Counters.Bytes <= 0 || r.Counters.UncompressedBytes <= 0 {
				t.Fatalf("byte accounting missing: %+v", r.Counters)
			}
			if tc.gzip && r.Counters.Bytes >= r.Counters.UncompressedBytes {
				t.Fatalf("gzip did not reduce wire bytes: %d >= %d", r.Counters.Bytes, r.Counters.UncompressedBytes)
			}
			if !tc.gzip && tc.encoding != EncodingGRPC && r.Counters.Bytes != r.Counters.UncompressedBytes {
				t.Fatalf("uncompressed HTTP wire bytes differ: %+v", r.Counters)
			}
			wantStatus := "http:200"
			if tc.encoding == EncodingGRPC {
				wantStatus = "grpc:OK"
			}
			if r.Statuses[wantStatus] != 3 {
				t.Fatalf("statuses %v", r.Statuses)
			}
		})
	}
}

// B11: a real collector driven into backpressure. Whole-request 503 /
// RESOURCE_EXHAUSTED refusals are counted as rejected with their retry hint,
// none of their spans is stored, and every acknowledged span is.
func runBackpressureCase(t *testing.T, chURL string, headers http.Header, writer chwriter.Config, encoding string, gzip bool, prepare func(Config) Config) Result {
	t.Helper()
	c := mockRunConfig()
	c.RunID = NewRunID()
	c.Encoding, c.Gzip = encoding, gzip
	// 50 requests/s against a 16-row budget that drains every 400 ms: after two
	// requests fill it, every request until the next flush is refused. Small
	// payloads keep request building cheap even under the race detector, so
	// refusals do not depend on generator speed.
	c.Rate, c.Duration, c.BatchSize, c.Concurrency, c.VerifyChunk = 400, time.Second, 8, 4, 64
	c.Payload, c.Settle = "small", 2*time.Second
	c.DirectProbes.Enabled = false
	fixture := startCollector(t, writer, server.Config{MaxPendingRows: 16, BatchMaxRows: 1000, BatchMaxAge: 400 * time.Millisecond})
	c.ClickHouseURL, c.Endpoint = chURL, fixture.endpoint(encoding)
	c = prepare(c)
	r, err := Run(context.Background(), c, Options{ClickHouseHeaders: headers})
	if err != nil {
		t.Fatal(err)
	}
	co, v := r.Counters, r.Verification
	if r.AccountingError != "" || co.CheckIdentity() != nil {
		t.Fatalf("accounting: %s %+v", r.AccountingError, co)
	}
	if co.RejectedRetryable == 0 || co.Acknowledged == 0 || co.RejectedNonRetryable != 0 || co.Ambiguous != 0 {
		t.Fatalf("backpressure not exercised as expected: %+v", co)
	}
	if co.RequestOutcomes.RejectedWithRetryHint != co.RequestOutcomes.RejectedRetryable {
		t.Fatalf("a retryable refusal came without Retry-After/RetryInfo: %+v", co.RequestOutcomes)
	}
	label := "http:503"
	if encoding == EncodingGRPC {
		label = "grpc:ResourceExhausted"
	}
	if r.Statuses[label] != co.RequestOutcomes.RejectedRetryable {
		t.Fatalf("statuses %v vs outcomes %+v", r.Statuses, co.RequestOutcomes)
	}
	if !v.Complete || v.MissingBy.AcknowledgedMissing != 0 || v.DataLossDetected || v.ContractViolation {
		t.Fatalf("acknowledged spans lost or rejected spans stored: %+v", v)
	}
	if v.Missing != uint64(co.WholeRequestRejected) || v.MissingBy.RejectedMissing != uint64(co.WholeRequestRejected) || v.DistinctExpected != uint64(co.Acknowledged) {
		t.Fatalf("missing spans not attributed to rejection: counters %+v verification %+v", co, v)
	}
	if r.Status != "measured-with-errors" || r.RetryHints.Requests != co.RequestOutcomes.RejectedRetryable || r.RetryHints.Delay.Count == 0 {
		t.Fatalf("status %s retry hints %+v", r.Status, r.RetryHints)
	}
	return r
}

// B11: a request larger than the whole row budget is refused as
// non-retryable (413 / INVALID_ARGUMENT, no retry hint), exactly once.
func runOversizeCase(t *testing.T, chURL string, headers http.Header, writer chwriter.Config, encoding string, gzip bool, prepare func(Config) Config) Result {
	t.Helper()
	c := mockRunConfig()
	c.RunID = NewRunID()
	c.Encoding, c.Gzip = encoding, gzip
	c.Rate, c.Duration, c.BatchSize, c.VerifyChunk = 40, time.Second, 20, 64
	c.Payload = "small"
	c.DirectProbes.Enabled = false
	fixture := startCollector(t, writer, server.Config{MaxPendingRows: 16, BatchMaxRows: 1000, BatchMaxAge: 50 * time.Millisecond})
	c.ClickHouseURL, c.Endpoint = chURL, fixture.endpoint(encoding)
	c.CollectorMaxPendingRows = 16
	c = prepare(c)
	var warn strings.Builder
	r, err := Run(context.Background(), c, Options{ClickHouseHeaders: headers, Warn: &warn})
	if err != nil {
		t.Fatal(err)
	}
	co, v := r.Counters, r.Verification
	if co.Attempted != 40 || co.Requests != 2 || co.RejectedNonRetryable != 40 || co.RequestOutcomes.RejectedNonRetryable != 2 || co.RequestOutcomes.RejectedWithRetryHint != 0 || co.Acknowledged != 0 || co.CheckIdentity() != nil {
		t.Fatalf("oversize accounting: %+v", co)
	}
	label := "http:413"
	if encoding == EncodingGRPC {
		label = "grpc:InvalidArgument"
	}
	if r.Statuses[label] != 2 {
		t.Fatalf("statuses %v", r.Statuses)
	}
	if !v.Complete || v.Missing != 40 || v.MissingBy.RejectedMissing != 40 || v.MissingBy.AcknowledgedMissing != 0 || v.ContractViolation || v.Rows != 0 {
		t.Fatalf("verification %+v", v)
	}
	if !strings.Contains(warn.String(), "every request will be refused as non-retryable") {
		t.Fatalf("the oversize plan was not warned about loudly: %q", warn.String())
	}
	return r
}

func TestBackpressureAndOversizeAccountingThroughRealCollector(t *testing.T) {
	for _, tc := range []struct {
		encoding string
		gzip     bool
	}{{EncodingProtobuf, false}, {EncodingJSON, true}, {EncodingGRPC, true}} {
		t.Run(fmt.Sprintf("%s-gzip-%t", tc.encoding, tc.gzip), func(t *testing.T) {
			store := &mockStorage{}
			ch := httptest.NewServer(http.HandlerFunc(store.serve))
			defer ch.Close()
			writer := chwriter.Config{URL: ch.URL, Table: "spans", Database: "default"}
			// The mock checks stored identities against the run's config.
			prepare := func(c Config) Config {
				store.mu.Lock()
				store.cfg = c
				store.mu.Unlock()
				return c
			}
			runBackpressureCase(t, ch.URL, nil, writer, tc.encoding, tc.gzip, prepare)
			runOversizeCase(t, ch.URL, nil, writer, tc.encoding, tc.gzip, prepare)
		})
	}
}

// B4: an interrupted verification resumes from its checkpoint, in a new
// process, to the same answer an uninterrupted one gives.
func TestVerificationResumesFromCheckpoint(t *testing.T) {
	c := mockRunConfig()
	c.VerifyChunk, c.VerifyQueryRetries = 8, 0
	c.DirectProbes.Enabled = false
	c.VerifyCheckpoint = filepath.Join(t.TempDir(), "verify.checkpoint.json")
	store := &mockStorage{cfg: c, failChunk: 2}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	defer ch.Close()
	c.ClickHouseURL, c.Endpoint = ch.URL, startFixtureCollector(t, ch.URL)
	store.cfg = c
	r, err := Run(context.Background(), c, Options{})
	if err != nil {
		t.Fatal(err)
	}
	if r.Verification.Complete || r.Status != "incomplete" || r.Verification.Chunks != 1 || !strings.Contains(r.Verification.Error, "TIMEOUT_EXCEEDED") {
		t.Fatalf("interrupted verification certified or misreported: %s %+v", r.Status, r.Verification)
	}
	// An unfinished scan concludes nothing: unscanned spans count as missing,
	// but no loss is claimed from them.
	if v := r.Verification; v.DataLossDetected || v.ScannedAttempted != 8 || v.Unscanned != 4 || v.Missing != 4 || v.MissingBy.AcknowledgedMissing != 0 {
		t.Fatalf("incomplete verification overclaimed: %+v", v)
	}
	if _, err := Run(context.Background(), c, Options{}); err == nil || !strings.Contains(err.Error(), "checkpoint already exists") {
		t.Fatalf("a new run overwrote a checkpoint: %v", err)
	}
	cfg, v, err := ResumeVerification(context.Background(), c.VerifyCheckpoint, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.RunID != c.RunID || !v.Complete || v.ResumedAtChunk != 1 || v.Chunks != 2 || v.TotalChunks != 2 || v.DistinctExpected != 12 || v.Missing != 0 || v.MissingBy.AcknowledgedMissing != 0 || v.PreexistingRunRows != 0 {
		t.Fatalf("resumed verification: %+v", v)
	}
	// A completed checkpoint answers without querying again.
	queries := store.chunkQueries
	_, again, err := ResumeVerification(context.Background(), c.VerifyCheckpoint, nil, nil)
	if err != nil || !again.Complete || again.DistinctExpected != 12 || store.chunkQueries != queries {
		t.Fatalf("completed checkpoint re-queried or changed: %+v %v", again, err)
	}
}

func cannedVerifier(t *testing.T, handler http.HandlerFunc, attempted int64, preexisting uint64) Verification {
	t.Helper()
	srv := httptest.NewServer(handler)
	defer srv.Close()
	c := testConfig()
	c.ClickHouseURL, c.VerifyQueryRetries = srv.URL, 0
	ledger := newOutcomeLedger(int64(c.BatchSize))
	if attempted > 0 {
		if err := ledger.record(0, exportOutcome{class: outcomeAcknowledged}); err != nil {
			t.Fatal(err)
		}
	}
	tl := newTimeline(time.Now())
	tl.add(time.Now(), 0, attempted)
	return verifyRun(context.Background(), clickHouse{cfg: c, client: c.readClient()}, ledger, tl, attempted, preexisting)
}

func TestVerificationRejectsInvalidAndChangingResponses(t *testing.T) {
	for _, mode := range []string{"bad-json", "missing-fields", "out-of-range", "too-many-distinct", "changing", "unexpected", "outside-namespace", "server-error"} {
		t.Run(mode, func(t *testing.T) {
			var chunkCalls atomic.Int32
			v := cannedVerifier(t, func(w http.ResponseWriter, r *http.Request) {
				body, _ := io.ReadAll(r.Body)
				chunk := strings.Contains(string(body), "GROUP BY r")
				switch {
				case mode == "bad-json":
					fmt.Fprint(w, "Code: 241 memory exceeded")
				case mode == "missing-fields":
					fmt.Fprint(w, `{}`)
				case mode == "server-error":
					http.Error(w, "Code: 241. DB::Exception: Memory limit exceeded (MEMORY_LIMIT_EXCEEDED)", 500)
				case !chunk && mode == "outside-namespace":
					fmt.Fprint(w, `{"rows":2}`)
				case !chunk:
					fmt.Fprint(w, `{"rows":0}`)
				case mode == "out-of-range":
					fmt.Fprint(w, `{"r":99,"d":1,"n":1}`)
				case mode == "too-many-distinct":
					fmt.Fprint(w, `{"r":0,"d":2,"n":2}`)
				case mode == "changing" && chunkCalls.Add(1) > 1:
					fmt.Fprint(w, `{"r":0,"d":1,"n":2}`)
				case mode == "unexpected":
					fmt.Fprint(w, "{\"r\":0,\"d\":1,\"n\":1}\n{\"r\":18446744073709551615,\"d\":0,\"n\":1}\n")
				default:
					fmt.Fprint(w, `{"r":0,"d":1,"n":1}`)
				}
			}, 1, 0)
			switch mode {
			case "unexpected":
				if !v.Complete || v.UnexpectedRows != 1 || v.Rows != 2 || v.Missing != 0 {
					t.Fatalf("unexpected rows: %+v", v)
				}
			case "outside-namespace":
				if !v.Complete || v.OutsideNamespace != 2 || v.UnexpectedRows != 2 {
					t.Fatalf("outside-namespace rows: %+v", v)
				}
			case "server-error":
				if v.Complete || !strings.Contains(v.Error, "MEMORY_LIMIT_EXCEEDED") {
					t.Fatalf("server error hidden: %+v", v)
				}
			default:
				if v.Complete {
					t.Fatalf("invalid verification certified: %+v", v)
				}
			}
		})
	}
}

// B6: the preflight row count survives into the final verification.
func TestPreexistingRunRowsSurviveVerification(t *testing.T) {
	v := cannedVerifier(t, func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if strings.Contains(string(body), "GROUP BY r") {
			fmt.Fprint(w, `{"r":0,"d":1,"n":1}`)
			return
		}
		fmt.Fprint(w, `{"rows":0}`)
	}, 1, 7)
	if !v.Complete || v.PreexistingRunRows != 7 {
		t.Fatalf("preexisting rows overwritten: %+v", v)
	}
}

// B4: the verification budget and per-query limit are configurable, derive
// from the run size by default, and reach ClickHouse as server-side limits.
func TestVerificationBudgetsScaleAndReachTheServer(t *testing.T) {
	c := testConfig()
	if got := c.effectiveVerifyTimeout(1_296_000_000); got < 7*time.Hour {
		t.Fatalf("a 24h sustained profile gets only %s of verification budget", got)
	}
	if got := c.effectiveVerifyTimeout(500); got != 10*time.Minute {
		t.Fatalf("smoke verification budget %s", got)
	}
	c.VerifyChunk = 1_000_000
	if got := c.effectiveVerifyQueryTimeout(); got < 50*time.Second {
		t.Fatalf("1M-span chunks get only %s per query", got)
	}
	c.VerifyTimeout, c.VerifyQueryTimeout, c.VerifyMaxMemory = 3*time.Hour, 4*time.Minute, 2<<30
	if c.effectiveVerifyTimeout(1) != 3*time.Hour || c.effectiveVerifyQueryTimeout() != 4*time.Minute {
		t.Fatal("explicit budgets ignored")
	}
	var seen atomic.Value
	v := cannedVerifier(t, func(w http.ResponseWriter, r *http.Request) {
		seen.Store(r.URL.Query().Get("max_execution_time") + "/" + r.URL.Query().Get("max_memory_usage"))
		body, _ := io.ReadAll(r.Body)
		if strings.Contains(string(body), "GROUP BY r") {
			fmt.Fprint(w, `{"r":0,"d":1,"n":1}`)
			return
		}
		fmt.Fprint(w, `{"rows":0}`)
	}, 1, 0)
	if !v.Complete || seen.Load() != "30/1073741824" || v.QueryTimeoutSecs != 30 {
		t.Fatalf("limits not sent: %v %+v", seen.Load(), v)
	}
}

type probeRoundTripFunc func(*http.Request) (*http.Response, error)

func (f probeRoundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type probeCancelOnClose struct {
	io.ReadCloser
	cancel context.CancelFunc
}

func (b probeCancelOnClose) Close() error {
	err := b.ReadCloser.Close()
	b.cancel()
	return err
}

func TestApplicationProbesDoNotTreatLoginAsSuccess(t *testing.T) {
	for _, tc := range []struct {
		name, content string
		success       bool
	}{
		{"success", `{"results":[]}`, true},
		{"login", `<html>Login</html>`, false},
		{"error", `{"error":"unauthorized"}`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("Authorization") != "Bearer fixture" {
					t.Error("missing auth")
				}
				fmt.Fprint(w, tc.content)
			}))
			defer srv.Close()
			// The deadline is only a deadlock guard. End the load window AFTER
			// the real response has been consumed, rather than racing HTTP with
			// a 20ms timer on a contended race-test runner.
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			client := testConfig().client()
			transport := client.Transport.(*http.Transport)
			defer transport.CloseIdleConnections()
			client.Transport = probeRoundTripFunc(func(req *http.Request) (*http.Response, error) {
				resp, err := transport.RoundTrip(req)
				if err != nil {
					cancel()
					return nil, err
				}
				resp.Body = probeCancelOnClose{ReadCloser: resp.Body, cancel: cancel}
				return resp, nil
			})
			p := ReadProbe{Name: "trace-list", URL: srv.URL, Method: "GET", ExpectStatus: 200, ExpectKey: "results", IntervalMS: 1000}
			r := runReadProbe(ctx, client, p, http.Header{"Authorization": []string{"Bearer fixture"}})
			if r.Censored != 0 || r.HTTPStatuses[http.StatusOK] != 1 {
				t.Fatalf("expected exactly one fully consumed response: %+v", r)
			}
			if tc.success {
				if r.Successes != 1 || r.Errors != 0 || r.Latency.Count != 1 {
					t.Fatal(r)
				}
			} else if r.Successes != 0 || r.Errors != 1 || r.FailureLatency.Count != 1 {
				t.Fatal(r)
			}
		})
	}
}
