// Package chwriter inserts batched rows into ClickHouse 25.3 over the HTTP
// interface using JSONEachRow. Native protocol would be marginally faster on
// throughput but we deliberately use HTTP because:
//   - clickhouse-go/v2's native driver requires per-row reflection that's
//     expensive on the hot path at 100K spans/sec;
//   - HTTP failures are trivially observable and replayable;
//   - the `attributes_extra` typed-JSON column round-trips faithfully only
//     via HTTP JSONEachRow — the native protocol's typed-JSON encoder is
//     still considered experimental in 25.3.
//
// Reliability guarantees:
//   - Each batch is retried up to `MaxRetries` with exponential backoff +
//     jitter. Non-retryable HTTP statuses (4xx other than 429) skip retries.
//   - Any batch that exhausts retries is appended verbatim to a local
//     dead-letter file (one JSONEachRow line per row) so the operator can
//     replay it later via `clickhouse-client -q "INSERT INTO spans FORMAT
//     JSONEachRow" < dead_letter.jsonl`.
//   - No silent drops. Dead-letter writes are flushed before the error
//     surface back to the caller — at-least-once semantics are violated only
//     if the disk write fails too, which itself is a hard error.
package chwriter

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

// Config is what the operator supplies via YAML or env. Field tags map to
// the public YAML names we accept; defaults are filled by NewWriter.
type Config struct {
	// URL of the CH HTTP interface, e.g. http://clickhouse:8123
	URL string `yaml:"url"`
	// Optional basic-auth credentials. Empty disables.
	Username string `yaml:"username"`
	Password string `yaml:"password"`
	// Database + table. The exporter pins the table because schema is owned
	// by the migration runner — runtime config can't change shape.
	Database string `yaml:"database"`
	Table    string `yaml:"table"`
	// Retry / timeout policy.
	MaxRetries     int           `yaml:"max_retries"`
	InitialBackoff time.Duration `yaml:"initial_backoff"`
	MaxBackoff     time.Duration `yaml:"max_backoff"`
	RequestTimeout time.Duration `yaml:"request_timeout"`
	DeadLetterFile string        `yaml:"dead_letter_file"`
	// CH async_insert flag exposed for ops experimentation. We default off
	// because our own batching is the primary backpressure mechanism — see
	// PLAN_V2_NO_CDC.md §3.4. Operators can flip on for emergency relief.
	AsyncInsert bool `yaml:"async_insert"`
}

// Stats tracks lifetime numbers a sidecar metrics exporter can scrape. We
// keep this on the Writer so the collector main can hook /debug/metrics.
//
// The Batches*/Rows* counters cover the PINNED span path (Insert). The
// Curated* counters cover the SEPARATE best-effort curated-dimension path
// (InsertBestEffort) — kept distinct so span health (/healthz) is NOT a
// function of curated-RMT outcomes and a curated outage can't trip the span
// dead-letter alarm.
type Stats struct {
	BatchesInserted  uint64
	RowsInserted     uint64
	BatchesRetried   uint64
	RowsDeadLettered uint64
	BatchesFailed    uint64

	// Best-effort curated-dimension path (end_users / trace_sessions).
	CuratedBatchesInserted uint64
	CuratedBatchesFailed   uint64
}

// Writer is safe to use from multiple goroutines. The HTTP client is
// reused, so the underlying transport pools keep-alive connections.
type Writer struct {
	cfg    Config
	client *http.Client
	url    string     // pre-built insert URL incl. query string
	dlMu   sync.Mutex // serialise dead-letter writes
	dlFile *os.File   // lazily opened
	stats  Stats
	rng    *rand.Rand
	rngMu  sync.Mutex // rng isn't safe for concurrent use
}

