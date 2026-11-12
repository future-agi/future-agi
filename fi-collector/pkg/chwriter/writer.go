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
//     An unconfirmed HTTP 200 also skips retries: the canonical insert may
//     already have committed. Reconcile the source before any replay; do not
//     hand off observations merely because the response status was 200.
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
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"

	"golang.org/x/sync/semaphore"
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
	// maxInsertBytes, when positive, splits a span batch into inserts of
	// about this many bytes (SetMaxInsertBytes).
	maxInsertBytes int
	// inserts, when set, caps the span inserts in flight across every Insert
	// call, and lets one batch's inserts run at once (SetMaxConcurrentInserts).
	inserts *semaphore.Weighted
}

// SetMaxInsertBytes makes Insert send a batch as several inserts of about n
// bytes of JSON each (a single row larger than n is sent on its own), so that
// encoding a batch holds one insert's body at a time rather than the whole
// batch's. 0, the default, sends each batch as one insert. Call it before the
// writer is used.
func (w *Writer) SetMaxInsertBytes(n int) { w.maxInsertBytes = max(n, 0) }

// SetMaxConcurrentInserts lets up to n span inserts run at once, across all
// Insert calls: a batch split by SetMaxInsertBytes sends its inserts without
// waiting for the one before, while it encodes the next. Insert still returns
// only when every insert of its batch has ended. Each caller then holds at
// most one body it is encoding, and n bodies are in flight in all. 0 or 1,
// the default, sends one insert at a time per Insert call. Call it before
// the writer is used.
func (w *Writer) SetMaxConcurrentInserts(n int) {
	w.inserts = nil
	if n > 1 {
		w.inserts = semaphore.NewWeighted(int64(n))
	}
}

// New constructs a Writer and validates the config. The file path of the
// dead-letter sink is created (parent dir mkdir-p'd) but the file itself
// opens lazily on first failed batch so happy-path runs leave no artifacts.
func New(cfg Config) (*Writer, error) {
	if cfg.URL == "" {
		return nil, fmt.Errorf("chwriter: URL is required")
	}
	// Fail here rather than on every batch. Leave the URL and the parse error
	// out: url.Error echoes the URL verbatim, password included.
	if _, err := url.Parse(cfg.URL); err != nil {
		return nil, fmt.Errorf("chwriter: URL cannot be parsed (not shown: it may hold a password)")
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
	} else {
		q += "&async_insert=0"
	}

	return &Writer{
		cfg:    cfg,
		client: &http.Client{Timeout: cfg.RequestTimeout},
		url:    cfg.URL + q,
		rng:    rand.New(rand.NewSource(time.Now().UnixNano())),
	}, nil
}

// Insert serialises `rows` to JSONEachRow request bodies (one, or several of
// about SetMaxInsertBytes bytes each) and POSTs each with retry/backoff into
// the writer's PINNED table (cfg.Table — `spans`). Returns nil only when every
// insert got a complete, exception-free HTTP 200 response. Explicit
// AsyncInsert still means acceptance, not commit confirmation. An insert that
// fails dead-letters its own rows; Insert then returns an error, including
// for ambiguous inserts needing reconciliation, after sending the rest.
//
// Caller owns `rows`. We do NOT mutate the slice; row maps are also not
// mutated. Returning quickly on transient CH outages with successful
// dead-letter is preferable to blocking the receiver loop indefinitely.
func (w *Writer) Insert(ctx context.Context, rows []map[string]any) error {
	// Hot path: use the pre-built URL (no per-batch string-concat).
	return w.insert(ctx, w.url, rows, nil)
}

// InsertConfirmed has Insert's retry/dead-letter semantics, and additionally
// reports the exact input positions whose chunks completed synchronously with
// an exception-free response. Failed, unsent, ambiguous and async-accepted rows
// are false. An error does not invalidate confirmed sibling chunks. The slice
// is complete only on return, after all started inserts have finished; callers
// must retain their row leases through any sidecar handoff that follows.
func (w *Writer) InsertConfirmed(ctx context.Context, rows []map[string]any) ([]bool, error) {
	confirmed := make([]bool, len(rows))
	err := w.insert(ctx, w.url, rows, confirmed)
	return confirmed, err
}

