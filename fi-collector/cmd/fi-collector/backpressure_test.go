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
