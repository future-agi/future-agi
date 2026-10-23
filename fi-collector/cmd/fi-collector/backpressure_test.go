package main

import (
	"io"
	"log/slog"
	"testing"
)

func TestPendingRowBudgetEnvironmentValidation(t *testing.T) {
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	for _, value := range []string{"-1", "not-an-integer"} {
		t.Run(value, func(t *testing.T) {
			t.Setenv("FI_MAX_PENDING_ROWS", value)
			if err := applyEnvOverrides(log, &rootConfig{}); err == nil {
				t.Fatal("invalid row budget accepted")
			}
		})
	}
	t.Setenv("FI_MAX_PENDING_ROWS", "42")
	var cfg rootConfig
	if err := applyEnvOverrides(log, &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.Server.MaxPendingRows != 42 {
		t.Fatalf("budget not applied: %d", cfg.Server.MaxPendingRows)
	}
	t.Setenv("FI_MAX_PENDING_ROWS", "0")
	if err := applyEnvOverrides(log, &cfg); err != nil || cfg.Server.MaxPendingRows != 0 {
		t.Fatalf("cannot explicitly disable row admission: %v, %d", err, cfg.Server.MaxPendingRows)
	}
	t.Setenv("FI_MAX_PENDING_ROWS", "")
	cfg.Server.MaxPendingRows = -1
	if err := applyEnvOverrides(log, &cfg); err == nil {
		t.Fatal("negative YAML row budget accepted")
	}
}
