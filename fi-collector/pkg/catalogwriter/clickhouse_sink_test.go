package catalogwriter

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func sinkKeyRow() map[string]any {
	return map[string]any{
		"project_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "source_kind": "custom_attribute", "attribute_key": "user.name",
		"key_folded": "user.name", "attribute_type": "string",
		"first_seen": "2026-08-13 12:00:00.000001", "last_seen": "2026-08-13 12:00:00.000001",
		"catalog_epoch": uint16(1),
	}
}

func sinkValueRow() map[string]any {
	return map[string]any{
		"project_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "source_kind": "custom_attribute", "attribute_key": "user.name",
		"attribute_type": "string", "value_fingerprint": strings.Repeat("a", 64),
		"value_json": `"alice"`, "value_search_text": "alice",
		"first_seen": "2026-08-13 12:00:00.000001", "last_seen": "2026-08-13 12:00:00.000001",
		"catalog_epoch": uint16(1),
	}
}

func sinkDeliveryRow() map[string]any {
	return map[string]any{
		"envelope_format":  "futureagi.span-attribute-catalog-envelope",
		"envelope_version": uint16(3),
		"envelope_id":      strings.Repeat("b", 64),
		"project_id":       "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "catalog_epoch": uint16(1),
		"producer_stream_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "sequence": uint64(4),
		"payload_sha256": strings.Repeat("c", 64), "previous_payload_sha256": strings.Repeat("d", 64),
		"source_batch_digest": strings.Repeat("e", 64),
		"outcome":             "committed", "gap_reasons": []string{},
		"source_min_start": "2026-08-13 12:00:00.000001",
		"source_max_start": "2026-08-13 12:00:01.000001", "source_rows": uint64(2),
		"key_rows": uint64(2), "value_rows": uint64(2),
		"transport": "direct", "kafka_partition": int32(-1), "kafka_offset": int64(-1),
		"delivered_at": "2026-08-13 12:00:02.000001", "_version": uint64(1),
	}
}

