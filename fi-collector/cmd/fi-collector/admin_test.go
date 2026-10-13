package main

import (
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

func testWriter(t *testing.T) *chwriter.Writer {
	t.Helper()
	w, err := chwriter.New(chwriter.Config{
		URL:            "http://127.0.0.1:1",
		DeadLetterFile: filepath.Join(t.TempDir(), "dead_letter.jsonl"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = w.Close() })
	return w
}

func TestLoadConfig_AdminAddr(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cfg.yaml")
	if err := os.WriteFile(path, []byte("admin:\n  addr: \":19464\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := loadConfig(slog.New(slog.NewJSONHandler(io.Discard, nil)), path)
	if cfg.Admin.Addr != ":19464" {
		t.Fatalf("admin.addr=%q, want :19464 (yaml key was discarded)", cfg.Admin.Addr)
	}
}

func TestLoadConfig_ShippedCollectorYAMLAdminAddr(t *testing.T) {
	cfg := loadConfig(slog.New(slog.NewJSONHandler(io.Discard, nil)), filepath.Join("..", "..", "config", "collector.yaml"))
	if cfg.Admin.Addr != "127.0.0.1:9464" {
		t.Fatalf("shipped admin.addr=%q, want 127.0.0.1:9464", cfg.Admin.Addr)
	}
}

func TestApplyEnvOverrides_AdminAddr(t *testing.T) {
	t.Setenv("FI_ADMIN_ADDR", ":19464")
	var cfg rootConfig
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.Admin.Addr != ":19464" {
		t.Fatalf("admin.addr=%q, want :19464", cfg.Admin.Addr)
	}
}

func TestResolveAdminAddr(t *testing.T) {
	if got := resolveAdminAddr(rootConfig{}); got != "127.0.0.1:9464" {
		t.Fatalf("empty addr default=%q", got)
	}
	if got := resolveAdminAddr(rootConfig{Admin: adminConfig{Addr: "  "}}); got != "127.0.0.1:9464" {
		t.Fatalf("whitespace addr default=%q", got)
	}
	if got := resolveAdminAddr(rootConfig{Admin: adminConfig{Addr: ":19464"}}); got != ":19464" {
		t.Fatalf("configured addr=%q", got)
	}
}

func TestAdminMux_ServesHealthzAndMetrics(t *testing.T) {
	mux := adminMux(testWriter(t))

	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rr.Code != http.StatusOK {
		t.Fatalf("healthz status=%d body=%s", rr.Code, rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), `"status":"ok"`) {
		t.Fatalf("healthz body=%s", rr.Body.String())
	}

	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if rr.Code != http.StatusOK {
		t.Fatalf("metrics status=%d body=%s", rr.Code, rr.Body.String())
	}
	body := rr.Body.String()
	for _, want := range []string{
		"fi_collector_batches_inserted_total",
		"fi_collector_batches_failed_total",
		"# TYPE fi_collector_batches_inserted_total counter",
		"go_goroutines",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("metrics missing %q", want)
		}
	}
	if ct := rr.Header().Get("Content-Type"); !strings.Contains(ct, "text/plain") {
		t.Errorf("content-type=%q", ct)
	}
}

func TestAdminMux_UnknownPathNotFound(t *testing.T) {
	mux := adminMux(testWriter(t))
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/not-a-route", nil))
	if rr.Code != http.StatusNotFound {
		t.Fatalf("status=%d, want 404", rr.Code)
	}
}