// confirm marks disjoint chunk positions. Concurrent chunks never share a
// position, and insertConcurrently waits before publishing the result.
func (w *Writer) confirm(confirmed []bool, start, end int) {
	if confirmed == nil || w.cfg.AsyncInsert {
		return
	}
	for i := start; i < end; i++ {
		confirmed[i] = true
	}
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

// insert is the body behind Insert (the pinned span hot path): encode →
// POST with retry/backoff → dead-letter on exhaustion, one chunk of about
// maxInsertBytes at a time. `url` selects the target table. The curated path
// uses InsertBestEffort instead — single POST, no retry, no dead-letter — so
// it deliberately does NOT share this.
func (w *Writer) insert(ctx context.Context, url string, rows []map[string]any, confirmed []bool) error {
	if len(rows) == 0 {
		return nil
	}
	if w.inserts != nil {
		return w.insertConcurrently(ctx, url, rows, confirmed)
	}
	limit := w.maxInsertBytes
	// Estimate ~512 B/row so we avoid 5+ buffer-grow re-allocs; with chunks,
	// a chunk's worth.
	size := len(rows) * 512
	if limit > 0 {
		size = min(size, limit+limit/8)
	}
	var buf bytes.Buffer
	buf.Grow(size)
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false) // CH treats &, <, > as plain text; escaping bloats
	var firstErr error
	start := 0
	for i, r := range rows {
		if err := enc.Encode(r); err != nil {
			err = fmt.Errorf("chwriter: encode: row %d: %w", i, err)
			return errors.Join(firstErr, err)
		}
		if i == len(rows)-1 || (limit > 0 && buf.Len() >= limit) {
			unavailable, err := w.insertChunk(ctx, url, buf.Bytes(), rows[start:i+1])
			if err == nil {
				w.confirm(confirmed, start, i+1)
			}
			if err != nil && firstErr == nil {
				firstErr = err
			}
			if unavailable && i < len(rows)-1 {
				// ClickHouse did not take this insert after every retry
				// (or the drain was cancelled): each remaining insert
				// would wait as long. Dead-letter the rest of the batch
				// now, so the drain ends and its rows' memory is freed.
				return errors.Join(firstErr, w.deadLetterUnsent(rows[i+1:]))
			}
			// A new buffer, not Reset: when ClickHouse answers before it has
			// read a whole body, the HTTP transport may still be reading
			// that body after Do returns.
			buf = bytes.Buffer{}
			if i < len(rows)-1 {
				buf.Grow(size)
			}
			start = i + 1
		}
	}
	return firstErr
}

// insertConcurrently is insert with SetMaxConcurrentInserts: each insert of
// the batch is sent as soon as it is encoded and a slot is free, while the
// next is encoded. As in insert, once an insert gives up because ClickHouse
// is unavailable, the rows not sent yet are dead-lettered at once; inserts
// already in flight finish on their own (each dead-letters its own rows if it
// fails). It returns only when every insert it started has ended.
func (w *Writer) insertConcurrently(ctx context.Context, url string, rows []map[string]any, confirmed []bool) error {
	limit := w.maxInsertBytes
	size := len(rows) * 512
	if limit > 0 {
		size = min(size, limit+limit/8)
	}
	var (
		wg          sync.WaitGroup
		mu          sync.Mutex
		firstErr    error
		unavailable atomic.Bool
	)
	fail := func(err error) {
		mu.Lock()
		defer mu.Unlock()
		if firstErr == nil {
			firstErr = err
		}
	}
	defer wg.Wait()
	var buf bytes.Buffer
	buf.Grow(size)
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	start := 0
	for i, r := range rows {
		if err := enc.Encode(r); err != nil {
			wg.Wait()
			mu.Lock()
			defer mu.Unlock()
			return errors.Join(firstErr, fmt.Errorf("chwriter: encode: row %d: %w", i, err))
		}
		if i < len(rows)-1 && (limit <= 0 || buf.Len() < limit) {
			continue
		}
		part := rows[start : i+1]
		if unavailable.Load() || w.inserts.Acquire(ctx, 1) != nil {
			// ClickHouse did not take an earlier insert of the batch after
			// every retry, or the drain was cancelled: dead-letter what is
			// left, this insert's rows included, so the drain ends.
			wg.Wait()
			mu.Lock()
			defer mu.Unlock()
			return errors.Join(firstErr, w.deadLetterUnsent(rows[start:]))
		}
		body := buf.Bytes()
		first, end := start, i+1
		wg.Add(1)
		go func() {
			defer wg.Done()
			defer w.inserts.Release(1)
			gaveUp, err := w.insertChunk(ctx, url, body, part)
			if err != nil {
				fail(err)
			} else {
				w.confirm(confirmed, first, end)
			}
			if gaveUp {
				unavailable.Store(true)
			}
		}()
		// A new buffer: the one just sent is in flight.
		buf = bytes.Buffer{}
		if i < len(rows)-1 {
			buf.Grow(size)
		}
		start = i + 1
	}
	wg.Wait()
	mu.Lock()
	defer mu.Unlock()
	return firstErr
}

