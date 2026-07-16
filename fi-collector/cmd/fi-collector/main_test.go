package main

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
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
