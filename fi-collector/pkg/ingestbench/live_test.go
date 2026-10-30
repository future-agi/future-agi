package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
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

// fixtureWriter keeps every failure artifact in the test's own temporary
// directory, never in the production writer's default /var/lib path.
func (lt liveTarget) fixtureWriter(t *testing.T) *chwriter.Writer {
	t.Helper()
	cfg := lt.writer
	cfg.DeadLetterFile = t.TempDir() + "/dead-letter.jsonl"
	writer, err := chwriter.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { writer.Close() })
	return writer
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
	// v3 on real ClickHouse: the per-span insert lag SQL, the clock probe, the
	// time-bounded sentinel polls and the settings/storage provenance queries.
	if d := r.Verification.DurableLag; d.Status != "measured" || d.Rows != 20 || d.Lag.Count+int64(d.NegativeRows) != 20 || !d.ClockMeasured {
		t.Fatalf("durable lag on real ClickHouse: %+v", d)
	}
	// Every acknowledged request carries a sentinel here (stride 1); at least
	// one must be seen through the time-bounded poll. One that becomes visible
	// only after the settle window is reported as unseen, not as a lag: on a
	// slow host that is a measurement, not a failure.
	if l := r.Lag; l.Stride != 1 || l.Sampled != 5 || l.FromScheduled.Count == 0 || l.FromScheduled.Count+int64(l.Unseen) != int64(l.Sampled) ||
		l.LowerFromScheduled.Count != l.FromScheduled.Count || l.QueryErrors != 0 {
		t.Fatalf("read-visible sentinels on real ClickHouse: %+v", l)
	}
	if p := r.Provenance.ClickHouse; len(p.CreateTableSHA256) != 64 || p.SessionInsertSettings["async_insert"] == "" || p.MergeTreeSettings["fsync_after_insert"] == "" ||
		p.StorageBefore == nil || p.StorageBefore.Error != "" || p.StorageAfter == nil || p.StorageAfter.Error != "" || p.StorageAfter.Rows < p.StorageBefore.Rows {
		t.Fatalf("v3 provenance on real ClickHouse: %+v settings error %q", p, p.SettingsError)
	}
	t.Logf("real ClickHouse lag: %d of %d sentinels seen; read-visible upper p50/p99 %.0f/%.0f ms (lower p50 %.0f ms) from the scheduled send; insert lag p50/p99 %.0f/%.0f ms over %d stored spans; clock offset %.3f +/- %.3f ms",
		r.Lag.FromScheduled.Count, r.Lag.Sampled, r.Lag.FromScheduled.P50, r.Lag.FromScheduled.P99, r.Lag.LowerFromScheduled.P50, r.Verification.DurableLag.Lag.P50, r.Verification.DurableLag.Lag.P99,
		r.Verification.DurableLag.Rows, r.Clock.Start.OffsetMS, r.Clock.Start.UncertaintyMS)
	if p := r.Provenance.ClickHouse; p.Version == "" || p.TableEngine == "" || p.Hardware.Error != "" || p.Hardware.OSMemoryBytes == 0 || p.Hardware.MaxThreads == "" {
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

// B11 on real ClickHouse: a real collector with a tiny row budget behind a slow
// insert path is driven into backpressure (retryable whole-request refusals) and
// sent requests larger than its whole budget (admitted into an empty buffer,
// refused as retryable while over it), over HTTP and gRPC. The independent
// verification must find every acknowledged span and none of the refused ones.
func TestLiveClickHouseBackpressureAccounting(t *testing.T) {
	lt := requireLiveTarget(t)
	results := map[string]Result{}
	for _, tc := range []struct {
		encoding string
		gzip     bool
	}{{EncodingProtobuf, false}, {EncodingJSON, true}, {EncodingGRPC, false}} {
		name := fmt.Sprintf("%s-gzip-%t", tc.encoding, tc.gzip)
		t.Run(name, func(t *testing.T) {
			results[name+"/backpressure"] = runBackpressureCase(t, lt.url, lt.headers, lt.writer, tc.encoding, tc.gzip, lt.apply)
			results[name+"/over-budget"] = runOverBudgetCase(t, lt.url, lt.headers, lt.writer, tc.encoding, tc.gzip, lt.apply)
		})
	}
	writeLiveArtifact(t, "OTLPBENCH_LIVE_BACKPRESSURE_RESULT", results)
}

// BM-T01 fallibility on real ClickHouse: 3 rows of acknowledged spans never
// reach storage and 3 spans are stored twice (by a proxy in front of the real
// server); count() stays 20 but the exact-ID SQL must report 3 missing and 3 duplicates.
func TestLiveClickHouseVerifierCountsDroppedAndDuplicatedSpans(t *testing.T) {
	lt := requireLiveTarget(t)
	r := runTamperCase(t, lt.url, lt.headers, lt.writer, lt.apply)
	writeLiveArtifact(t, "OTLPBENCH_LIVE_TAMPER_RESULT", r)
	t.Logf("real ClickHouse %s: missing %d (acknowledged-but-missing %d), duplicate rows %d, distinct %d of %d, run_id=%s",
		r.Provenance.ClickHouse.Version, r.Verification.Missing, r.Verification.MissingBy.AcknowledgedMissing, r.Verification.DuplicateRows,
		r.Verification.DistinctExpected, r.Counters.Attempted, r.Config.RunID)
}

// F3 on real ClickHouse: the durable lag's server-side bucketing (SQL) puts
// every value in the same bucket as the Go histogram, including the exact and
// log-linear edges and the clamp.
func TestLiveClickHouseLatencyBucketsMatchGo(t *testing.T) {
	lt := requireLiveTarget(t)
	values := []int64{0, 1, 127, 255, 256, 257, 258, 511, 512, 513, 1000, 1023, 1024, 4095, 4096, 65535, 65536, 999_999, 1_000_000,
		10_000_000, 10_001_000, 3_600_000_000, 86_400_000_000, 1<<37 - 1, 1 << 37, 1 << 40}
	for e := 9; e < 40; e++ {
		values = append(values, int64(1)<<e-1, int64(1)<<e, int64(1)<<e+1)
	}
	list := make([]string, len(values))
	for i, v := range values {
		list[i] = strconv.FormatInt(v, 10)
	}
	c := testConfig()
	c.ClickHouseURL, c.Database, c.Table = lt.url, lt.database, lt.table
	ch := clickHouse{cfg: c, client: c.readClient(), headers: lt.headers}
	body, err := ch.queryWith(context.Background(), "WITH "+latencyExponentSQL+" SELECT lag_us, "+latencyBucketSQL+" AS b FROM (SELECT toInt64(arrayJoin(["+strings.Join(list, ",")+"])) AS lag_us) ORDER BY lag_us", nil, c.controlLimits())
	if err != nil {
		t.Fatal(err)
	}
	d := json.NewDecoder(bytes.NewReader(body))
	n := 0
	for {
		var row struct {
			Lag *int64 `json:"lag_us"`
			B   *int64 `json:"b"`
		}
		if err := d.Decode(&row); err == io.EOF {
			break
		} else if err != nil || row.Lag == nil || row.B == nil {
			t.Fatalf("bucket response: %v %s", err, body)
		}
		if want := int64(latencyBucketMicros(*row.Lag)); *row.B != want {
			t.Fatalf("lag %d us: ClickHouse bucket %d, Go bucket %d", *row.Lag, *row.B, want)
		}
		n++
	}
	if n != len(values) {
		t.Fatalf("%d of %d values bucketed", n, len(values))
	}
	t.Logf("ClickHouse and Go agree on the bucket of all %d probe values", n)
}

// M2 on real ClickHouse: a CI-sized recovery case. The real collector is
// restarted mid-run, and separately every writer request to ClickHouse is held
// for 1.5 s (a paused server); the exact-ID verification must find every
// acknowledged span, the backlog must drain and each fault must recover within
// the run. On a slow shared host (OTLPBENCH_LIVE_SLOW_HOST=1) a graceful stop
// waits for in-flight inserts, so the load is longer; assertions are the same.
func TestLiveClickHouseRecovery(t *testing.T) {
	lt := requireLiveTarget(t)
	results := map[string]Result{}
	scale := func(c Config) Config {
		if lt.slow {
			c.Duration, c.MaxDuration = 6*time.Minute, 10*time.Minute
		}
		return c
	}
	faultAt := func(at, d time.Duration) (time.Duration, time.Duration) {
		if lt.slow {
			return 30 * time.Second, 10 * time.Second
		}
		return at, d
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Hour)
	defer cancel()
	t.Run("collector-restart", func(t *testing.T) {
		collector := newRestartableCollector(t, lt.writer, server.Config{IngestBudgetMiB: 16})
		c := scale(lt.apply(recoveryConfig(collector)))
		c.RunID, c.ClickHouseURL = NewRunID(), lt.url
		at, d := faultAt(1500*time.Millisecond, 800*time.Millisecond)
		r, err := Run(ctx, c, Options{ClickHouseHeaders: lt.headers, Faults: []Fault{{Name: "collector-restart", At: at, Duration: d,
			Inject: func(context.Context) error { return collector.stop() }, Restore: func(context.Context) error { return collector.start() }}}})
		results["collector-restart"] = r
		if err != nil {
			t.Fatal(err)
		}
		requireRecovered(t, r, "collector-restart")
	})
	t.Run("clickhouse-pause", func(t *testing.T) {
		front := newPauseProxy(t, lt.url)
		defer front.Close()
		proxy := front.Config.Handler.(*pauseProxy)
		writer := lt.writer
		writer.URL = front.URL
		collector := newRestartableCollector(t, writer, server.Config{IngestBudgetMiB: 2, IngestMaxWait: time.Millisecond, InsertConcurrency: 1})
		c := scale(lt.apply(recoveryConfig(collector)))
		c.RunID, c.ClickHouseURL = NewRunID(), lt.url
		at, d := faultAt(time.Second, 1500*time.Millisecond)
		r, err := Run(ctx, c, Options{ClickHouseHeaders: lt.headers, Faults: []Fault{{Name: "clickhouse-pause", At: at, Duration: d, Inject: proxy.pause, Restore: proxy.resume}}})
		results["clickhouse-pause"] = r
		if err != nil {
			t.Fatal(err)
		}
		requireRecovered(t, r, "clickhouse-pause")
		if proxy.held.Load() == 0 {
			t.Fatal("no writer request was held by the pause")
		}
	})
	writeLiveArtifact(t, "OTLPBENCH_LIVE_RECOVERY_RESULT", results)
}
