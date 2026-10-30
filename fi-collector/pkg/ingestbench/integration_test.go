package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
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
	// clockSkew is how far the mock server's clock runs ahead of the
	// generator's; it stamps created_at (DEFAULT now64(6)) and answers now64.
	clockSkew time.Duration
	// createdAtShift forges created_at by this much on top of the clock.
	createdAtShift time.Duration
	// failDurable fails every durable lag query with HTTP 500.
	failDurable bool
}

const mockTimeLayout = "2006-01-02 15:04:05.000000"

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
			if _, ok := row["created_at"]; !ok {
				// The column DEFAULT, evaluated on the server's clock.
				row["created_at"] = time.Now().Add(m.clockSkew + m.createdAtShift).UTC().Format(mockTimeLayout)
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
	case strings.Contains(query, "now64(6)) AS t"):
		_ = enc.Encode(map[string]any{"t": time.Now().Add(m.clockSkew).UnixMicro()})
	case strings.Contains(query, "toUnixTimestamp64Micro(created_at)"):
		if m.failDurable {
			http.Error(w, "Code: 241. DB::Exception: Memory limit exceeded (MEMORY_LIMIT_EXCEEDED)", 500)
			return
		}
		m.durableLag(q, query, enc)
	case strings.Contains(query, "FROM system.settings WHERE name IN"):
		for _, kv := range [][2]string{{"async_insert", "0"}, {"insert_quorum", "0"}, {"wait_for_async_insert", "1"}} {
			_ = enc.Encode(map[string]any{"name": kv[0], "value": kv[1]})
		}
	case strings.Contains(query, "FROM system.merge_tree_settings"):
		for _, kv := range [][2]string{{"fsync_after_insert", "0"}, {"non_replicated_deduplication_window", "0"}} {
			_ = enc.Encode(map[string]any{"name": kv[0], "value": kv[1]})
		}
	case strings.Contains(query, "FROM system.parts"):
		_ = enc.Encode(map[string]any{"rows": len(m.rows), "bytes": 100 * len(m.rows), "uncompressed": 400 * len(m.rows), "parts": 1})
	case strings.Contains(query, "SELECT version()"):
		_ = enc.Encode(map[string]any{"version": "mock", "timezone": "UTC", "uptime": 1})
	case strings.Contains(query, "FROM system.tables"):
		_ = enc.Encode(map[string]any{"engine": "MockMergeTree", "sorting_key": "trace_id, id", "primary_key": "trace_id", "partition_key": "", "create_table_query": "CREATE TABLE mock.spans (id String) ENGINE = MockMergeTree"})
	case strings.Contains(query, "FROM system.clusters"):
	case strings.HasPrefix(query, clickHouseHardwareQuery):
		_ = enc.Encode(map[string]any{"max_threads": "'auto(4)'", "os_memory_total": 8 << 30, "cgroup_max_cpu": 0, "cgroup_memory_total": 0})
	case strings.HasPrefix(query, "SELECT count() AS rows FROM (SELECT 1"):
		_ = enc.Encode(map[string]any{"rows": min(1, len(m.matching(q)))})
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

// durableLag emulates the durable lag query: per distinct valid span of the
// chunk, its first created_at minus its request's scheduled send, bucketed like
// the latency histogram.
func (m *mockStorage) durableLag(q map[string][]string, query string, enc *json.Encoder) {
	num := func(k string) int64 { v, _ := strconv.ParseInt(first(q, "param_"+k), 10, 64); return v }
	lo, hi, batch, rate, origin, offset := num("lo"), num("hi"), num("batch"), num("rate"), num("origin"), num("offset")
	traceLo, traceHi := first(q, "param_trace_lo"), first(q, "param_trace_hi")
	inclusive := strings.Contains(query, "trace_id <= {trace_hi:String}")
	tlo, thi := paramTime(q, "param_time_lo"), paramTime(q, "param_time_hi")
	firstCreated := map[int64]int64{}
	for _, row := range m.matching(q) {
		tr := row["trace_id"].(string)
		if tr < traceLo || (inclusive && tr > traceHi) || (!inclusive && tr >= traceHi) {
			continue
		}
		if st := rowTime(row); st.Before(tlo) || !st.Before(thi) {
			continue
		}
		seq, ok := rowSeq(row)
		if !ok || seq < lo || seq >= hi || row["trace_id"] != m.cfg.traceString(seq) || row["id"] != spanID(seq) {
			continue
		}
		stamp, _ := row["created_at"].(string)
		created, err := time.Parse(mockTimeLayout, stamp)
		if err != nil {
			continue
		}
		if prev, ok := firstCreated[seq]; !ok || created.UnixMicro() < prev {
			firstCreated[seq] = created.UnixMicro()
		}
	}
	type bucket struct{ n, max int64 }
	buckets := map[int64]*bucket{}
	for seq, created := range firstCreated {
		lag := created - offset - (origin + seq/batch*batch*1000000/rate)
		b := int64(-1)
		if lag >= 0 {
			b = int64(latencyBucketMicros(lag))
		}
		if buckets[b] == nil {
			buckets[b] = &bucket{max: lag}
		}
		buckets[b].n++
		buckets[b].max = max(buckets[b].max, lag)
	}
	keys := make([]int64, 0, len(buckets))
	for k := range buckets {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	for _, k := range keys {
		_ = enc.Encode(map[string]any{"b": k, "n": buckets[k].n, "max_us": buckets[k].max})
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
				// The database server's own hardware view; unreported limits stay unset.
				if hw := r.Provenance.ClickHouse.Hardware; hw.Error != "" || hw.OSMemoryBytes != 8<<30 || hw.MaxThreads != "auto(4)" || hw.CGroupMaxCPU != 0 || hw.CGroupMemoryBytes != 0 {
					t.Fatalf("ClickHouse hardware provenance: %+v", hw)
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
	}{{EncodingJSON, false}, {EncodingProtobuf, false}, {EncodingProtobuf, true}, {EncodingJSON, true}, {EncodingGRPC, false}} {
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

// insertDelayProxy forwards every request to target and holds each span INSERT
// (into table) for delay first: a slow database, so a collector's in-flight
// batch holds its row budget for at least that long. Reads and the curated
// best-effort inserts pass straight through.
func insertDelayProxy(t *testing.T, target, table string, delay time.Duration) string {
	t.Helper()
	u, err := url.Parse(target)
	if err != nil {
		t.Fatal(err)
	}
	proxy := httputil.NewSingleHostReverseProxy(u)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Query().Get("query"), "INSERT INTO "+table+" ") {
			select {
			case <-time.After(delay):
			case <-r.Context().Done():
				return
			}
		}
		proxy.ServeHTTP(w, r)
	}))
	t.Cleanup(srv.Close)
	return srv.URL
}

// B11: a real collector driven into backpressure. Whole-request 503 /
// UNAVAILABLE refusals are counted as rejected with their retry hint,
// none of their spans is stored, and every acknowledged span is.
func runBackpressureCase(t *testing.T, chURL string, headers http.Header, writer chwriter.Config, encoding string, gzip bool, prepare func(Config) Config) Result {
	t.Helper()
	c := mockRunConfig()
	c.RunID = NewRunID()
	c.Encoding, c.Gzip = encoding, gzip
	// Small requests each fit the 2 MiB byte budget, but a slow insert holds
	// their row leases. Short lease waits expose busy refusals, not overshoot.
	c.Rate, c.Duration, c.BatchSize, c.Concurrency, c.VerifyChunk = 400, time.Second, 8, 4, 64
	c.Payload, c.Settle = "small", 2*time.Second
	c.DirectProbes.Enabled = false
	writer.URL = insertDelayProxy(t, writer.URL, writer.Table, 300*time.Millisecond)
	fixture := startCollector(t, writer, server.Config{IngestBudgetMiB: 2, IngestMaxWait: time.Millisecond, InsertConcurrency: 1, BatchMaxRows: 16, BatchMaxAge: 400 * time.Millisecond})
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
	if co.Acknowledged == 0 || co.RejectedNonRetryable != 0 {
		t.Fatalf("backpressure not exercised: %+v", co)
	}
	if encoding == EncodingGRPC {
		// UNAVAILABLE can originate in the transport after enqueue. Do not relabel
		// it as a proven refusal merely because this fixture knows the cause.
		if co.Ambiguous == 0 || co.WholeRequestRejected != 0 || r.Statuses["grpc:Unavailable"] != co.RequestOutcomes.Ambiguous {
			t.Fatalf("gRPC ambiguity lost: %+v %v", co, r.Statuses)
		}
		if v.MissingBy.AmbiguousMissing != uint64(co.Ambiguous) {
			t.Fatalf("ambiguous identities: %+v", v)
		}
	} else {
		if co.RejectedRetryable == 0 || co.Ambiguous != 0 || co.RequestOutcomes.RejectedWithRetryHint != co.RequestOutcomes.RejectedRetryable || r.Statuses["http:503"] != co.RequestOutcomes.RejectedRetryable {
			t.Fatalf("HTTP refusal accounting: %+v %v", co, r.Statuses)
		}
		if v.MissingBy.RejectedMissing != uint64(co.WholeRequestRejected) || r.RetryHints.Delay.Count == 0 {
			t.Fatalf("HTTP missing attribution: %+v %+v", v, r.RetryHints)
		}
	}
	if !v.Complete || v.MissingBy.AcknowledgedMissing != 0 || v.DataLossDetected || v.ContractViolation || v.DistinctExpected != uint64(co.Acknowledged) || r.Status != "measured-with-errors" {
		t.Fatalf("verification: %+v status %s", v, r.Status)
	}

	return r
}

// A request too large for the entire work pool is a permanent refusal in the
// current collector. No overshoot is allowed and no rejected identity is stored.
func runOverBudgetCase(t *testing.T, chURL string, headers http.Header, writer chwriter.Config, encoding string, gzip bool, prepare func(Config) Config) Result {
	t.Helper()
	c := mockRunConfig()
	c.RunID = NewRunID()
	c.Encoding, c.Gzip = encoding, gzip
	c.Rate, c.Duration, c.BatchSize, c.VerifyChunk = 256, time.Second, 128, 64
	c.Payload, c.Settle = "small", time.Second
	c.DirectProbes.Enabled = false
	fixture := startCollector(t, writer, server.Config{IngestBudgetMiB: 2, IngestMaxWait: time.Millisecond, InsertConcurrency: 1, BatchMaxRows: 16})
	c.ClickHouseURL, c.Endpoint = chURL, fixture.endpoint(encoding)
	c = prepare(c)
	r, err := Run(context.Background(), c, Options{ClickHouseHeaders: headers})
	if err != nil {
		t.Fatal(err)
	}
	co, v := r.Counters, r.Verification
	if co.Attempted != 256 || co.Requests != 2 || co.Acknowledged != 0 || co.RejectedNonRetryable != 256 || co.RejectedRetryable != 0 || co.Ambiguous != 0 || co.CheckIdentity() != nil {
		t.Fatalf("permanent oversize accounting: %+v", co)
	}
	label := "http:413"
	if encoding == EncodingGRPC {
		label = "grpc:InvalidArgument"
	}
	if r.Statuses[label] != 2 {
		t.Fatalf("statuses: %v", r.Statuses)
	}
	if !v.Complete || v.DistinctExpected != 0 || v.Missing != 256 || v.MissingBy.RejectedMissing != 256 || v.MissingBy.AcknowledgedMissing != 0 || v.ContractViolation {
		t.Fatalf("verification: %+v", v)
	}
	return r
}

// tamperProxy sits between a collector's writer and ClickHouse and corrupts the
// span INSERTs the way a lossy, duplicating storage path would: it drops the
// first `drop` rows that pass and stores the next `dup` rows twice. The copy
// differs only in service_name, a column of the table's ORDER BY key, so no
// ReplacingMergeTree merge can collapse it before the verification reads it;
// its trace and span IDs are unchanged, so it is a duplicate of a span of the
// run. Every other request, including the curated-table inserts, passes as is.
type tamperProxy struct {
	table              string
	proxy              *httputil.ReverseProxy
	mu                 sync.Mutex
	drop, dup          int
	dropped, duplicate int
}

func newTamperProxy(t *testing.T, target, table string, drop, dup int) (*tamperProxy, string) {
	t.Helper()
	u, err := url.Parse(target)
	if err != nil {
		t.Fatal(err)
	}
	p := &tamperProxy{table: table, proxy: httputil.NewSingleHostReverseProxy(u), drop: drop, dup: dup}
	srv := httptest.NewServer(p)
	t.Cleanup(srv.Close)
	return p, srv.URL
}

func (p *tamperProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !strings.HasPrefix(r.URL.Query().Get("query"), "INSERT INTO "+p.table+" ") {
		p.proxy.ServeHTTP(w, r)
		return
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read insert", http.StatusBadRequest)
		return
	}
	var out bytes.Buffer
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber() // re-encode numbers exactly
	p.mu.Lock()
	for {
		var row map[string]any
		if err := d.Decode(&row); err == io.EOF {
			break
		} else if err != nil {
			p.mu.Unlock()
			http.Error(w, "invalid insert row", http.StatusBadRequest)
			return
		}
		if p.dropped < p.drop {
			p.dropped++
			continue
		}
		line, _ := json.Marshal(row)
		out.Write(append(line, '\n'))
		if p.duplicate < p.dup {
			p.duplicate++
			row["service_name"] = fmt.Sprint(row["service_name"]) + "-duplicate-copy"
			line, _ = json.Marshal(row)
			out.Write(append(line, '\n'))
		}
	}
	p.mu.Unlock()
	r.Body, r.ContentLength = io.NopCloser(bytes.NewReader(out.Bytes())), int64(out.Len())
	r.Header.Set("Content-Length", strconv.Itoa(out.Len()))
	p.proxy.ServeHTTP(w, r)
}

// BM-T01 fallibility: with K rows of acknowledged spans removed from storage and
// J further spans stored twice, the exact-ID verification must report exactly K
// missing (all acknowledged-but-missing, data loss) and exactly J duplicate rows,
// no unexpected row, and must not certify the run.
func runTamperCase(t *testing.T, chURL string, headers http.Header, writer chwriter.Config, prepare func(Config) Config) Result {
	t.Helper()
	// Equal counts deliberately hide corruption from a plain count(): only
	// the exact identity verifier can distinguish the lost and duplicated rows.
	const drop, dup = 3, 3
	c := mockRunConfig()
	c.RunID = NewRunID()
	c.Rate, c.Duration, c.BatchSize, c.VerifyChunk = 20, time.Second, 4, 7
	c.Payload, c.Settle = "small", time.Second
	c.DirectProbes.Enabled = false
	proxy, front := newTamperProxy(t, writer.URL, writer.Table, drop, dup)
	writer.URL = front
	fixture := startCollector(t, writer, server.Config{})
	c.ClickHouseURL, c.Endpoint = chURL, fixture.endpoint(EncodingProtobuf)
	c = prepare(c)
	r, err := Run(context.Background(), c, Options{ClickHouseHeaders: headers})
	if err != nil {
		t.Fatal(err)
	}
	proxy.mu.Lock()
	dropped, duplicated := proxy.dropped, proxy.duplicate
	proxy.mu.Unlock()
	co, v := r.Counters, r.Verification
	if dropped != drop || duplicated != dup || co.Attempted != 20 || co.Acknowledged != 20 || co.CheckIdentity() != nil {
		t.Fatalf("tamper setup: dropped %d duplicated %d, counters %+v", dropped, duplicated, co)
	}
	var rangeSpans uint64
	for _, m := range v.MissingRanges {
		if m.Class != outcomeAcknowledged.String() {
			t.Fatalf("missing range of class %s: %+v", m.Class, v.MissingRanges)
		}
		rangeSpans += m.MissingSpans
	}
	if !v.Complete || v.Missing != drop || v.MissingBy.AcknowledgedMissing != drop || !v.DataLossDetected || rangeSpans != drop ||
		v.DuplicateRows != dup || v.DistinctExpected != 20-drop || v.Rows != 20-drop+dup || v.UnexpectedRows != 0 || v.ContractViolation {
		t.Fatalf("the verifier did not report exactly %d missing and %d duplicates: %+v", drop, dup, v)
	}
	if r.Status == "measured-profile-complete" {
		t.Fatalf("a run with lost and duplicated spans was certified: %s", r.Status)
	}
	return r
}

func TestVerifierCountsExactlyTheDroppedAndDuplicatedSpans(t *testing.T) {
	store := &mockStorage{}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	defer ch.Close()
	runTamperCase(t, ch.URL, nil, chwriter.Config{URL: ch.URL, Table: "spans", Database: "default"}, func(c Config) Config {
		store.mu.Lock()
		store.cfg = c
		store.mu.Unlock()
		return c
	})
}

func TestBackpressureAndOverBudgetAccountingThroughRealCollector(t *testing.T) {
	for _, tc := range []struct {
		encoding string
		gzip     bool
	}{{EncodingProtobuf, false}, {EncodingJSON, true}, {EncodingGRPC, false}} {
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
			runOverBudgetCase(t, ch.URL, nil, writer, tc.encoding, tc.gzip, prepare)
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

// B4: checkpoint writes are spaced by at least twenty times their own cost, so
// a huge ledger (outcomes alternating under backpressure for a day) cannot make
// the verification spend most of its budget rewriting the checkpoint.
func TestCheckpointSavesAreSpacedByTheirCost(t *testing.T) {
	c := testConfig()
	path := filepath.Join(t.TempDir(), "verify.checkpoint.json")
	vf := &verifier{cfg: c, cp: newCheckpoint(c, 0, newOutcomeLedger(int64(c.BatchSize)), newTimeline(time.Now())), path: path}
	vf.lastSave, vf.saveCost = time.Now().Add(-15*time.Second), time.Second
	if err := vf.saveProgress(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("saved 15s after a save that took 1s: %v", err)
	}
	vf.lastSave = time.Now().Add(-21 * time.Second)
	if err := vf.saveProgress(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); err != nil || vf.saveCost <= 0 {
		t.Fatalf("not saved 21s after a 1s save, or its cost not measured: %v %s", err, vf.saveCost)
	}
	// A cheap checkpoint is still saved every checkpointEvery.
	vf.lastSave, vf.saveCost = time.Now().Add(-checkpointEvery-time.Second), time.Millisecond
	before := vf.lastSave
	if err := vf.saveProgress(); err != nil || !vf.lastSave.After(before) {
		t.Fatalf("cheap checkpoint not saved after %s: %v", checkpointEvery, err)
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
	return verifyRun(context.Background(), clickHouse{cfg: c, client: c.readClient()}, ledger, tl, attempted, preexisting, nil)
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
				// Each clock-hour namespace window answers 2 rows; the run covers
				// two windows when it is near the top of an hour.
				if !v.Complete || v.NamespaceWindows < 1 || v.OutsideNamespace != 2*uint64(v.NamespaceWindows) || v.UnexpectedRows != v.OutsideNamespace {
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

// B9: a trace or session probe with no acknowledged candidate old enough is
// reported as skipped, never as measured and never silently as not executed.
func TestDirectProbesWithoutACandidateAreReportedAsSkipped(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `{"rows":0}`)
	}))
	defer srv.Close()
	c := testConfig()
	c.ClickHouseURL = srv.URL
	d := newDirectRunner(clickHouse{cfg: c, client: c.readClient()}, time.Now())
	d.round(context.Background(), "during-load", candidate{})
	d.round(context.Background(), "during-load", candidate{})
	for _, r := range d.results() {
		if r.Phase != "during-load" {
			continue
		}
		switch r.Name {
		case "attribute-filter":
			if r.Status != "measured" || r.Successes != 2 || r.Skipped != 0 {
				t.Fatalf("attribute filter: %+v", r)
			}
		default:
			if r.Status != "skipped-no-candidate" || r.Skipped != 2 || r.Successes != 0 || r.Latency.Count != 0 {
				t.Fatalf("%s without a candidate: %+v", r.Name, r)
			}
		}
	}
}

// B9: a direct query cut off by the end of its phase is counted as censored,
// neither as a success (no latency sample) nor as a database error. A probe that
// also completed queries is "measured-with-censoring"; one whose only query was
// cut off is "window-censored".
func TestDirectProbeCutOffByThePhaseIsCensored(t *testing.T) {
	for _, answeredFirst := range []bool{false, true} {
		t.Run(fmt.Sprintf("answered-round-first-%t", answeredFirst), func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			release := make(chan struct{})
			var answering atomic.Bool
			answering.Store(answeredFirst)
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				// Consume the query first: only then does the server watch the
				// connection and see the client abandon the request.
				_, _ = io.Copy(io.Discard, r.Body)
				if answering.Load() {
					fmt.Fprint(w, `{"rows":1,"payload_bytes":10}`)
					return
				}
				// The phase ends while ClickHouse is still answering, after the
				// query has already run for 200 ms.
				time.Sleep(200 * time.Millisecond)
				cancel()
				select {
				case <-r.Context().Done():
				case <-release:
				}
			}))
			defer srv.Close()
			// Runs before srv.Close, which waits for the handler to return.
			defer close(release)
			c := testConfig()
			c.ClickHouseURL = srv.URL
			d := newDirectRunner(clickHouse{cfg: c, client: c.readClient()}, time.Now())
			if answeredFirst {
				d.round(ctx, "during-load", candidate{seq: 0, ok: true})
				answering.Store(false)
			}
			d.round(ctx, "during-load", candidate{seq: 0, ok: true})
			// A round that starts after the phase ended runs and counts nothing.
			d.round(ctx, "during-load", candidate{seq: 0, ok: true})
			wantStatus, wantSuccesses := "window-censored", int64(0)
			if answeredFirst {
				wantStatus, wantSuccesses = "measured-with-censoring", 1
			}
			for _, r := range d.results() {
				if r.Phase != "during-load" || r.Name != "trace-by-id" {
					continue
				}
				if r.Status != wantStatus || r.Censored != 1 || r.Successes != wantSuccesses || r.Errors != 0 || r.Latency.Count != wantSuccesses || r.FailureLatency.Count != 0 {
					t.Fatalf("cut-off query misreported: %+v", r)
				}
				// F13: the cut-off query is not dropped from the latency picture:
				// its elapsed time is kept as a right-censored lower bound.
				if c := r.CensoredElapsed; c.Count != 1 || c.Max < 200 || c.P50 > c.Max || c.P50 < 200*(1-1.0/128) {
					t.Fatalf("censored query elapsed not recorded as a lower bound: %+v", c)
				}
				res := newResult(c)
				res.DirectReads = []DirectReadResult{r}
				var out strings.Builder
				if err := Report(&out, res); err != nil {
					t.Fatal(err)
				}
				if !strings.Contains(out.String(), "1 censored queries had run at least") {
					t.Fatalf("report hides the censored lower bound: %s", out.String())
				}
				return
			}
			t.Fatal("trace probe result missing")
		})
	}
}