func testSink(t *testing.T, endpoint string, mutate func(*ClickHouseSinkConfig)) *ClickHouseSink {
	t.Helper()
	cfg := ClickHouseSinkConfig{URL: endpoint, Database: "futureagi"}
	if mutate != nil {
		mutate(&cfg)
	}
	sink, err := NewClickHouseSink(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return sink
}

func TestNewClickHouseSinkRejectsUnsafeConfig(t *testing.T) {
	valid := ClickHouseSinkConfig{URL: "https://clickhouse.test:8443", Database: "futureagi"}
	tests := []struct {
		mutate func(*ClickHouseSinkConfig)
		want   string
	}{
		{func(c *ClickHouseSinkConfig) { c.URL = "" }, "URL is required"},
		{func(c *ClickHouseSinkConfig) { c.URL = "tcp://clickhouse:9000" }, "http(s)"},
		{func(c *ClickHouseSinkConfig) { c.URL = "https://user:pass@clickhouse.test" }, "must not contain"},
		{func(c *ClickHouseSinkConfig) { c.URL += "?async_insert=1" }, "must not contain"},
		{func(c *ClickHouseSinkConfig) { c.Database = "default;DROP TABLE spans" }, "unquoted identifier"},
		{func(c *ClickHouseSinkConfig) { c.Password = "secret" }, "requires a username"},
		{func(c *ClickHouseSinkConfig) { c.RequestTimeout = maxCatalogRequestTimeout + 1 }, "request timeout"},
		{func(c *ClickHouseSinkConfig) { c.MaxRequestBytes = maxCatalogRequestBytes + 1 }, "request bytes"},
		{func(c *ClickHouseSinkConfig) { c.MaxResponseBytes = maxCatalogResponseBytes + 1 }, "response bytes"},
		{func(c *ClickHouseSinkConfig) { c.RequestTimeout = time.Second; c.MaxExecutionTime = 2 * time.Second }, "execution time"},
		{func(c *ClickHouseSinkConfig) { c.MaxMemoryUsage = maxCatalogMemoryBytes + 1 }, "memory setting"},
		{func(c *ClickHouseSinkConfig) { c.MaxThreads = maxCatalogMaxThreads + 1 }, "max threads"},
	}
	for index, test := range tests {
		cfg := valid
		test.mutate(&cfg)
		if _, err := NewClickHouseSink(cfg); err == nil || !strings.Contains(err.Error(), test.want) {
			t.Errorf("case %d error=%v, want %q", index, err, test.want)
		}
	}
}

func TestClickHouseSinkAllowlistAuthAndSettings(t *testing.T) {
	var calls atomic.Int32
	var queries []string
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		calls.Add(1)
		queries = append(queries, request.URL.Query().Get("query"))
		username, password, ok := request.BasicAuth()
		if !ok || username != "catalog" || password != "secret" {
			t.Errorf("auth=%q/%q/%v", username, password, ok)
		}
		if request.Method != http.MethodPost || request.Header.Get("Content-Type") != "application/x-ndjson" || request.Header.Get("X-ClickHouse-Format") != "JSONEachRow" {
			t.Errorf("method/headers=%s/%v", request.Method, request.Header)
		}
		query := request.URL.Query()
		want := map[string]string{
			"database": "futureagi", "async_insert": "1", "wait_for_async_insert": "1",
			"wait_end_of_query": "1", "input_format_parallel_parsing": "0",
			"input_format_defaults_for_omitted_fields": "0", "insert_deduplicate": "1",
			"max_execution_time": "1.500000", "max_memory_usage": "67108864", "max_threads": "1",
		}
		for key, value := range want {
			if query.Get(key) != value {
				t.Errorf("setting %s=%q want %q", key, query.Get(key), value)
			}
		}
		body, _ := io.ReadAll(request.Body)
		if strings.Count(string(body), "\n") != 1 {
			t.Errorf("not one JSONEachRow record: %q", body)
		}
		response.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	sink := testSink(t, server.URL, func(cfg *ClickHouseSinkConfig) {
		cfg.Username, cfg.Password = "catalog", "secret"
		cfg.RequestTimeout, cfg.MaxExecutionTime = 2*time.Second, 1500*time.Millisecond
		cfg.MaxMemoryUsage, cfg.MaxThreads, cfg.AsyncInsert = 64<<20, 1, true
	})

	for _, forbidden := range []Table{"spans", "traces", "end_users", "trace_sessions", Table(DeliveryTableName)} {
		if err := sink.InsertCatalog(context.Background(), forbidden, []map[string]any{sinkKeyRow()}); err == nil || !strings.Contains(err.Error(), "not allowlisted") {
			t.Fatalf("forbidden table %q error=%v", forbidden, err)
		}
	}
	malformed := sinkKeyRow()
	delete(malformed, "catalog_epoch")
	malformed["id"] = "span-id"
	if err := sink.InsertCatalog(context.Background(), KeyTable, []map[string]any{malformed}); err == nil || !strings.Contains(err.Error(), "non-catalog column shape") {
		t.Fatalf("column allowlist error=%v", err)
	}
	if calls.Load() != 0 {
		t.Fatal("unsafe insert reached HTTP")
	}
	if err := sink.InsertCatalog(context.Background(), KeyTable, []map[string]any{sinkKeyRow()}); err != nil {
		t.Fatal(err)
	}
	if err := sink.InsertCatalog(context.Background(), ValueTable, []map[string]any{sinkValueRow()}); err != nil {
		t.Fatal(err)
	}
	if err := sink.InsertDelivery(context.Background(), []map[string]any{sinkDeliveryRow()}); err != nil {
		t.Fatal(err)
	}
	wantQueries := []string{
		"INSERT INTO span_attribute_key_catalog FORMAT JSONEachRow",
		"INSERT INTO span_attribute_value_catalog FORMAT JSONEachRow",
		"INSERT INTO span_attribute_catalog_deliveries FORMAT JSONEachRow",
	}
	for index := range wantQueries {
		if queries[index] != wantQueries[index] {
			t.Errorf("query %d=%q want %q", index, queries[index], wantQueries[index])
		}
	}
}

func TestClickHouseSinkAcceptsExactLegacyCatalogRowsOnlyAsUniformBatches(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		calls.Add(1)
		body, _ := io.ReadAll(request.Body)
		if strings.Contains(string(body), "source_kind") {
			t.Fatalf("legacy replay was rewritten: %s", body)
		}
		response.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	sink := testSink(t, server.URL, nil)
	legacyKey := sinkKeyRow()
	delete(legacyKey, "source_kind")
	legacyValue := sinkValueRow()
	delete(legacyValue, "source_kind")
	if err := sink.InsertCatalog(context.Background(), KeyTable, []map[string]any{legacyKey}); err != nil {
		t.Fatal(err)
	}
	if err := sink.InsertCatalog(context.Background(), ValueTable, []map[string]any{legacyValue}); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 2 {
		t.Fatalf("legacy rows did not reach sink: %d", calls.Load())
	}
	if err := sink.InsertCatalog(
		context.Background(), KeyTable, []map[string]any{legacyKey, sinkKeyRow()},
	); err == nil || !strings.Contains(err.Error(), "mixes legacy") {
		t.Fatalf("mixed-shape batch error=%v", err)
	}
}

func TestClickHouseSinkBoundedFailures(t *testing.T) {
	t.Run("request and encoding", func(t *testing.T) {
		var calls atomic.Int32
		server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
			calls.Add(1)
			response.WriteHeader(http.StatusOK)
		}))
		defer server.Close()
		small := testSink(t, server.URL, func(cfg *ClickHouseSinkConfig) { cfg.MaxRequestBytes = 32 })
		if err := small.InsertCatalog(context.Background(), KeyTable, []map[string]any{sinkKeyRow()}); err == nil || !strings.Contains(err.Error(), "encoded byte limit") {
			t.Fatalf("oversize error=%v", err)
		}
		normal := testSink(t, server.URL, nil)
		row := sinkKeyRow()
		row["attribute_key"] = make(chan int)
		if err := normal.InsertCatalog(context.Background(), KeyTable, []map[string]any{row}); err == nil || !strings.Contains(err.Error(), "unsupported type") {
			t.Fatalf("encoding error=%v", err)
		}
		if calls.Load() != 0 {
			t.Fatal("invalid body reached HTTP")
		}
	})

	t.Run("HTTP status and response", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
			response.WriteHeader(http.StatusBadRequest)
			_, _ = response.Write([]byte("schema mismatch"))
		}))
		defer server.Close()
		sink := testSink(t, server.URL, nil)
		if err := sink.InsertDelivery(context.Background(), []map[string]any{sinkDeliveryRow()}); err == nil || !strings.Contains(err.Error(), "HTTP 400: schema mismatch") {
			t.Fatalf("status error=%v", err)
		}
	})

	t.Run("response ceiling", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
			response.WriteHeader(http.StatusInternalServerError)
			_, _ = response.Write([]byte(strings.Repeat("x", 33)))
		}))
		defer server.Close()
		sink := testSink(t, server.URL, func(cfg *ClickHouseSinkConfig) { cfg.MaxResponseBytes = 32 })
		if err := sink.InsertCatalog(context.Background(), KeyTable, []map[string]any{sinkKeyRow()}); err == nil || !strings.Contains(err.Error(), "response exceeds 32-byte limit") {
			t.Fatalf("response error=%v", err)
		}
	})

	t.Run("timeout", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
			time.Sleep(100 * time.Millisecond)
			response.WriteHeader(http.StatusOK)
		}))
		defer server.Close()
		sink := testSink(t, server.URL, func(cfg *ClickHouseSinkConfig) { cfg.RequestTimeout = 10 * time.Millisecond })
		started := time.Now()
		err := sink.InsertCatalog(context.Background(), KeyTable, []map[string]any{sinkKeyRow()})
		if err == nil || time.Since(started) > 80*time.Millisecond {
			t.Fatalf("timeout error=%v duration=%s", err, time.Since(started))
		}
	})
}

func TestClickHouseSinkEmptyBatchStillChecksAllowlist(t *testing.T) {
	sink := testSink(t, "http://127.0.0.1:1", nil)
	if err := sink.InsertCatalog(context.Background(), KeyTable, nil); err != nil {
		t.Fatal(err)
	}
	if err := sink.InsertDelivery(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	if err := sink.InsertCatalog(context.Background(), Table("spans"), nil); err == nil {
		t.Fatal("forged empty insert bypassed table allowlist")
	}
}
