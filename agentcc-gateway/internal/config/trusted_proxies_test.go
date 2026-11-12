package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func loadServerYAML(t *testing.T, server string) (*Config, error) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte("server:\n  port: 9090\n"+server), 0o644); err != nil {
		t.Fatalf("writing config: %v", err)
	}
	return Load(path)
}

func TestLoad_ReadsTrustedProxies(t *testing.T) {
	cfg, err := loadServerYAML(t, "  trusted_proxies: [\"10.0.0.0/8\", \"35.191.0.0/16\", \"2001:db8::1\"]\n")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	want := []string{"10.0.0.0/8", "35.191.0.0/16", "2001:db8::1"}
	if strings.Join(cfg.Server.TrustedProxies, ",") != strings.Join(want, ",") {
		t.Fatalf("server.trusted_proxies = %q, want %q", cfg.Server.TrustedProxies, want)
	}

	cfg, err = loadServerYAML(t, "  trusted_hops: 2\n")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Server.TrustedHops != 2 {
		t.Fatalf("server.trusted_hops = %d, want 2", cfg.Server.TrustedHops)
	}
}

func TestLoad_TrustsNoProxyByDefault(t *testing.T) {
	cfg := DefaultConfig()
	if len(cfg.Server.TrustedProxies) != 0 || cfg.Server.TrustedHops != 0 {
		t.Fatalf("default server.trusted_proxies %q, trusted_hops %d: want none", cfg.Server.TrustedProxies, cfg.Server.TrustedHops)
	}
}

// A mistyped proxy range must stop the gateway, not quietly trust nothing (or
// the wrong network).
func TestLoad_RefusesInvalidTrustedProxies(t *testing.T) {
	for _, tc := range []struct{ yaml, wantErr string }{
		{"  trusted_proxies: [\"10.0.0.0/33\"]\n", "is not an IP address or CIDR"},
		{"  trusted_proxies: [\"lb.internal\"]\n", "is not an IP address or CIDR"},
		{"  trusted_hops: -1\n", "must not be negative"},
		{"  trusted_proxies: [\"10.0.0.0/8\"]\n  trusted_hops: 1\n", "not both"},
	} {
		_, err := loadServerYAML(t, tc.yaml)
		if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
			t.Errorf("config %q: error %v, want one containing %q", tc.yaml, err, tc.wantErr)
		}
	}
}
