package main

import (
	"io"
	"log/slog"
	"testing"
)

func TestPendingRowBudgetEnvironmentValidation(t *testing.T) {
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	for _, value := range []string{"0", "-1", "not-an-integer"} {
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
}