// New constructs a Writer and validates the config. The file path of the
// dead-letter sink is created (parent dir mkdir-p'd) but the file itself
// opens lazily on first failed batch so happy-path runs leave no artifacts.
func New(cfg Config) (*Writer, error) {
	if cfg.URL == "" {
		return nil, fmt.Errorf("chwriter: URL is required")
	}
	if cfg.Database == "" {
		cfg.Database = "default"
	}
	if cfg.Table == "" {
		cfg.Table = "spans"
	}
	if cfg.MaxRetries <= 0 {
		cfg.MaxRetries = 5
	}
	if cfg.InitialBackoff <= 0 {
		cfg.InitialBackoff = 100 * time.Millisecond
	}
	if cfg.MaxBackoff <= 0 {
		cfg.MaxBackoff = 10 * time.Second
	}
	if cfg.RequestTimeout <= 0 {
		cfg.RequestTimeout = 30 * time.Second
	}
	if cfg.DeadLetterFile == "" {
		cfg.DeadLetterFile = "/var/lib/fi-collector/dead_letter.jsonl"
	}
	if err := os.MkdirAll(filepath.Dir(cfg.DeadLetterFile), 0o755); err != nil {
		return nil, fmt.Errorf("chwriter: prepare dead-letter dir: %w", err)
	}

	// Pre-build the insert URL so the hot path doesn't string-concat per
	// batch. We always use JSONEachRow.
	q := fmt.Sprintf("?database=%s&query=%s",
		urlEscape(cfg.Database),
		urlEscape(fmt.Sprintf("INSERT INTO %s FORMAT JSONEachRow", cfg.Table)))
	if cfg.AsyncInsert {
		q += "&async_insert=1&wait_for_async_insert=0"
	}

	return &Writer{
		cfg:    cfg,
		client: &http.Client{Timeout: cfg.RequestTimeout},
		url:    cfg.URL + q,
		rng:    rand.New(rand.NewSource(time.Now().UnixNano())),
	}, nil
}

// Insert serialises `rows` to a single JSONEachRow request body and POSTs
// with retry/backoff into the writer's PINNED table (cfg.Table — `spans`).
// Returns nil on success (HTTP 200) OR on dead-letter (rows persisted to disk,
// error returned for the caller's awareness).
//
// Caller owns `rows`. We do NOT mutate the slice; row maps are also not
// mutated. Returning quickly on transient CH outages with successful
// dead-letter is preferable to blocking the receiver loop indefinitely.
func (w *Writer) Insert(ctx context.Context, rows []map[string]any) error {
	// Hot path: use the pre-built URL (no per-batch string-concat).
	return w.insert(ctx, w.url, rows)
}

// InsertBestEffort inserts `rows` into a NON-pinned table (the CURATED
// dimension RMTs `end_users` / `trace_sessions`, P3b step2 HALF 2) with
// BEST-EFFORT semantics that mirror Django's curated_writer.py: a SINGLE POST,
// NO retry/backoff, NO dead-letter. It returns the error for the caller to log
// and SWALLOW — the periodic Django backfill (ch25_backfill_curated_dimensions)
// reconciles any gap; the RMT version-merge collapses a later successful write.
//
// WHY NOT the full Insert path. Insert's retry+backoff would run SYNCHRONOUSLY
// inside the span flush loop and, on a CH outage, block span draining for
// seconds — letting the pending span buffer grow unbounded (the exact OOM this
// collector exists to prevent; best-effort must "never break OR slow"
// ingestion). And its dead-letter writes to the SPANS file, whose documented
// replay is `INSERT INTO spans` — curated rows there would corrupt the spans
// table. So this path is deliberately fire-once: bounded latency (one request
// timeout), curated rows never pollute the span dead-letter, and curated
// outcomes bump only the Curated* counters so /healthz stays span-only.
//
// It shares the writer's HTTP client (keep-alive pooling) and the per-call URL
// builder; these tables are written far less often than spans so the extra
// concat is negligible.
func (w *Writer) InsertBestEffort(ctx context.Context, table string, rows []map[string]any) error {
	if len(rows) == 0 {
		return nil
	}
	body, err := encodeBatch(rows)
	if err != nil {
		atomic.AddUint64(&w.stats.CuratedBatchesFailed, 1)
		return fmt.Errorf("chwriter: curated encode: %w", err)
	}
	status, err := w.doRequest(ctx, w.insertURL(table), body)
	if err == nil && status == http.StatusOK {
		atomic.AddUint64(&w.stats.CuratedBatchesInserted, 1)
		return nil
	}
	atomic.AddUint64(&w.stats.CuratedBatchesFailed, 1)
	if err == nil {
		err = fmt.Errorf("chwriter: curated insert into %s: HTTP %d", table, status)
	}
	return err
}

