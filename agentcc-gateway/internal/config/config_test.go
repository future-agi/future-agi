package config

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDefaultConfig(t *testing.T) {
	cfg := DefaultConfig()

	if cfg.Server.Port != 8080 {
		t.Errorf("default port = %d, want 8080", cfg.Server.Port)
	}
	if cfg.Server.DefaultRequestTimeout != 60*time.Second {
		t.Errorf("default timeout = %v, want 60s", cfg.Server.DefaultRequestTimeout)
	}
	if cfg.Logging.Level != "info" {
		t.Errorf("default log level = %q, want %q", cfg.Logging.Level, "info")
	}
}

func TestValidate(t *testing.T) {
	tests := []struct {
		name    string
		modify  func(*Config)
		wantErr bool
	}{
		{
			name:    "valid default",
			modify:  func(c *Config) {},
			wantErr: false,
		},
		{
			name:    "invalid port zero",
			modify:  func(c *Config) { c.Server.Port = 0 },
			wantErr: true,
		},
		{
			name:    "invalid port too high",
			modify:  func(c *Config) { c.Server.Port = 70000 },
			wantErr: true,
		},
		{
			name:    "invalid read timeout",
			modify:  func(c *Config) { c.Server.ReadTimeout = 0 },
			wantErr: true,
		},
		{
			name:    "invalid log level",
			modify:  func(c *Config) { c.Logging.Level = "verbose" },
			wantErr: true,
		},
		{
			name: "provider missing base_url",
			modify: func(c *Config) {
				c.Providers["test"] = ProviderConfig{APIFormat: "openai"}
			},
			wantErr: true,
		},
		{
			name: "provider missing api_format",
			modify: func(c *Config) {
				c.Providers["test"] = ProviderConfig{BaseURL: "http://localhost"}
			},
			wantErr: true,
		},
		{
			name: "valid provider",
			modify: func(c *Config) {
				c.Providers["test"] = ProviderConfig{BaseURL: "http://localhost", APIFormat: "openai"}
			},
			wantErr: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cfg := DefaultConfig()
			tt.modify(cfg)
			err := cfg.Validate()
			if (err != nil) != tt.wantErr {
				t.Errorf("Validate() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestLoadFromFile(t *testing.T) {
	content := `
server:
  port: 9090
logging:
  level: debug
providers:
  test-openai:
    base_url: "https://api.openai.com"
    api_key: "sk-test"
    api_format: "openai"
    models:
      - gpt-4o
`
	tmpDir := t.TempDir()
	cfgPath := filepath.Join(tmpDir, "config.yaml")
	if err := os.WriteFile(cfgPath, []byte(content), 0644); err != nil {
		t.Fatalf("writing config: %v", err)
	}

	cfg, err := Load(cfgPath)
	if err != nil {
		t.Fatalf("Load error: %v", err)
	}

	if cfg.Server.Port != 9090 {
		t.Errorf("port = %d, want 9090", cfg.Server.Port)
	}
	if cfg.Logging.Level != "debug" {
		t.Errorf("log level = %q, want %q", cfg.Logging.Level, "debug")
	}
	if p, ok := cfg.Providers["test-openai"]; !ok {
		t.Error("provider test-openai not found")
	} else {
		if p.BaseURL != "https://api.openai.com" {
			t.Errorf("base_url = %q", p.BaseURL)
		}
		if p.APIKey != "sk-test" {
			t.Errorf("api_key = %q", p.APIKey)
		}
	}
}

func TestLoadFromEnv(t *testing.T) {
	t.Setenv("AGENTCC_PORT", "3000")
	t.Setenv("AGENTCC_LOG_LEVEL", "warn")
	t.Setenv("AGENTCC_ADMIN_TOKEN", "secret123")

	cfg, err := Load("")
	if err != nil {
		t.Fatalf("Load error: %v", err)
	}

	if cfg.Server.Port != 3000 {
		t.Errorf("port = %d, want 3000", cfg.Server.Port)
	}
	if cfg.Logging.Level != "warn" {
		t.Errorf("log level = %q, want %q", cfg.Logging.Level, "warn")
	}
	if cfg.Admin.Token != "secret123" {
		t.Errorf("admin token = %q, want %q", cfg.Admin.Token, "secret123")
	}
}

func TestAddr(t *testing.T) {
	cfg := DefaultConfig()
	if addr := cfg.Addr(); addr != "0.0.0.0:8080" {
		t.Errorf("Addr() = %q, want %q", addr, "0.0.0.0:8080")
	}
}