// B4: the namespace scan reads each toStartOfHour key bucket once: windows tile
// the run window without gaps or overlap and none crosses a clock hour.
func TestNamespaceWindowsTileTheRunInsideClockHours(t *testing.T) {
	from := time.Date(2026, 10, 22, 9, 58, 30, 0, time.UTC)
	for _, to := range []time.Time{from.Add(time.Minute), from.Add(90 * time.Second), from.Add(25 * time.Hour), time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC)} {
		windows := hourWindows(from, to)
		at := from
		for _, w := range windows {
			if !w.Lo.Equal(at) || !w.Hi.After(w.Lo) || w.Hi.After(to) || !w.Lo.Truncate(time.Hour).Equal(w.Hi.Add(-time.Nanosecond).Truncate(time.Hour)) {
				t.Fatalf("to %s: bad window %+v after %s", to, w, at)
			}
			at = w.Hi
		}
		if !at.Equal(to) {
			t.Fatalf("to %s: windows end at %s", to, at)
		}
	}
	if n := len(hourWindows(from, from.Add(25*time.Hour))); n != 26 {
		t.Fatalf("25 hours from :58 touch 26 clock hours, got %d windows", n)
	}
}

// storedRow is a row as the collector stores span seq of the run in c.
func storedRow(c Config, seq int64, at time.Time) map[string]any {
	return map[string]any{
		"project_id": c.ProjectID, "trace_id": c.traceString(seq), "id": spanID(seq),
		"start_time":   at.UTC().Format("2006-01-02 15:04:05.000000"),
		"attrs_string": map[string]any{"benchmark.run_id": c.RunID, "benchmark.seq": strconv.FormatInt(seq, 10)},
	}
}

