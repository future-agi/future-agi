package main

import (
	"bytes"
	"log/slog"
	"strings"
	"testing"

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

func TestApplyEnvOverridesGzipInflightMiB(t *testing.T) {
	cases := []struct {
		env     string
		want    int
		warning bool
	}{
		{"", 0, false},
		{"64", 64, false},
		{"0", 0, true},
		{"-8", 0, true},
		{"lots", 0, true},
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
			if cfg.Server.GzipInflightMiB != tc.want {
				t.Errorf("GzipInflightMiB = %d, want %d", cfg.Server.GzipInflightMiB, tc.want)
			}
			if got := strings.Contains(logs.String(), "FI_GZIP_INFLIGHT_MIB"); got != tc.warning {
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
