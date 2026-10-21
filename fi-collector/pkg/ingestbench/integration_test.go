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
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
)

// mockStorage stores REAL converter/writer output, but only emulates verification
// queries. It is deliberately not a ClickHouse SQL or performance test.
type mockStorage struct {
	mu        sync.Mutex
	rows      []map[string]any
	cfg       Config
	drop      bool
	duplicate bool
	invalid   bool
}

func (m *mockStorage) serve(w http.ResponseWriter, r *http.Request) {
	m.mu.Lock()
	defer m.mu.Unlock()
	q := r.URL.Query()
	if strings.HasPrefix(q.Get("query"), "INSERT INTO spans ") {
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
	body, _ := io.ReadAll(r.Body)
	query := string(body)
	w.Header().Set("Content-Type", "application/json")
	run := q.Get("param_run")
	matching := []map[string]any{}
	for _, row := range m.rows {
		attrs, _ := row["attrs_string"].(map[string]any)
		if row["project_id"] == q.Get("param_project") && attrs["benchmark.run_id"] == run {
			matching = append(matching, row)
		}
	}
	if strings.Contains(query, "SELECT DISTINCT id") {
		for _, row := range matching {
			if strings.Contains(q.Get("param_ids"), row["id"].(string)) {
				_ = json.NewEncoder(w).Encode(map[string]any{"id": row["id"]})
			}
		}
		return
	}
	if strings.Contains(query, "uniqExactIf") {
		lo, _ := strconv.ParseInt(q.Get("param_lo"), 10, 64)
		hi, _ := strconv.ParseInt(q.Get("param_hi"), 10, 64)
		seen := map[int64]bool{}
		n := 0
		for _, row := range matching {
			attrs := row["attrs_string"].(map[string]any)
			seq, err := strconv.ParseInt(attrs["benchmark.seq"].(string), 10, 64)
			if err == nil && seq >= lo && seq < hi && row["trace_id"] == m.cfg.traceString(seq) && row["id"] == spanID(seq) {
				n++
				seen[seq] = true
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"valid_rows": n, "distinct_spans": len(seen)})
		return
	}
	if strings.Contains(query, "AS unexpected") {
		unexpected := 0
		for _, row := range matching {
			attrs := row["attrs_string"].(map[string]any)
			seq, _ := strconv.ParseInt(attrs["benchmark.seq"].(string), 10, 64)
			if row["trace_id"] != m.cfg.traceString(seq) || row["id"] != spanID(seq) {
				unexpected++
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"rows": len(matching), "unexpected": unexpected})
		return
	}
	if strings.Contains(query, "SELECT count() AS rows") {
		_ = json.NewEncoder(w).Encode(map[string]any{"rows": len(matching)})
		return
	}
	http.Error(w, "mock does not implement query", 400)
}

func startFixtureCollector(t *testing.T, chURL string) string {
	t.Helper()
	return startCollectorWithWriterConfig(t, chwriter.Config{URL: chURL, Table: "spans", Database: "default"})
}

func startCollectorWithWriterConfig(t *testing.T, cfg chwriter.Config) string {
	t.Helper()
	cfg.MaxRetries, cfg.InitialBackoff, cfg.MaxBackoff = 1, time.Millisecond, time.Millisecond
	cfg.RequestTimeout, cfg.DeadLetterFile = 5*time.Second, t.TempDir()+"/dl.jsonl"
	writer, err := chwriter.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := lis.Addr().String()
	lis.Close()
	srv := server.New(server.Config{GRPCAddr: "127.0.0.1:0", HTTPAddr: addr, BatchMaxRows: 8, BatchMaxAge: 20 * time.Millisecond}, writer, nil, nil, nil, server.WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
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
	for time.Now().Before(deadline) {
		conn, err := net.DialTimeout("tcp", addr, 20*time.Millisecond)
		if err == nil {
			conn.Close()
			return "http://" + addr + "/v1/traces"
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("collector did not start")
	return ""
}

func TestMockCollectorPipelineAndIndependentLossDetection(t *testing.T) {
	for _, mode := range []string{"complete", "drop", "duplicate", "byte-budget"} {
		t.Run(mode, func(t *testing.T) {
			c := testConfig()
			c.Execute, c.AllowUnauth = true, true
			c.Rate, c.Duration, c.BatchSize, c.VerifyChunk = 40, 300*time.Millisecond, 4, 5
			c.Settle, c.PollInterval, c.Environment = 220*time.Millisecond, 100*time.Millisecond, "mock-ClickHouse-real-collector"
			if mode == "byte-budget" {
				c.MaxBytes = 1
			}
			store := &mockStorage{cfg: c, drop: mode == "drop", duplicate: mode == "duplicate"}
			ch := httptest.NewServer(http.HandlerFunc(store.serve))
			defer ch.Close()
			c.ClickHouseURL, c.Endpoint = ch.URL, startFixtureCollector(t, ch.URL)
			r, err := Run(context.Background(), c, Options{})
			if err != nil {
				t.Fatal(err)
			}
			if !r.Verification.Complete {
				t.Fatalf("incomplete: %+v", r.Verification)
			}
			if mode == "byte-budget" {
				if r.Counters.Attempted != 0 || r.Counters.Generated != 4 || r.StopReason != "byte-budget" || r.Status == "measured-profile-complete" {
					t.Fatalf("budget failed: %+v", r.Counters)
				}
				return
			}
			if r.Counters.Attempted != 12 || r.Counters.Acknowledged != 12 {
				t.Fatalf("wrong exported count: %+v", r.Counters)
			}
			if mode == "drop" && (r.Verification.Missing != 1 || r.Verification.DistinctExpected != 11 || r.Status == "measured-profile-complete") {
				t.Fatalf("missed dropped span: %+v", r.Verification)
			}
			if mode == "duplicate" && (r.Verification.DuplicateRows != 1 || r.Verification.DistinctExpected != 12 || r.Status == "measured-profile-complete") {
				t.Fatalf("missed duplicate: %+v", r.Verification)
			}
			if mode == "complete" {
				if r.Status != "measured-profile-complete" || r.Verification.Missing != 0 || r.Verification.Rows != 12 || r.Lag.Latency.Count == 0 {
					t.Fatalf("complete fixture failed: %+v %+v", r.Verification, r.Lag)
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
				// Existing IDs cannot make a second execution appear lossless.
				_, err := Run(context.Background(), c, Options{})
				if err == nil || !strings.Contains(err.Error(), "already has") {
					t.Fatalf("reuse not rejected: %v", err)
				}
			}
		})
	}
}

func TestVerificationRejectsInvalidAndChangingResponses(t *testing.T) {
	for _, mode := range []string{"bad-json", "missing-fields", "changing", "unexpected"} {
		t.Run(mode, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				body, _ := io.ReadAll(r.Body)
				if mode == "bad-json" {
					fmt.Fprint(w, "Code: 241 memory exceeded")
					return
				}
				if mode == "missing-fields" {
					fmt.Fprint(w, `{}`)
					return
				}
				if strings.Contains(string(body), "uniqExactIf") {
					fmt.Fprint(w, `{"valid_rows":1,"distinct_spans":1}`)
				} else if mode == "unexpected" {
					fmt.Fprint(w, `{"rows":2,"unexpected":1}`)
				} else {
					fmt.Fprint(w, `{"rows":2,"unexpected":0}`)
				}
			}))
			defer srv.Close()
			c := testConfig()
			c.ClickHouseURL = srv.URL
			v := (clickHouse{cfg: c, client: c.client()}).verify(context.Background(), 1)
			if mode == "unexpected" {
				if !v.Complete || v.UnexpectedRows != 1 {
					t.Fatalf("unexpected rows: %+v", v)
				}
			} else if v.Complete {
				t.Fatalf("invalid verification certified: %+v", v)
			}
		})
	}
}

func TestApplicationProbesDoNotTreatLoginAsSuccess(t *testing.T) {
	for _, content := range []string{`{"results":[]}`, `<html>Login</html>`, `{"error":"unauthorized"}`} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("Authorization") != "Bearer fixture" {
				t.Error("missing auth")
			}
			fmt.Fprint(w, content)
		}))
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
		p := ReadProbe{Name: "trace-list", URL: srv.URL, Method: "GET", ExpectStatus: 200, ExpectKey: "results", IntervalMS: 1000}
		r := runReadProbe(ctx, testConfig().client(), p, http.Header{"Authorization": []string{"Bearer fixture"}})
		cancel()
		srv.Close()
		if content == `{"results":[]}` {
			if r.Successes != 1 || r.Errors != 0 {
				t.Fatal(r)
			}
		} else if r.Successes != 0 || r.Errors != 1 {
			t.Fatal(r)
		}
	}
}