// B3: missing spans are decomposed by what the collector said about their
// request, including partial-success requests that cross a chunk boundary, and
// stored spans of refused requests are a contract violation. Six requests of six
// spans, verified in five chunks of eight:
//
//	r0 acknowledged, all stored (span 0 twice)   -> 1 visible duplicate
//	r1 partial, 2 rejected, 3 of 6 stored        -> 2 rejected-missing, 1 loss (lower bound)
//	r2 refused retryable, 1 of 6 stored          -> 5 rejected-missing, 1 rejected-but-persisted
//	r3 acknowledged, span 20 missing             -> 1 loss
//	r4 ambiguous, 2 of 6 stored                  -> 4 ambiguous-missing, 2 ambiguous-persisted
//	r5 partial, 1 rejected, all 6 stored         -> 1 persisted beyond the acknowledgement
func TestMissingSpansDecomposeByRequestOutcome(t *testing.T) {
	c := testConfig()
	c.BatchSize, c.VerifyChunk, c.VerifyQueryRetries = 6, 8, 0
	start := time.Now().Add(-time.Minute).UTC()
	store := &mockStorage{cfg: c}
	stored := []int64{0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 18, 19, 21, 22, 23, 24, 25, 30, 31, 32, 33, 34, 35}
	for _, seq := range stored {
		store.rows = append(store.rows, storedRow(c, seq, start))
	}
	srv := httptest.NewServer(http.HandlerFunc(store.serve))
	defer srv.Close()
	c.ClickHouseURL = srv.URL
	store.cfg = c
	ledger := newOutcomeLedger(int64(c.BatchSize))
	for i, o := range []exportOutcome{
		{class: outcomeAcknowledged},
		{class: outcomePartial, rejected: 2},
		{class: outcomeRejectedRetryable},
		{class: outcomeAcknowledged},
		{class: outcomeAmbiguous},
		{class: outcomePartial, rejected: 1},
	} {
		if err := ledger.record(int64(i*c.BatchSize), o); err != nil {
			t.Fatal(err)
		}
	}
	tl := newTimeline(start)
	tl.add(start, 0, 36)
	v := verifyRun(context.Background(), clickHouse{cfg: c, client: c.readClient()}, ledger, tl, 36, 0, nil)
	if !v.Complete || v.Error != "" || v.TotalChunks != 5 {
		t.Fatalf("verification did not finish: %+v", v)
	}
	want := MissingByAcknowledgement{AcknowledgedMissing: 2, PartialBeyondRejected: 1, PartialRejectedMissing: 2, RejectedMissing: 5, AmbiguousMissing: 4}
	if v.MissingBy != want || v.Missing != 13 {
		t.Fatalf("missing %d decomposed as %+v, want %+v", v.Missing, v.MissingBy, want)
	}
	if v.PersistedBy != (PersistedByAcknowledgement{RejectedButPersisted: 1, PartialExcess: 1, AmbiguousPersisted: 2}) {
		t.Fatalf("persisted without acknowledgement: %+v", v.PersistedBy)
	}
	if v.Rows != 24 || v.DistinctExpected != 23 || v.DuplicateRows != 1 || v.UnexpectedRows != 0 || !v.DataLossDetected || !v.ContractViolation {
		t.Fatalf("totals or conclusions: %+v", v)
	}
	// BM-T12: every missing span is placed in a request range of one outcome
	// class, in request order, with its scheduled offset. r1 crosses the chunk
	// boundary at span 8 and is still one range.
	at := func(r int64) float64 { return float64(r*int64(c.BatchSize)) / float64(c.Rate) }
	wantRanges := []MissingRange{
		{Class: "partial_rejection", FirstRequest: 1, LastRequest: 1, FirstScheduledS: at(1), LastScheduledS: at(1), MissingSpans: 3},
		{Class: "whole_request_rejected_retryable", FirstRequest: 2, LastRequest: 2, FirstScheduledS: at(2), LastScheduledS: at(2), MissingSpans: 5},
		{Class: "acknowledged", FirstRequest: 3, LastRequest: 3, FirstScheduledS: at(3), LastScheduledS: at(3), MissingSpans: 1},
		{Class: "ambiguous", FirstRequest: 4, LastRequest: 4, FirstScheduledS: at(4), LastScheduledS: at(4), MissingSpans: 4},
	}
	if fmt.Sprint(v.MissingRanges) != fmt.Sprint(wantRanges) || v.MissingRangesOmitted != 0 {
		t.Fatalf("missing ranges %+v, want %+v", v.MissingRanges, wantRanges)
	}
	var inRanges uint64
	for _, m := range v.MissingRanges {
		inRanges += m.MissingSpans
	}
	if inRanges != v.Missing {
		t.Fatalf("ranges hold %d of %d missing spans", inRanges, v.Missing)
	}
}

