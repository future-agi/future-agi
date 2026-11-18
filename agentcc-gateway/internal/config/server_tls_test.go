package config

import (
	"crypto/tls"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestServerTLSDefaults(t *testing.T) {
	cfg := DefaultConfig()
	if !cfg.Server.HTTPOn() {
		t.Error("HTTP is off by default, want on")
	}
	if cfg.Server.HTTPEnabled != nil {
		t.Errorf("http_enabled = %v by default, want unset (nil means on)", *cfg.Server.HTTPEnabled)
	}
	tlsCfg := cfg.Server.TLS
	if tlsCfg.Enabled || tlsCfg.Port != 8443 || tlsCfg.MinVersion != "1.2" || tlsCfg.ReloadInterval != 30*time.Second {
		t.Errorf("tls defaults = %+v, want off, 8443, 1.2, 30s", tlsCfg)
	}
	if err := cfg.Validate(); err != nil {
		t.Errorf("default config does not validate: %v", err)
	}
	if got := cfg.ServingAddr(); got != "0.0.0.0:8080" {
		t.Errorf("ServingAddr = %q, want the HTTP address", got)
	}
}

func TestServerTLSFromEnv(t *testing.T) {
	t.Setenv("AGENTCC_HTTP_ENABLED", "false")
	t.Setenv("AGENTCC_TLS_ENABLED", "true")
	t.Setenv("AGENTCC_TLS_PORT", "9443")
	t.Setenv("AGENTCC_TLS_CERT_FILE", "/tls/tls.crt")
	t.Setenv("AGENTCC_TLS_KEY_FILE", "/tls/tls.key")
	t.Setenv("AGENTCC_TLS_MIN_VERSION", "1.3")
	t.Setenv("AGENTCC_TLS_RELOAD_INTERVAL", "10s")

	cfg, err := Load("")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	s := cfg.Server
	if s.HTTPOn() {
		t.Error("AGENTCC_HTTP_ENABLED=false left HTTP on")
	}
	want := ServerTLSConfig{Enabled: true, Port: 9443, CertFile: "/tls/tls.crt", KeyFile: "/tls/tls.key", MinVersion: "1.3", ReloadInterval: 10 * time.Second}
	if s.TLS != want {
		t.Errorf("tls = %+v, want %+v", s.TLS, want)
	}
	if got := cfg.ServingAddr(); got != "0.0.0.0:9443" {
		t.Errorf("ServingAddr with HTTP off = %q, want the HTTPS address", got)
	}
}

// A value that does not parse keeps the configured one and says so, as the
// other AGENTCC_* settings do.
func TestServerTLSFromEnvKeepsConfiguredValueOnGarbage(t *testing.T) {
	t.Setenv("AGENTCC_TLS_PORT", "eighty")
	t.Setenv("AGENTCC_TLS_RELOAD_INTERVAL", "30")

	cfg := DefaultConfig()
	loadFromEnv(cfg)
	if !cfg.Server.HTTPOn() || cfg.Server.TLS.Enabled || cfg.Server.TLS.Port != 8443 || cfg.Server.TLS.ReloadInterval != 30*time.Second {
		t.Errorf("garbage env changed the server config: http=%v tls=%+v", cfg.Server.HTTPOn(), cfg.Server.TLS)
	}
	if err := cfg.Validate(); err != nil {
		t.Errorf("a garbage port or interval is ignored, not refused: %v", err)
	}
}

// A listener switch that is not true or false fails startup, as the
// frontend's does: ignoring AGENTCC_TLS_ENABLED=yes would serve plain HTTP
// only, and ignoring AGENTCC_HTTP_ENABLED=off would keep HTTP on.
func TestServerListenerSwitchFromEnvRefusesGarbage(t *testing.T) {
	for _, tt := range []struct{ name, value string }{
		{"AGENTCC_TLS_ENABLED", "yes"},
		{"AGENTCC_TLS_ENABLED", "on"},
		{"AGENTCC_HTTP_ENABLED", "off"},
		{"AGENTCC_HTTP_ENABLED", "nope"},
	} {
		t.Run(tt.name+"="+tt.value, func(t *testing.T) {
			t.Setenv(tt.name, tt.value)
			path := filepath.Join(t.TempDir(), "config.yaml")
			body := "server:\n  tls:\n    cert_file: /c\n    key_file: /k\n"
			if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
				t.Fatal(err)
			}
			_, err := Load(path)
			if err == nil || !strings.Contains(err.Error(), tt.name+" must be true or false") {
				t.Fatalf("Load = %v, want a refusal naming %s", err, tt.name)
			}
		})
	}
	// Valid spellings still work, with surrounding space.
	t.Setenv("AGENTCC_TLS_ENABLED", " TRUE ")
	t.Setenv("AGENTCC_TLS_CERT_FILE", "/c")
	t.Setenv("AGENTCC_TLS_KEY_FILE", "/k")
	t.Setenv("AGENTCC_HTTP_ENABLED", "0")
	cfg, err := Load("")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if !cfg.Server.TLS.Enabled || cfg.Server.HTTPOn() {
		t.Errorf("valid switches not applied: tls=%v http=%v", cfg.Server.TLS.Enabled, cfg.Server.HTTPOn())
	}
}