// deadLetterUnsent dead-letters rows that were never sent.
func (w *Writer) deadLetterUnsent(rows []map[string]any) error {
	if err := w.appendDeadLetter(rows); err != nil {
		return fmt.Errorf("chwriter: dead-letter write failed for %d unsent rows: %w", len(rows), err)
	}
	atomic.AddUint64(&w.stats.BatchesFailed, 1)
	atomic.AddUint64(&w.stats.RowsDeadLettered, uint64(len(rows)))
	return fmt.Errorf("chwriter: %d rows dead-lettered unsent: an earlier insert of the batch could not reach ClickHouse", len(rows))
}

// insertChunk POSTs one insert body with retry/backoff and dead-letters its
// rows if it cannot be confirmed. unavailable reports that it gave up
// because ClickHouse kept failing (transport errors, 429 or 5xx) or the
// context ended, not because of this body.
func (w *Writer) insertChunk(ctx context.Context, url string, body []byte, rows []map[string]any) (unavailable bool, err error) {
	var lastErr error
	attempts := 0
	unavailable = true
	for attempt := 0; attempt <= w.cfg.MaxRetries; attempt++ {
		if attempt > 0 {
			atomic.AddUint64(&w.stats.BatchesRetried, 1)
			if err := w.sleepBackoff(ctx, attempt); err != nil {
				lastErr = err
				break
			}
		}
		attempts++
		status, err := w.doRequest(ctx, url, body)
		if err == nil && status == http.StatusOK {
			atomic.AddUint64(&w.stats.BatchesInserted, 1)
			atomic.AddUint64(&w.stats.RowsInserted, uint64(len(rows)))
			return false, nil
		}
		lastErr = err
		if status == http.StatusOK {
			// A late exception or incomplete response does not prove that no
			// rows committed. Preserve the batch, but never add a source replay.
			lastErr = fmt.Errorf("chwriter: insert not confirmed; reconcile canonical source before replay: %w", err)
			unavailable = false
			break
		}
		// 4xx (except 429) is non-retryable — schema or data bug. Dead-letter
		// immediately so we don't loop on a guaranteed failure.
		if status >= 400 && status < 500 && status != http.StatusTooManyRequests {
			lastErr = fmt.Errorf("chwriter: non-retryable status %d: %w", status, err)
			unavailable = false
			break
		}
	}

	// Exhausted retries — persist verbatim and surface error so the caller
	// (and any /healthz reader) sees the failure.
	if dlErr := w.appendDeadLetter(rows); dlErr != nil {
		return unavailable, fmt.Errorf("chwriter: terminal failure and dead-letter write failed: %v (original: %w)", dlErr, lastErr)
	}
	atomic.AddUint64(&w.stats.BatchesFailed, 1)
	atomic.AddUint64(&w.stats.RowsDeadLettered, uint64(len(rows)))
	return unavailable, fmt.Errorf("chwriter: batch dead-lettered after %d attempts: %w", attempts, lastErr)
}

