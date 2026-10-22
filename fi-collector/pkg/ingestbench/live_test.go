package ingestbench

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
)

// liveTarget is an ALREADY provisioned, isolated v2 schema. The live tests
// create/delete no infrastructure and never target remote hosts. The
// production receiver/converter/writer run in process without PG auth;
// authenticated external-collector coverage belongs to benchmark/run-profile.sh.
type liveTarget struct {
	url, database, table string
	headers              http.Header
	writer               chwriter.Config
	slow                 bool
}

func requireLiveTarget(t *testing.T) liveTarget {
	t.Helper()
	url := os.Getenv("OTLPBENCH_LIVE_CH_URL")
	if url == "" {
		t.Skip("live ClickHouse not configured; mock tests are NOT ClickHouse evidence")
	}
	if os.Getenv("OTLPBENCH_LIVE_CONFIRM") != "isolated-test-stack" {
		t.Fatal("set OTLPBENCH_LIVE_CONFIRM=isolated-test-stack after verifying the target")
	}
	lt := liveTarget{url: url, database: "default", table: "spans", headers: http.Header{}}
	if db := os.Getenv("OTLPBENCH_LIVE_CH_DATABASE"); db != "" {
		lt.database = db
	}
	if table := os.Getenv("OTLPBENCH_LIVE_CH_TABLE"); table != "" {
		lt.table = table
	}
	user, password := os.Getenv("OTLPBENCH_LIVE_CH_USER"), os.Getenv("OTLPBENCH_LIVE_CH_PASSWORD")
	if user != "" {
		lt.headers.Set("X-ClickHouse-User", user)
	}
	if password != "" {
		lt.headers.Set("X-ClickHouse-Key", password)
	}
	// OTLPBENCH_LIVE_SLOW_HOST=1 is for a shared, overloaded developer
	// ClickHouse: it only lengthens the writer, settle and query timeouts and
	// verifies in one chunk. Every assertion stays the same.
	lt.slow = os.Getenv("OTLPBENCH_LIVE_SLOW_HOST") == "1"
	lt.writer = chwriter.Config{URL: url, Database: lt.database, Table: lt.table, Username: user, Password: password, RequestTimeout: 30 * time.Second}
	if lt.slow {
		// An insert the client abandons may still complete on the server; the
		// writer's retry would then store the rows twice. Wait it out instead.
		lt.writer.RequestTimeout = 15 * time.Minute
	}
	return lt
}

func (lt liveTarget) apply(c Config) Config {
	c.Database, c.Table = lt.database, lt.table
	c.Environment = "isolated-real-ClickHouse-inprocess-collector-auth-bypassed"
	c.Settle = max(c.Settle, 3*time.Second)
	if lt.slow {
		// Settle must outlast the slowest acknowledged insert (minutes there).
		c.Settle = max(c.Settle, 8*time.Minute)
		c.VerifyQueryTimeout, c.VerifyQueryRetries, c.VerifyChunk = 5*time.Minute, 3, 1_000_000
		c.DirectProbes.QueryTimeout = 5 * time.Minute
	}
	return c
}

func writeLiveArtifact(t *testing.T, env string, v any) {
	t.Helper()
	path := os.Getenv(env)
	if path == "" {
		return
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		t.Fatal(err)
	}
}

func TestLiveClickHouseCollectorPipeline(t *testing.T) {
	lt := requireLiveTarget(t)
	c := testConfig()
	c.RunID, c.ClickHouseURL = NewRunID(), lt.url
	c.Execute, c.AllowUnauth = true, true
	c.Rate, c.Duration, c.BatchSize, c.VerifyChunk = 20, time.Second, 4, 7
	c.Settle, c.PollInterval, c.MaxBytes = time.Second, 100*time.Millisecond, 1<<20
	c.DirectProbes = DirectProbeConfig{Enabled: true, Interval: 500 * time.Millisecond, MinAge: 0, AfterSettleSamples: 3, QueryTimeout: 10 * time.Second}
	c = lt.apply(c)
	if lt.slow {
		// Several chunks even on a slow host: the multi-chunk SQL is what this proves.
		c.VerifyChunk = 7
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	c.Endpoint = startCollectorWithWriterConfig(t, lt.writer)
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Minute)
	defer cancel()
	r, err := Run(ctx, c, Options{ClickHouseHeaders: lt.headers})
	writeLiveArtifact(t, "OTLPBENCH_LIVE_RESULT", r)
	if err != nil {
		t.Fatal(err)
	}
	if r.Status != "measured-profile-complete" || r.Verification.DistinctExpected != 20 || r.Lag.Latency.Count == 0 {
		t.Fatalf("real pipeline verification failed: status=%s counters=%+v verification=%+v lag=%+v", r.Status, r.Counters, r.Verification, r.Lag)
	}
	if r.Provenance.ClickHouse.Version == "" || r.Provenance.ClickHouse.TableEngine == "" {
		t.Fatalf("ClickHouse provenance missing: %+v", r.Provenance.ClickHouse)
	}
	for _, d := range r.DirectReads {
		if d.Phase == "after-settle" && (d.Status != "measured" || d.Successes != 3) {
			t.Fatalf("direct read %s/%s not measured: %+v", d.Phase, d.Name, d)
		}
		if d.Phase == "after-settle" && d.Name != "attribute-filter" && d.NonEmpty != 3 {
			t.Fatalf("direct read %s did not find acknowledged data: %+v", d.Name, d)
		}
		if d.Phase == "after-settle" && d.Name != "attribute-filter" && (d.PayloadBytes == nil || d.PayloadBytes.Min <= 0) {
			t.Fatalf("direct read %s did not read the span payloads: %+v", d.Name, d.PayloadBytes)
		}
	}
	t.Logf("real ClickHouse %s: 20 attempted/acknowledged/distinct persisted spans, run_id=%s; small functional smoke, NOT a throughput capacity benchmark", r.Provenance.ClickHouse.Version, c.RunID)
}

// B11 on real ClickHouse: a real collector with a tiny row budget is driven
// into backpressure (retryable whole-request refusals) and sent requests larger
// than its whole budget (non-retryable), over HTTP and gRPC. The independent
// verification must find every acknowledged span and none of the refused ones.
func TestLiveClickHouseBackpressureAccounting(t *testing.T) {
	lt := requireLiveTarget(t)
	results := map[string]Result{}
	for _, tc := range []struct {
		encoding string
		gzip     bool
	}{{EncodingProtobuf, false}, {EncodingJSON, true}, {EncodingGRPC, true}} {
		name := fmt.Sprintf("%s-gzip-%t", tc.encoding, tc.gzip)
		t.Run(name, func(t *testing.T) {
			results[name+"/backpressure"] = runBackpressureCase(t, lt.url, lt.headers, lt.writer, tc.encoding, tc.gzip, lt.apply)
			results[name+"/oversize"] = runOversizeCase(t, lt.url, lt.headers, lt.writer, tc.encoding, tc.gzip, lt.apply)
		})
	}
	writeLiveArtifact(t, "OTLPBENCH_LIVE_BACKPRESSURE_RESULT", results)
}