// http_enabled: false in config.yaml, and the env turning it back on.
func TestServerHTTPEnabledFromFileAndEnv(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	body := "server:\n  http_enabled: false\n  tls:\n    enabled: true\n    cert_file: /c\n    key_file: /k\n"
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Server.HTTPOn() {
		t.Error("http_enabled: false left HTTP on")
	}
	if cfg.Server.TLS.Port != 8443 || cfg.Server.TLS.ReloadInterval != 30*time.Second {
		t.Errorf("a partial tls block lost its defaults: %+v", cfg.Server.TLS)
	}

	t.Setenv("AGENTCC_HTTP_ENABLED", "true")
	cfg, err = Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if !cfg.Server.HTTPOn() {
		t.Error("AGENTCC_HTTP_ENABLED=true did not override the file")
	}
}

func TestServerTLSValidate(t *testing.T) {
	off := false
	on := func(c *Config) {
		c.Server.TLS.Enabled = true
		c.Server.TLS.CertFile = "/c"
		c.Server.TLS.KeyFile = "/k"
	}
	tests := []struct {
		name   string
		modify func(*Config)
		want   string // empty: valid
	}{
		{"TLS on with both files", on, ""},
		{"TLS only", func(c *Config) { on(c); c.Server.HTTPEnabled = &off }, ""},
		{"TLS 1.3", func(c *Config) { on(c); c.Server.TLS.MinVersion = "1.3" }, ""},
		{"TLS port may equal the unused HTTP port", func(c *Config) { on(c); c.Server.HTTPEnabled = &off; c.Server.TLS.Port = 8080 }, ""},
		{"a minute reload", func(c *Config) { on(c); c.Server.TLS.ReloadInterval = time.Minute }, ""},
		{"no listener", func(c *Config) { c.Server.HTTPEnabled = &off }, "turn on at least one listener"},
		{"TLS without files", func(c *Config) { c.Server.TLS.Enabled = true }, "needs server.tls.cert_file and server.tls.key_file"},
		{"TLS without a key file", func(c *Config) { on(c); c.Server.TLS.KeyFile = "" }, "needs server.tls.cert_file and server.tls.key_file"},
		{"TLS port 0", func(c *Config) { on(c); c.Server.TLS.Port = 0 }, "server.tls.port must be between 1 and 65535"},
		{"TLS port too high", func(c *Config) { on(c); c.Server.TLS.Port = 70000 }, "server.tls.port must be between 1 and 65535"},
		{"TLS port is the HTTP port", func(c *Config) { on(c); c.Server.TLS.Port = 8080 }, "need their own ports"},
		{"min version 1.1", func(c *Config) { on(c); c.Server.TLS.MinVersion = "1.1" }, `min_version must be "1.2" or "1.3"`},
		{"min version garbage", func(c *Config) { on(c); c.Server.TLS.MinVersion = "tls12" }, `min_version must be "1.2" or "1.3"`},
		{"reload interval 0", func(c *Config) { on(c); c.Server.TLS.ReloadInterval = 0 }, "reload_interval must be above 0"},
		{"reload interval over a minute", func(c *Config) { on(c); c.Server.TLS.ReloadInterval = 61 * time.Second }, "at most 1m0s"},
		// TLS settings are not checked while TLS is off.
		{"TLS off ignores its port", func(c *Config) { c.Server.TLS.Port = 0 }, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cfg := DefaultConfig()
			tt.modify(cfg)
			err := cfg.Validate()
			if tt.want == "" {
				if err != nil {
					t.Fatalf("Validate: %v, want valid", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("Validate = %v, want an error containing %q", err, tt.want)
			}
		})
	}
}

func TestParseTLSMinVersion(t *testing.T) {
	for in, want := range map[string]uint16{"": tls.VersionTLS12, "1.2": tls.VersionTLS12, "1.3": tls.VersionTLS13} {
		if got, err := ParseTLSMinVersion(in); err != nil || got != want {
			t.Errorf("ParseTLSMinVersion(%q) = %x, %v; want %x", in, got, err, want)
		}
	}
}
