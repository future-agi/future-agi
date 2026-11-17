package ingestbench

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestLiveClickHouseReusePreflightChecksLaterTraces(t *testing.T) {
	lt := requireLiveTarget(t)
	c := lt.apply(mockRunConfig())
	c.RunID, c.ClickHouseURL = NewRunID(), lt.url
	c.Rate, c.SpansPerTrace = 68, 1
	c.DirectProbes.Enabled = false
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	ch := clickHouse{cfg: c, client: c.readClient(), headers: lt.headers}
	defer ch.client.CloseIdleConnections()
	if n, err := ch.preflight(ctx); err != nil || n != 0 {
		t.Fatalf("fresh run preflight: %d %v", n, err)
	}
	writer := lt.fixtureWriter(t)
	rows := []map[string]any{}
	for seq := int64(64); seq < 68; seq++ {
		rows = append(rows, storedRow(c, seq, time.Now().Add(-10*time.Second)))
	}
	if err := writer.Insert(ctx, rows); err != nil {
		t.Fatal(err)
	}
	var sent atomic.Int64
	collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { sent.Add(1) }))
	defer collector.Close()
	c.Endpoint = collector.URL
	r, err := Run(ctx, c, Options{ClickHouseHeaders: lt.headers})
	if err == nil || !strings.Contains(err.Error(), "already has") || r.Verification.PreexistingRunRows == 0 || sent.Load() != 0 || r.Counters.Attempted != 0 {
		t.Fatalf("later stored identities did not prevent reuse: err=%v existing=%d requests=%d attempted=%d", err, r.Verification.PreexistingRunRows, sent.Load(), r.Counters.Attempted)
	}
	t.Logf("real ClickHouse %s: only sequences 64-67 stored; reuse refused before any export, run_id=%s", r.Provenance.ClickHouse.Version, c.RunID)
}