// B2/B11: HTTP 429, the collector's quota answer, is a whole-request retryable
// rejection carrying its Retry-After, end to end. A throttling proxy in front of
// the real collector refuses the second request; its spans are expected to be
// missing and every acknowledged span must be stored.
func TestThrottledRequestsAreCountedAsRetryableRejections(t *testing.T) {
	c := mockRunConfig()
	c.DirectProbes.Enabled = false
	store := &mockStorage{cfg: c}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	defer ch.Close()
	collector := startFixtureCollector(t, ch.URL)
	var requests atomic.Int64
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if requests.Add(1) == 2 {
			w.Header().Set("Retry-After", "3")
			http.Error(w, "quota exceeded", http.StatusTooManyRequests)
			return
		}
		req, _ := http.NewRequestWithContext(r.Context(), http.MethodPost, collector, strings.NewReader(string(body)))
		req.Header = r.Header.Clone()
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			http.Error(w, "collector unreachable", http.StatusBadGateway)
			return
		}
		defer resp.Body.Close()
		for k, v := range resp.Header {
			w.Header()[k] = v
		}
		w.WriteHeader(resp.StatusCode)
		_, _ = io.Copy(w, resp.Body)
	}))
	defer proxy.Close()
	c.ClickHouseURL, c.Endpoint = ch.URL, proxy.URL+"/v1/traces"
	store.cfg = c
	r, err := Run(context.Background(), c, Options{})
	if err != nil {
		t.Fatal(err)
	}
	co, v := r.Counters, r.Verification
	if r.AccountingError != "" || co.Attempted != 12 || co.Acknowledged != 8 || co.RejectedRetryable != 4 || co.WholeRequestRejected != 4 || co.Ambiguous != 0 {
		t.Fatalf("429 accounting: %s %+v", r.AccountingError, co)
	}
	if co.RequestOutcomes != (RequestOutcomes{Acknowledged: 2, RejectedRetryable: 1, RejectedWithRetryHint: 1}) || r.Statuses["http:429"] != 1 || r.Statuses["http:200"] != 2 {
		t.Fatalf("request outcomes %+v statuses %v", co.RequestOutcomes, r.Statuses)
	}
	if r.RetryHints.Requests != 1 || r.RetryHints.Delay.Count != 1 || r.RetryHints.Delay.P50 != 3000 {
		t.Fatalf("Retry-After not recorded: %+v", r.RetryHints)
	}
	if !v.Complete || v.Missing != 4 || v.MissingBy.RejectedMissing != 4 || v.MissingBy.AcknowledgedMissing != 0 || v.DistinctExpected != 8 || v.DataLossDetected || v.ContractViolation {
		t.Fatalf("verification %+v", v)
	}
	if r.Status != "measured-with-errors" {
		t.Fatalf("a throttled run was reported as %s", r.Status)
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
	// The durable lag's extra query per chunk has its own budget, by default
	// the size of the exact-ID budget, so the whole plan allows both.
	withLag, withoutLag := c, c
	withoutLag.DurableLag = false
	if a, b, d := withLag.effectiveVerifyTimeout(50_000_000), withoutLag.effectiveVerifyTimeout(50_000_000), withLag.effectiveDurableLagTimeout(50_000_000); a != 10*time.Minute+1000*time.Second || b != a || d != a {
		t.Fatalf("exact-ID budget with/without durable lag %s / %s, durable lag budget %s", a, b, d)
	}
	plan := withLag
	plan.Rate, plan.Duration = 1000, 50_000*time.Second
	if got, want := plan.VerifyBudget(), 2*plan.effectiveVerifyTimeout(plan.PlannedSpans()); got != want {
		t.Fatalf("plan budget with durable lag %s, want exact-ID plus durable lag %s", got, want)
	}
	explicit := withLag
	explicit.VerifyTimeout = 12 * time.Hour
	if explicit.effectiveDurableLagTimeout(1) != 12*time.Hour {
		t.Fatal("the durable lag budget does not follow an explicit exact-ID budget by default")
	}
	explicit.DurableLagTimeout = 3 * time.Hour
	if explicit.effectiveDurableLagTimeout(1) != 3*time.Hour || explicit.effectiveVerifyTimeout(1) != 12*time.Hour {
		t.Fatal("explicit durable lag budget ignored")
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

// F10: a slow durable lag query cannot exhaust the exact-ID verification. The
// exact-ID budget (1 s here) and the durable lag budget (1 s, the default) are
// separate: a durable query that runs out of its own budget stops the durable
// lag, and the exact-ID verification still completes. With one shared budget
// the same run ended as "verification budget exhausted" (incomplete).
func TestSlowDurableLagQueriesDoNotSpendTheExactIDBudget(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch q := string(body); {
		case strings.Contains(q, "toUnixTimestamp64Micro(created_at)"):
			select {
			case <-time.After(3 * time.Second):
			case <-r.Context().Done():
				return
			}
			fmt.Fprint(w, `{"b":5,"n":1,"max_us":5}`)
		case strings.Contains(q, "GROUP BY r"):
			fmt.Fprint(w, `{"r":0,"d":1,"n":1}`)
		default:
			fmt.Fprint(w, `{"rows":0}`)
		}
	}))
	defer srv.Close()
	c := testConfig()
	c.ClickHouseURL, c.VerifyQueryRetries, c.VerifyTimeout = srv.URL, 0, time.Second
	ledger := newOutcomeLedger(int64(c.BatchSize))
	if err := ledger.record(0, exportOutcome{class: outcomeAcknowledged}); err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	tl := newTimeline(start)
	tl.add(start, 0, 1)
	v := verifyRun(context.Background(), clickHouse{cfg: c, client: c.readClient()}, ledger, tl, 1, 0, c.durableLagOrigin(start, ClockSample{}))
	if !v.Complete || v.DistinctExpected != 1 || v.Error != "" {
		t.Fatalf("the exact-ID verification ran out of budget because of the durable lag: %+v", v)
	}
	d := v.DurableLag
	if d.Status != "errors" || !strings.Contains(d.Method, "durable lag budget of 1s exhausted") || v.DurableLagTimeoutSecs != 1 || v.DurableLagElapsedSecs < 0.9 || v.TimeoutSeconds != 1 {
		t.Fatalf("durable lag budget not reported: %+v (durable budget %.1f s, spent %.1f s)", d, v.DurableLagTimeoutSecs, v.DurableLagElapsedSecs)
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
