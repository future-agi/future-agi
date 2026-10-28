package main

import (
	"bytes"
	"log/slog"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/encoding"
)

// The collector must not register a gzip decompressor for gRPC: it would
// inflate gzip calls outside the inflate budget, and gRPC stays
// uncompressed-only (see the comment by grpc.NewServer in pkg/server).
// Importing google.golang.org/grpc/encoding/gzip anywhere in the binary
// (directly or through a dependency) registers it globally; this test binary
// links the same packages as cmd/fi-collector, so such an import fails here.
func TestGRPCGzipDecompressorIsNotRegistered(t *testing.T) {
	if c := encoding.GetCompressor("gzip"); c != nil {
		t.Fatalf("a gzip gRPC compressor is registered (%T); see the comment by grpc.NewServer in pkg/server", c)
	}
}

// FI_GZIP_INFLIGHT_MIB (a gzip-only budget in an earlier build) is not read:
// a gzip-sized value must not shrink the budget for every request. Setting it
// logs a warning that names FI_INGEST_BUDGET_MIB.
func TestApplyEnvOverridesIgnoresGzipInflightMiB(t *testing.T) {
	cases := []struct {
		env     string
		warning bool
	}{
		{"", false},
		{"64", true},
		{"lots", true},
	}
	for _, tc := range cases {
		t.Run(tc.env, func(t *testing.T) {
			clearCollectorEnv(t)
			t.Setenv("FI_GZIP_INFLIGHT_MIB", tc.env)
			var logs bytes.Buffer
			cfg := rootConfig{}
			if err := applyEnvOverrides(slog.New(slog.NewTextHandler(&logs, nil)), &cfg); err != nil {
				t.Fatal(err)
			}
			if cfg.Server.IngestBudgetMiB != 0 {
				t.Errorf("IngestBudgetMiB = %d, want 0 (FI_GZIP_INFLIGHT_MIB is not read)", cfg.Server.IngestBudgetMiB)
			}
			if got := strings.Contains(logs.String(), "FI_INGEST_BUDGET_MIB"); got != tc.warning {
				t.Errorf("warning logged = %v, want %v (%q)", got, tc.warning, logs.String())
			}
		})
	}
}

func TestApplyEnvOverridesIngestLimits(t *testing.T) {
	clearCollectorEnv(t)
	t.Setenv("FI_INGEST_BUDGET_MIB", "300")
	t.Setenv("FI_OTLP_MAX_BODY_MIB", "8")
	t.Setenv("FI_GRPC_MAX_RECV_MIB", "12")
	t.Setenv("FI_OTLP_MAX_CONNECTIONS", "lots")
	var logs bytes.Buffer
	cfg := rootConfig{}
	if err := applyEnvOverrides(slog.New(slog.NewTextHandler(&logs, nil)), &cfg); err != nil {
		t.Fatal(err)
	}
	s := cfg.Server
	if s.IngestBudgetMiB != 300 || s.MaxBodyMiB != 8 || s.GRPCMaxRecvMiB != 12 || s.MaxConnections != 0 {
		t.Errorf("budget %d body %d recv %d conns %d; want 300 8 12 0", s.IngestBudgetMiB, s.MaxBodyMiB, s.GRPCMaxRecvMiB, s.MaxConnections)
	}
	if !strings.Contains(logs.String(), "FI_OTLP_MAX_CONNECTIONS") {
		t.Errorf("an invalid value must be logged: %q", logs.String())
	}
}

// FI_INGEST_MAX_WAIT (a Go duration) and FI_INSERT_CONCURRENCY reach the
// server config; invalid values are ignored with a warning naming them.
func TestApplyEnvOverridesBurstSettings(t *testing.T) {
	cases := []struct {
		wait, inserts string
		wantWait      time.Duration
		wantInserts   int
		warn          []string
	}{
		{"", "", 0, 0, nil},
		{"3s", "4", 3 * time.Second, 4, nil},
		{"1500ms", "1", 1500 * time.Millisecond, 1, nil},
		{"10", "-2", 0, 0, []string{"FI_INGEST_MAX_WAIT", "FI_INSERT_CONCURRENCY"}},
		{"0s", "many", 0, 0, []string{"FI_INGEST_MAX_WAIT", "FI_INSERT_CONCURRENCY"}},
	}
	for _, tc := range cases {
		t.Run(tc.wait+"/"+tc.inserts, func(t *testing.T) {
			clearCollectorEnv(t)
			t.Setenv("FI_INGEST_MAX_WAIT", tc.wait)
			t.Setenv("FI_INSERT_CONCURRENCY", tc.inserts)
			var logs bytes.Buffer
			cfg := rootConfig{}
			if err := applyEnvOverrides(slog.New(slog.NewTextHandler(&logs, nil)), &cfg); err != nil {
				t.Fatal(err)
			}
			if cfg.Server.IngestMaxWait != tc.wantWait || cfg.Server.InsertConcurrency != tc.wantInserts {
				t.Errorf("wait %v inserts %d; want %v %d", cfg.Server.IngestMaxWait, cfg.Server.InsertConcurrency, tc.wantWait, tc.wantInserts)
			}
			for _, w := range tc.warn {
				if !strings.Contains(logs.String(), w) {
					t.Errorf("an invalid %s must be logged: %q", w, logs.String())
				}
			}
			if tc.warn == nil && logs.Len() != 0 {
				t.Errorf("unexpected warnings: %q", logs.String())
			}
		})
	}
}