// insertURL builds an INSERT URL for an arbitrary table, mirroring the
// pre-built hot-path URL in New (same database, JSONEachRow, async flag).
func (w *Writer) insertURL(table string) string {
	q := fmt.Sprintf("?database=%s&query=%s",
		urlEscape(w.cfg.Database),
		urlEscape(fmt.Sprintf("INSERT INTO %s FORMAT JSONEachRow", table)))
	if w.cfg.AsyncInsert {
		q += "&async_insert=1&wait_for_async_insert=0"
	} else {
		q += "&async_insert=0"
	}
	return w.cfg.URL + q
}

// doRequest issues a single POST to `url` and returns the HTTP status + any
// transport/confirmation error. Preserve the status on response failures so
// insert can distinguish an unconfirmed 200 from existing retryable failures.
func (w *Writer) doRequest(ctx context.Context, url string, body []byte) (int, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	if w.cfg.Username != "" {
		req.SetBasicAuth(w.cfg.Username, w.cfg.Password)
	}
	req.Header.Set("Content-Type", "application/x-ndjson")
	req.Header.Set("X-ClickHouse-Format", "JSONEachRow")
	waitForBodies := trackRequestBodies(req)
	defer func() {
		// An early response can finish before the upload. Stop outstanding
		// transport work, then retain ownership until every body is closed.
		cancel()
		waitForBodies()
	}()

	resp, err := w.client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		// INSERT has no result body. Read through EOF before acknowledging;
		// ClickHouse can send an exception after the HTTP status is committed.
		// Bound unexpected responses and never include source payloads in errors.
		const maxResponse = 4096
		b, readErr := io.ReadAll(io.LimitReader(resp.Body, maxResponse+1))
		if readErr != nil || len(b) > maxResponse || (resp.ContentLength >= 0 && int64(len(b)) != resp.ContentLength) {
			return resp.StatusCode, fmt.Errorf("CH HTTP %d: incomplete or oversized insert response", resp.StatusCode)
		}
		if len(bytes.TrimSpace(b)) != 0 || resp.Header.Get("X-ClickHouse-Exception-Code") != "" || resp.Trailer.Get("X-ClickHouse-Exception-Code") != "" {
			return resp.StatusCode, fmt.Errorf("CH HTTP %d: insert response contains an exception or unexpected result", resp.StatusCode)
		}
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
	// Sync the dead-letter before reporting the upstream error. A crash before
	// this completes can lose these rows; this is not a durable pre-ack queue
	// and does not provide automatic replay or an exactly-once guarantee.
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

// Policy is the writer's insert mode and retry policy after defaults, without
// the URL, credentials or dead-letter path, so a benchmark report can disclose
// what "durable" meant for the run: with async_insert the writer does not wait
// for the flush (wait_for_async_insert=0), so an acknowledged insert is not a
// stored one.
type Policy struct {
	Table            string `json:"table"`
	InsertSettings   string `json:"insert_settings"`
	MaxRetries       int    `json:"max_retries"`
	InitialBackoffMS int64  `json:"initial_backoff_ms"`
	MaxBackoffMS     int64  `json:"max_backoff_ms"`
	RequestTimeoutMS int64  `json:"request_timeout_ms"`
	DeadLetter       bool   `json:"dead_letter_after_retries"`
}

// Policy returns the writer's effective insert mode and retry policy.
func (w *Writer) Policy() Policy {
	settings := "async_insert=0"
	if w.cfg.AsyncInsert {
		settings = "async_insert=1&wait_for_async_insert=0"
	}
	return Policy{Table: w.cfg.Table, InsertSettings: settings, MaxRetries: w.cfg.MaxRetries,
		InitialBackoffMS: w.cfg.InitialBackoff.Milliseconds(), MaxBackoffMS: w.cfg.MaxBackoff.Milliseconds(),
		RequestTimeoutMS: w.cfg.RequestTimeout.Milliseconds(), DeadLetter: true}
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
