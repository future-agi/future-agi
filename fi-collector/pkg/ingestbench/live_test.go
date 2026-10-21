package ingestbench

import (
	"context"
	"net/http"
	"os"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
)

// This opt-in test performs real inserts into an already provisioned, isolated
// v2 schema. It creates/deletes no infrastructure and never targets remote hosts.
// The production receiver/converter/writer run in process without PG auth;
// authenticated external-collector coverage belongs to benchmark/run-profile.sh.
func TestLiveClickHouseCollectorPipeline(t *testing.T) {
	url := os.Getenv("OTLPBENCH_LIVE_CH_URL")
	if url == "" {
		t.Skip("live ClickHouse not configured; mock tests are NOT ClickHouse evidence")
	}
	if os.Getenv("OTLPBENCH_LIVE_CONFIRM") != "isolated-test-stack" {
		t.Fatal("set OTLPBENCH_LIVE_CONFIRM=isolated-test-stack after verifying the target")
	}
	c := testConfig()
	c.RunID, c.ClickHouseURL = NewRunID(), url
	c.Execute, c.AllowUnauth = true, true
	c.Environment = "isolated-real-ClickHouse-inprocess-collector-auth-bypassed"
	c.Rate, c.Duration, c.BatchSize, c.VerifyChunk = 20, time.Second, 4, 7
	c.Settle, c.PollInterval, c.MaxBytes = time.Second, 100*time.Millisecond, 1<<20
	if db := os.Getenv("OTLPBENCH_LIVE_CH_DATABASE"); db != "" {
		c.Database = db
	}
	if table := os.Getenv("OTLPBENCH_LIVE_CH_TABLE"); table != "" {
		c.Table = table
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	user, password := os.Getenv("OTLPBENCH_LIVE_CH_USER"), os.Getenv("OTLPBENCH_LIVE_CH_PASSWORD")
	c.Endpoint = startCollectorWithWriterConfig(t, chwriter.Config{URL: url, Database: c.Database, Table: c.Table, Username: user, Password: password})
	headers := http.Header{}
	if user != "" {
		headers.Set("X-ClickHouse-User", user)
	}
	if password != "" {
		headers.Set("X-ClickHouse-Key", password)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	r, err := Run(ctx, c, Options{ClickHouseHeaders: headers})
	if path := os.Getenv("OTLPBENCH_LIVE_RESULT"); path != "" {
		f, fileErr := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if fileErr != nil {
			t.Fatal(fileErr)
		}
		if fileErr = WriteResult(f, r); fileErr != nil {
			t.Fatal(fileErr)
		}
		f.Close()
	}
	if err != nil {
		t.Fatal(err)
	}
	if r.Status != "measured-profile-complete" || r.Verification.DistinctExpected != 20 || r.Lag.Latency.Count == 0 {
		t.Fatalf("real pipeline verification failed: status=%s counters=%+v verification=%+v lag=%+v", r.Status, r.Counters, r.Verification, r.Lag)
	}
	t.Logf("real ClickHouse: 20 attempted/acknowledged/distinct persisted spans, run_id=%s; small functional smoke, NOT a throughput capacity benchmark", c.RunID)
}
