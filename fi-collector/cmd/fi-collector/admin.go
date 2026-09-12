package main

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"runtime"
	"strings"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
)

// runAdmin serves /healthz and /metrics on the internal admin listener.
func runAdmin(addr string, w *chwriter.Writer, log *slog.Logger) {
	srv := &http.Server{Addr: addr, Handler: adminMux(w), ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Warn("admin server stopped", "err", err)
	}
}

func adminMux(w *chwriter.Writer) *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(rw http.ResponseWriter, r *http.Request) {
		s := w.Snapshot()
		denom := s.BatchesInserted + s.BatchesFailed
		if denom > 100 && s.BatchesFailed*2 > denom {
			rw.WriteHeader(http.StatusServiceUnavailable)
			_ = json.NewEncoder(rw).Encode(map[string]any{"status": "unhealthy", "stats": s})
			return
		}
		rw.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(rw).Encode(map[string]any{"status": "ok", "stats": s})
	})
	mux.HandleFunc("/metrics", func(rw http.ResponseWriter, r *http.Request) {
		writePrometheusMetrics(rw, w.Snapshot())
	})
	return mux
}

func writePrometheusMetrics(rw http.ResponseWriter, s chwriter.Stats) {
	var mem runtime.MemStats
	runtime.ReadMemStats(&mem)
	rw.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	var b strings.Builder
	counter := func(name, help string, v uint64) {
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s counter\n%s %d\n", name, help, name, name, v)
	}
	gauge := func(name, help string, v uint64) {
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s gauge\n%s %d\n", name, help, name, name, v)
	}
	counter("fi_collector_batches_inserted_total", "ClickHouse span batches inserted successfully.", s.BatchesInserted)
	counter("fi_collector_rows_inserted_total", "ClickHouse span rows inserted successfully.", s.RowsInserted)
	counter("fi_collector_batches_retried_total", "ClickHouse span batches that required a retry.", s.BatchesRetried)
	counter("fi_collector_rows_dead_lettered_total", "Span rows written to the dead-letter file.", s.RowsDeadLettered)
	counter("fi_collector_batches_failed_total", "ClickHouse span batches that exhausted retries.", s.BatchesFailed)
	counter("fi_collector_curated_batches_inserted_total", "Best-effort curated-dimension batches inserted.", s.CuratedBatchesInserted)
	counter("fi_collector_curated_batches_failed_total", "Best-effort curated-dimension batches that failed.", s.CuratedBatchesFailed)
	gauge("go_goroutines", "Number of goroutines that currently exist.", uint64(runtime.NumGoroutine()))
	gauge("go_memstats_alloc_bytes", "Number of bytes allocated and still in use.", mem.Alloc)
	gauge("go_memstats_sys_bytes", "Number of bytes obtained from the system.", mem.Sys)
	_, _ = rw.Write([]byte(b.String()))
}
