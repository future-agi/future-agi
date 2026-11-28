package main

import (
	"gopkg.in/yaml.v3"
	"io"
	"log/slog"
	"strings"
	"testing"
)

func TestObsoletePendingRowBudgetIsRejected(t *testing.T) {
	for _, v := range []string{"0", "42", "-1", "invalid"} {
		t.Run(v, func(t *testing.T) {
			t.Setenv("FI_MAX_PENDING_ROWS", v)
			if err := applyEnvOverrides(slog.New(slog.NewTextHandler(io.Discard, nil)), &rootConfig{}); err == nil || !strings.Contains(err.Error(), "FI_INGEST_BUDGET_MIB") {
				t.Fatalf("obsolete row setting silently accepted: %v", err)
			}
		})
	}
}

func TestObsoleteYAMLRowBudgetIsRejected(t *testing.T) {
	for _, v := range []string{"0", "42", "-1"} {
		t.Run(v, func(t *testing.T) {
			var cfg rootConfig
			if err := yaml.Unmarshal([]byte("server:\n  max_pending_rows: "+v+"\n"), &cfg); err != nil {
				t.Fatal(err)
			}
			if err := applyEnvOverrides(slog.New(slog.NewTextHandler(io.Discard, nil)), &cfg); err == nil {
				t.Fatal("obsolete YAML row budget silently accepted")
			}
		})
	}
}

func TestTrajectoryEnvIsRefusedWhileDormant(t *testing.T) {
	for _, tc := range []struct{ key, value string }{
		{"FI_TRAJECTORY_DEDUP_MODE", "on"},
		{"FI_TRAJECTORY_DEDUP_MODE", "canary"},
		{"FI_TRAJECTORY_DEDUP_PROJECTS", "11111111-1111-1111-1111-111111111111"},
		{"FI_COMPACT_SPAN_ATTRS", "true"},
		{"FI_BILL_DEDUPED_BYTES", "true"},
	} {
		t.Run(tc.key+"="+tc.value, func(t *testing.T) {
			t.Setenv(tc.key, tc.value)
			err := applyEnvOverrides(slog.New(slog.NewTextHandler(io.Discard, nil)), &rootConfig{})
			if err == nil || !strings.Contains(err.Error(), "disabled in this release") {
				t.Fatalf("expected dormant refusal for %s=%s, got %v", tc.key, tc.value, err)
			}
		})
	}
	t.Run("defaults start", func(t *testing.T) {
		t.Setenv("FI_TRAJECTORY_DEDUP_MODE", "off")
		t.Setenv("FI_COMPACT_SPAN_ATTRS", "false")
		t.Setenv("FI_BILL_DEDUPED_BYTES", "false")
		if err := applyEnvOverrides(slog.New(slog.NewTextHandler(io.Discard, nil)), &rootConfig{}); err != nil && strings.Contains(err.Error(), "disabled in this release") {
			t.Fatalf("explicit off values must be accepted: %v", err)
		}
	})
}
