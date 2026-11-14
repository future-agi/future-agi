package config

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestLogFlushConfiguration(t *testing.T) {
	defaults := DefaultConfig().ControlPlane.LogFlush
	if defaults.Interval != 5*time.Second || defaults.Timeout != 15*time.Second || defaults.MaxBatch != 5000 || defaults.MaxRetries != 3 || defaults.RetryBackoff != 5*time.Second || defaults.MaxRetryBackoff != time.Minute {
		t.Fatalf("defaults: %+v", defaults)
	}
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	if err := os.WriteFile(path, []byte("control_plane:\n  log_flush:\n    interval: 2s\n    timeout: 4s\n    max_batch: 9\n    max_retries: 7\n    retry_backoff: 3s\n    max_retry_backoff: 30s\n"), 0600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if f := cfg.ControlPlane.LogFlush; f.Interval != 2*time.Second || f.Timeout != 4*time.Second || f.MaxBatch != 9 || f.MaxRetries != 7 || f.RetryBackoff != 3*time.Second || f.MaxRetryBackoff != 30*time.Second {
		t.Fatalf("YAML: %+v", f)
	}
	for k, v := range map[string]string{"AGENTCC_LOG_FLUSH_INTERVAL": "1s", "AGENTCC_LOG_FLUSH_TIMEOUT": "6s", "AGENTCC_LOG_FLUSH_MAX_BATCH": "11", "AGENTCC_LOG_FLUSH_MAX_RETRIES": "4", "AGENTCC_LOG_FLUSH_RETRY_BACKOFF": "8s", "AGENTCC_LOG_FLUSH_MAX_RETRY_BACKOFF": "40s"} {
		t.Setenv(k, v)
	}
	cfg, err = Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if f := cfg.ControlPlane.LogFlush; f.Interval != time.Second || f.Timeout != 6*time.Second || f.MaxBatch != 11 || f.MaxRetries != 4 || f.RetryBackoff != 8*time.Second || f.MaxRetryBackoff != 40*time.Second {
		t.Fatalf("env: %+v", f)
	}
}

func TestLogFlushInvalidValuesFallBack(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	if err := os.WriteFile(path, []byte("control_plane:\n  log_flush:\n    interval: -1s\n    timeout: -2s\n    max_batch: -1\n    max_retries: -2\n    retry_backoff: -3s\n    max_retry_backoff: -4s\n"), 0600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ControlPlane.LogFlush != DefaultConfig().ControlPlane.LogFlush {
		t.Fatalf("negative: %+v", cfg.ControlPlane.LogFlush)
	}
	t.Setenv("AGENTCC_LOG_FLUSH_INTERVAL", "180")
	t.Setenv("AGENTCC_LOG_FLUSH_MAX_BATCH", "-10")
	cfg, err = Load("")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ControlPlane.LogFlush != DefaultConfig().ControlPlane.LogFlush {
		t.Fatalf("bare number/negative: %+v", cfg.ControlPlane.LogFlush)
	}
	if err := os.WriteFile(path, []byte("control_plane:\n  log_flush:\n    interval: 180\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("bare YAML duration without a unit was accepted")
	}
}
