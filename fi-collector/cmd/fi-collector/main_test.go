package main

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
)

// logLines parses newline-delimited slog JSON output into a slice of
// decoded records for easy assertions.
func logLines(t *testing.T, buf *bytes.Buffer) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(buf.String()), "\n") {
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("failed to parse log line %q: %v", line, err)
		}
		out = append(out, m)
	}
	return out
}

// TestLoadPriceTable_BadOverrideLogsWarnNotError proves a bad FI_PRICING_JSON
// override, followed by a successful embedded-snapshot fallback, logs at
// Warn — not Error. Pricing still works on this path, so an Error-level log
// would misreport a working fallback as a failure. The double-failure case
// (embedded snapshot itself unparseable) is not reachable in a test since
// the embedded snapshot is compiled in and always valid; that path keeps
// its Error log by inspection (see loadPriceTable).
func TestLoadPriceTable_BadOverrideLogsWarnNotError(t *testing.T) {
	badPath := filepath.Join(t.TempDir(), "bad.json")
	if err := os.WriteFile(badPath, []byte("not valid json"), 0o644); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	log := slog.New(slog.NewJSONHandler(&buf, nil))

	table := loadPriceTable(log, badPath)
	if table == nil {
		t.Fatal("want a non-nil table: the embedded snapshot fallback must succeed")
	}

	var sawWarnForOverride, sawErrorForOverride bool
	for _, rec := range logLines(t, &buf) {
		msg, _ := rec["msg"].(string)
		if !strings.Contains(msg, "FI_PRICING_JSON override load failed") {
			continue
		}
		switch rec["level"] {
		case "WARN":
			sawWarnForOverride = true
		case "ERROR":
			sawErrorForOverride = true
		}
	}
	if !sawWarnForOverride {
		t.Error("want a WARN log for the bad-override/successful-fallback path")
	}
	if sawErrorForOverride {
		t.Error("bad-override/successful-fallback path must not log at ERROR")
	}
}

// TestLoadPriceTable_SkippedEntriesWarns proves that when the loaded price
// table has skipped (malformed) entries, loadPriceTable logs a Warn with
// the skip count.
func TestLoadPriceTable_SkippedEntriesWarns(t *testing.T) {
	path := filepath.Join(t.TempDir(), "prices.json")
	body := `{
		"good-model": {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000002},
		"bad-model": {"input_cost_per_token": "not-a-number"}
	}`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	log := slog.New(slog.NewJSONHandler(&buf, nil))

	table := loadPriceTable(log, path)
	if table == nil {
		t.Fatal("want a non-nil table")
	}
	if table.Skipped != 1 {
		t.Fatalf("want Skipped=1, got %d", table.Skipped)
	}

	var sawSkippedWarn bool
	for _, rec := range logLines(t, &buf) {
		msg, _ := rec["msg"].(string)
		if msg == "pricing table loaded with skipped entries" && rec["level"] == "WARN" {
			sawSkippedWarn = true
			if skipped, ok := rec["skipped"].(float64); !ok || skipped != 1 {
				t.Errorf("want skipped=1 in log fields, got %v", rec["skipped"])
			}
		}
	}
	if !sawSkippedWarn {
		t.Error("want a WARN log reporting the skipped-entry count")
	}
}

// testWriter builds a chwriter.Writer that never connects anywhere (New
// only assembles the struct); the dead-letter file lands in a temp dir so
// the test does not touch /var/lib.
func testWriter(t *testing.T) *chwriter.Writer {
	t.Helper()
	w, err := chwriter.New(chwriter.Config{
		URL:            "http://127.0.0.1:1",
		DeadLetterFile: filepath.Join(t.TempDir(), "dead_letter.jsonl"),
	})
	if err != nil {
		t.Fatalf("chwriter.New: %v", err)
	}
	return w
}

// TestLoadConfig_AdminAddr proves the documented admin.addr key is parsed
// into rootConfig instead of being silently discarded by yaml.Unmarshal
// (issue #2135).
func TestLoadConfig_AdminAddr(t *testing.T) {
	path := filepath.Join(t.TempDir(), "collector.yaml")
	body := "admin:\n  addr: \":19464\"\n"
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	log := slog.New(slog.NewJSONHandler(&buf, nil))
	cfg := loadConfig(log, path)
	if cfg.Admin.Addr != ":19464" {
		t.Fatalf("want admin.addr=:19464, got %q", cfg.Admin.Addr)
	}
}

// TestApplyEnvOverrides_AdminAddr proves FI_ADMIN_ADDR (already referenced
// by the docker-compose files) overrides admin.addr instead of being
// silently ignored (issue #2135).
func TestApplyEnvOverrides_AdminAddr(t *testing.T) {
	t.Setenv("FI_ADMIN_ADDR", ":19464")
	cfg := rootConfig{Admin: adminConfig{Addr: ":9464"}}
	applyEnvOverrides(slog.New(slog.NewJSONHandler(&bytes.Buffer{}, nil)), &cfg)
	if cfg.Admin.Addr != ":19464" {
		t.Fatalf("want FI_ADMIN_ADDR=:19464, got %q", cfg.Admin.Addr)
	}
}

// TestResolveAdminAddr proves the fallback default is the loopback-only
// 127.0.0.1:9464 (internal-only admin surface must not bind 0.0.0.0 by
// default) and that a configured addr wins.
func TestResolveAdminAddr(t *testing.T) {
	if got := resolveAdminAddr(rootConfig{}); got != defaultAdminAddr {
		t.Fatalf("want default %q, got %q", defaultAdminAddr, got)
	}
	if got := resolveAdminAddr(rootConfig{Admin: adminConfig{Addr: ":9464"}}); got != ":9464" {
		t.Fatalf("want configured :9464, got %q", got)
	}
}

// TestAdminMux_ServesHealthzAndMetrics proves the admin mux serves both
// documented endpoints: /healthz (200 + ok) and /metrics (200 + Prometheus
// text exposition with writer stats) — the latter was a 404 before
// issue #2135.
func TestAdminMux_ServesHealthzAndMetrics(t *testing.T) {
	w := testWriter(t)
	log := slog.New(slog.NewJSONHandler(&bytes.Buffer{}, nil))
	srv := httptest.NewServer(newAdminMux(w, log))
	defer srv.Close()

	// /healthz
	resp, err := http.Get(srv.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("want /healthz 200, got %d", resp.StatusCode)
	}
	var hz map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&hz); err != nil {
		t.Fatalf("decode /healthz: %v", err)
	}
	if hz["status"] != "ok" {
		t.Fatalf("want status ok, got %v", hz["status"])
	}

	// /metrics
	resp, err = http.Get(srv.URL + "/metrics")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("want /metrics 200, got %d", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "text/plain") {
		t.Fatalf("want text/plain content type, got %q", ct)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"# TYPE fi_collector_batches_inserted_total counter",
		"fi_collector_batches_inserted_total 0",
		"fi_collector_rows_inserted_total 0",
		"# TYPE go_goroutines gauge",
	} {
		if !strings.Contains(string(body), want) {
			t.Errorf("want /metrics body to contain %q", want)
		}
	}
}

// TestAdminMux_UnknownPathNotFound proves the admin mux does not silently
// serve undocumented paths.
func TestAdminMux_UnknownPathNotFound(t *testing.T) {
	w := testWriter(t)
	log := slog.New(slog.NewJSONHandler(&bytes.Buffer{}, nil))
	srv := httptest.NewServer(newAdminMux(w, log))
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/nope")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("want /nope 404, got %d", resp.StatusCode)
	}
}
