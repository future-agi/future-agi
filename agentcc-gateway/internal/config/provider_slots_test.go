package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// PROVIDER-CONCURRENCY-1: providers.<id>.queue_timeout and
// provider_defaults.queue_timeout load from the file, and no slot setting may
// be negative (0 takes the default).
func TestProviderSlotKeysLoad(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	body := "provider_defaults:\n  queue_timeout: 3s\nproviders:\n  p:\n    base_url: https://api.example.invalid\n    api_format: openai\n" +
		"    max_concurrent: 2048\n    conn_pool_size: 512\n    queue_timeout: 750ms\n"
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	p := cfg.Providers["p"]
	if cfg.ProviderDefaults.QueueTimeout != 3*time.Second || p.QueueTimeout != 750*time.Millisecond || p.MaxConcurrent != 2048 || p.ConnPoolSize != 512 {
		t.Fatalf("defaults %+v provider %+v", cfg.ProviderDefaults, p)
	}
}

func TestProviderSlotKeysRejectNegatives(t *testing.T) {
	for _, tc := range []struct {
		name   string
		modify func(*Config)
		want   string
	}{
		{"max_concurrent", func(c *Config) { p := c.Providers["p"]; p.MaxConcurrent = -1; c.Providers["p"] = p }, `provider "p": max_concurrent`},
		{"conn_pool_size", func(c *Config) { p := c.Providers["p"]; p.ConnPoolSize = -1; c.Providers["p"] = p }, `provider "p": max_concurrent`},
		{"queue_timeout", func(c *Config) { p := c.Providers["p"]; p.QueueTimeout = -time.Second; c.Providers["p"] = p }, `provider "p": max_concurrent`},
		{"provider_defaults", func(c *Config) { c.ProviderDefaults.QueueTimeout = -time.Second }, "provider_defaults.queue_timeout"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := DefaultConfig()
			cfg.Providers["p"] = ProviderConfig{BaseURL: "https://api.example.invalid", APIFormat: "openai"}
			if err := cfg.Validate(); err != nil {
				t.Fatalf("valid config refused: %v", err)
			}
			tc.modify(cfg)
			if err := cfg.Validate(); err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("err %v; want one naming %q", err, tc.want)
			}
		})
	}
}