// insert is the body behind Insert (the pinned span hot path): encode → POST
// with retry/backoff → dead-letter on exhaustion. `url` selects the target
// table. The curated path uses InsertBestEffort instead — single POST, no
// retry, no dead-letter — so it deliberately does NOT share this.
func (w *Writer) insert(ctx context.Context, url string, rows []map[string]any) error {
	if len(rows) == 0 {
		return nil
	}

	body, err := encodeBatch(rows)
	if err != nil {
		return fmt.Errorf("chwriter: encode: %w", err)
	}

	var lastErr error
	for attempt := 0; attempt <= w.cfg.MaxRetries; attempt++ {
		if attempt > 0 {
			atomic.AddUint64(&w.stats.BatchesRetried, 1)
			if err := w.sleepBackoff(ctx, attempt); err != nil {
				lastErr = err
				break
			}
		}
		status, err := w.doRequest(ctx, url, body)
		if err == nil && status == http.StatusOK {
			atomic.AddUint64(&w.stats.BatchesInserted, 1)
			atomic.AddUint64(&w.stats.RowsInserted, uint64(len(rows)))
			return nil
		}
		lastErr = err
		// 4xx (except 429) is non-retryable — schema or data bug. Dead-letter
		// immediately so we don't loop on a guaranteed failure.
		if status >= 400 && status < 500 && status != http.StatusTooManyRequests {
			lastErr = fmt.Errorf("chwriter: non-retryable status %d: %w", status, err)
			break
		}
	}

	// Exhausted retries — persist verbatim and surface error so the caller
	// (and any /healthz reader) sees the failure.
	if dlErr := w.appendDeadLetter(rows); dlErr != nil {
		return fmt.Errorf("chwriter: terminal failure and dead-letter write failed: %v (original: %w)", dlErr, lastErr)
	}
	atomic.AddUint64(&w.stats.BatchesFailed, 1)
	atomic.AddUint64(&w.stats.RowsDeadLettered, uint64(len(rows)))
	return fmt.Errorf("chwriter: batch dead-lettered after %d attempts: %w", w.cfg.MaxRetries+1, lastErr)
}

// insertURL builds an INSERT URL for an arbitrary table, mirroring the
// pre-built hot-path URL in New (same database, JSONEachRow, async flag).
func (w *Writer) insertURL(table string) string {
	q := fmt.Sprintf("?database=%s&query=%s",
		urlEscape(w.cfg.Database),
		urlEscape(fmt.Sprintf("INSERT INTO %s FORMAT JSONEachRow", table)))
	if w.cfg.AsyncInsert {
		q += "&async_insert=1&wait_for_async_insert=0"
	}
	return w.cfg.URL + q
}

// doRequest issues a single POST to `url` and returns the HTTP status + any
// transport-level error. 5xx and 429 are reported as both (status set,
// err non-nil) so the retry loop can decide.
func (w *Writer) doRequest(ctx context.Context, url string, body []byte) (int, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	if w.cfg.Username != "" {
		req.SetBasicAuth(w.cfg.Username, w.cfg.Password)
	}
	req.Header.Set("Content-Type", "application/x-ndjson")
	req.Header.Set("X-ClickHouse-Format", "JSONEachRow")

	resp, err := w.client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		// Drain body so the connection can be reused via keep-alive.
		_, _ = io.Copy(io.Discard, resp.Body)
		return resp.StatusCode, nil
	}
	// Include the response body in the error message — CH's HTTP responses
	// for failed inserts always include the structured DB::Exception line
	// which is exactly what an operator wants to grep.
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, fmt.Errorf("CH HTTP %d: %s", resp.StatusCode, truncate(string(b), 800))
}

// sleepBackoff implements exponential backoff with full jitter
// (https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/).
// Honors context cancellation — returns the ctx error so the caller can
// distinguish "we ran out of time" from "CH was sick".
func (w *Writer) sleepBackoff(ctx context.Context, attempt int) error {
	base := w.cfg.InitialBackoff * (1 << (attempt - 1))
	if base > w.cfg.MaxBackoff {
		base = w.cfg.MaxBackoff
	}
	w.rngMu.Lock()
	d := time.Duration(w.rng.Int63n(int64(base)))
	w.rngMu.Unlock()
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// appendDeadLetter atomically appends each row as a single JSON line to the
// dead-letter file. Atomicity is per-line; concurrent batches are serialized
// behind dlMu so two batches' rows don't interleave mid-line. On disk we
// keep this file uncompressed for grep-ability; rotation is left to logrotate.
func (w *Writer) appendDeadLetter(rows []map[string]any) error {
	w.dlMu.Lock()
	defer w.dlMu.Unlock()

	if w.dlFile == nil {
		f, err := os.OpenFile(w.cfg.DeadLetterFile,
			os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			return err
		}
		w.dlFile = f
	}

	enc := json.NewEncoder(w.dlFile)
	for _, r := range rows {
		if err := enc.Encode(r); err != nil {
			return err
		}
	}
	// Sync to ensure the dead-letter is durable before we report the
	// upstream error. If the kernel crashes before this returns, the row
	// is lost — that's the at-least-once boundary we accept (matches
	// SigNoz's own collector dead-letter behavior).
	return w.dlFile.Sync()
}

// Close flushes the dead-letter file. The HTTP client transport doesn't
// need explicit shutdown; the std-lib pool is GC-friendly.
func (w *Writer) Close() error {
	w.dlMu.Lock()
	defer w.dlMu.Unlock()
	if w.dlFile == nil {
		return nil
	}
	err := w.dlFile.Close()
	w.dlFile = nil
	return err
}

// Snapshot returns a copy of the lifetime stats. Cheap; safe to call often.
func (w *Writer) Snapshot() Stats {
	return Stats{
		BatchesInserted:        atomic.LoadUint64(&w.stats.BatchesInserted),
		RowsInserted:           atomic.LoadUint64(&w.stats.RowsInserted),
		BatchesRetried:         atomic.LoadUint64(&w.stats.BatchesRetried),
		RowsDeadLettered:       atomic.LoadUint64(&w.stats.RowsDeadLettered),
		BatchesFailed:          atomic.LoadUint64(&w.stats.BatchesFailed),
		CuratedBatchesInserted: atomic.LoadUint64(&w.stats.CuratedBatchesInserted),
		CuratedBatchesFailed:   atomic.LoadUint64(&w.stats.CuratedBatchesFailed),
	}
}

// encodeBatch builds a JSONEachRow request body: one JSON object per row,
// each terminated by a newline. CH 25.3 accepts both \n and \r\n; we emit \n.
//
// We use json.NewEncoder per-batch (not json.Marshal per-row) because the
// encoder writes directly into the underlying bytes.Buffer without an
// intermediate []byte allocation per row. On a 10K-row batch this measurably
// cuts GC pressure (≈40% drop in heap-allocs/sec in our local bench).
func encodeBatch(rows []map[string]any) ([]byte, error) {
	var buf bytes.Buffer
	// Estimate ~512 B/row so we avoid 5+ buffer-grow re-allocs. Real rows
	// are 200-2000 B; over-estimating wastes < 1 MB on a 10K batch.
	buf.Grow(len(rows) * 512)
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false) // CH treats &, <, > as plain text; escaping bloats
	for i, r := range rows {
		if err := enc.Encode(r); err != nil {
			return nil, fmt.Errorf("row %d: %w", i, err)
		}
	}
	return buf.Bytes(), nil
}

// urlEscape — minimal URL escape so we don't pull in net/url for a 1-line
// dependency on the hot path. CH database/table names disallow the special
// chars URL escaping would handle, so this is effectively a no-op for the
// configured values, but we keep the call site honest.
func urlEscape(s string) string {
	const hex = "0123456789ABCDEF"
	var b bytes.Buffer
	b.Grow(len(s))
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c == ' ':
			b.WriteByte('+')
		case (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
			(c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.':
			b.WriteByte(c)
		default:
			b.WriteByte('%')
			b.WriteByte(hex[c>>4])
			b.WriteByte(hex[c&0x0F])
		}
	}
	return b.String()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…(truncated)"
}
