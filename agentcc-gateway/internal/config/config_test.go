package config

import (
	"bytes"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDefaultConfig(t *testing.T) {
	cfg := DefaultConfig()

	if cfg.Server.Port != 8080 {
		t.Errorf("default port = %d, want 8080", cfg.Server.Port)
	}
	if cfg.Server.ReadHeaderTimeout != 5*time.Second || cfg.Server.ReadTimeout != 60*time.Second {
		t.Errorf("default read timeouts = (%v, %v), want (5s, 60s)", cfg.Server.ReadHeaderTimeout, cfg.Server.ReadTimeout)
	}
	if cfg.Server.DefaultRequestTimeout != 60*time.Second {
		t.Errorf("default timeout = %v, want 60s", cfg.Server.DefaultRequestTimeout)
	}
	if cfg.Logging.Level != "info" {
		t.Errorf("default log level = %q, want %q", cfg.Logging.Level, "info")
	}
}

func TestValidate(t *testing.T) {
	publicKey := testLicensePublicKey(t)
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
			name:    "invalid read header timeout",
			modify:  func(c *Config) { c.Server.ReadHeaderTimeout = 0 },
			wantErr: true,
		},
		{
			name:    "invalid log level",
			modify:  func(c *Config) { c.Logging.Level = "verbose" },
			wantErr: true,
		},
		{
			name:    "max request timeout below the server default",
			modify:  func(c *Config) { c.Server.MaxRequestTimeout = 30 * time.Second },
			wantErr: true,
		},
		{
			name:    "max request timeout unset leaves it to Load",
			modify:  func(c *Config) { c.Server.MaxRequestTimeout = 0 },
			wantErr: false,
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
			name: "otlp exporter without endpoint",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "otlp"}
			},
			wantErr: true,
		},
		{
			name: "otlp exporter with endpoint",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "otlp", Endpoint: "http://otel-collector:4318"}
			},
			wantErr: false,
		},
		{
			name: "otlp exporter with explicit http/protobuf",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "otlp", Endpoint: "http://otel-collector:4318", Protocol: "http/protobuf"}
			},
			wantErr: false,
		},
		{
			name: "otlp headers accepted",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "otlp", Endpoint: "http://otel-collector:4318",
					Headers: map[string]string{"X-Api-Key": "k"}}
			},
			wantErr: false,
		},
		{
			// An unset ${VAR} expands to empty; sending it means a 401 and every
			// span silently discarded, which looks exactly like an idle gateway.
			name: "otlp header with an empty value is rejected",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "otlp", Endpoint: "http://otel-collector:4318",
					Headers: map[string]string{"X-Api-Key": ""}}
			},
			wantErr: true,
		},
		{
			name: "otlp exporter rejects grpc protocol",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "otlp", Endpoint: "http://otel-collector:4318", Protocol: "grpc"}
			},
			wantErr: true,
		},
		{
			name: "otlp exporter rejects http/json protocol",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "otlp", Endpoint: "http://otel-collector:4318", Protocol: "http/json"}
			},
			wantErr: true,
		},
		{
			name: "otlp endpoint must carry a scheme",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "otlp", Endpoint: "otel-collector:4318"}
			},
			wantErr: true,
		},
		{
			name: "otlp endpoint rejects non-http scheme",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "otlp", Endpoint: "grpc://otel-collector:4317"}
			},
			wantErr: true,
		},
		{
			name: "stdout exporter needs no endpoint",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: true, Exporter: "stdout"}
			},
			wantErr: false,
		},
		{
			name: "disabled otel is not validated",
			modify: func(c *Config) {
				c.OTel = OTelConfig{Enabled: false, Exporter: "otlp", Protocol: "grpc"}
			},
			wantErr: false,
		},
		{
			name: "license auth requires key",
			modify: func(c *Config) {
				c.LicenseAuth.Enabled = true
				c.Redis.Enabled = true
				c.Redis.Address = "redis:6379"
			},
			wantErr: true,
		},
		{
			name: "license auth requires runtime state",
			modify: func(c *Config) {
				c.LicenseAuth.Enabled = true
				c.LicenseAuth.PublicKey = publicKey
				c.LicenseAuth.RuntimeStateRequired = false
				c.Redis.Enabled = true
				c.Redis.Address = "redis:6379"
			},
			wantErr: true,
		},
		{
			name: "license auth requires redis",
			modify: func(c *Config) {
				c.LicenseAuth.Enabled = true
				c.LicenseAuth.PublicKey = publicKey
			},
			wantErr: true,
		},
		{
			name: "license auth rejects malformed key",
			modify: func(c *Config) {
				c.LicenseAuth.Enabled = true
				c.LicenseAuth.PublicKey = "not-pem"
				c.Redis.Enabled = true
				c.Redis.Address = "redis:6379"
			},
			wantErr: true,
		},
		{
			name: "valid license auth",
			modify: func(c *Config) {
				c.LicenseAuth.Enabled = true
				c.LicenseAuth.PublicKey = publicKey
				c.Redis.Enabled = true
				c.Redis.Address = "redis:6379"
			},
			wantErr: false,
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

// TestLoadOTelFromFile pins the YAML key names the shipped starter config uses.
// A silent rename here means a customer's collector never receives a span.
func TestLoadOTelFromFile(t *testing.T) {
	content := `
otel:
  enabled: true
  service_name: "agentcc-gateway-stage"
  exporter: "otlp"
  endpoint: "${TEST_OTLP_ENDPOINT}"
  protocol: "http/protobuf"
  sample_rate: 1.0
  attributes:
    deployment.environment: "stage"
`
	t.Setenv("TEST_OTLP_ENDPOINT", "http://otel-collector:4318")

	tmpDir := t.TempDir()
	cfgPath := filepath.Join(tmpDir, "config.yaml")
	if err := os.WriteFile(cfgPath, []byte(content), 0644); err != nil {
		t.Fatalf("writing config: %v", err)
	}

	cfg, err := Load(cfgPath)
	if err != nil {
		t.Fatalf("Load error: %v", err)
	}

	if !cfg.OTel.Enabled {
		t.Error("otel.enabled = false, want true")
	}
	if cfg.OTel.Exporter != "otlp" {
		t.Errorf("otel.exporter = %q, want otlp", cfg.OTel.Exporter)
	}
	// Endpoints hold secrets in some deployments, so ${VAR} must expand here
	// exactly as it does for provider credentials.
	if cfg.OTel.Endpoint != "http://otel-collector:4318" {
		t.Errorf("otel.endpoint = %q, want the expanded value", cfg.OTel.Endpoint)
	}
	if cfg.OTel.Protocol != "http/protobuf" {
		t.Errorf("otel.protocol = %q", cfg.OTel.Protocol)
	}
	if cfg.OTel.ServiceName != "agentcc-gateway-stage" {
		t.Errorf("otel.service_name = %q", cfg.OTel.ServiceName)
	}
	if cfg.OTel.SampleRate != 1.0 {
		t.Errorf("otel.sample_rate = %v, want 1.0", cfg.OTel.SampleRate)
	}
	if cfg.OTel.Attributes["deployment.environment"] != "stage" {
		t.Errorf("otel.attributes = %v", cfg.OTel.Attributes)
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

// The values are shared with the backend's test, which reads the same
// variable: an unparseable one keeps the safe default in both.
func TestLoadFromEnv_AllowPrivateProviderURLs(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "provider-url-policy.json"))
	if err != nil {
		t.Fatalf("reading the shared URL policy cases: %v", err)
	}
	var policy struct {
		OptInEnv []struct {
			Value   string `json:"value"`
			Allowed bool   `json:"allowed"`
		} `json:"opt_in_env"`
	}
	if err := json.Unmarshal(raw, &policy); err != nil {
		t.Fatalf("parsing the shared URL policy cases: %v", err)
	}
	for _, tt := range policy.OptInEnv {
		t.Run(tt.Value, func(t *testing.T) {
			t.Setenv(EnvAllowPrivateProviderURLs, tt.Value)
			cfg, err := Load("")
			if err != nil {
				t.Fatalf("Load error: %v", err)
			}
			if cfg.OrgProviders.AllowPrivateURLs != tt.Allowed {
				t.Errorf("AllowPrivateURLs = %v, want %v", cfg.OrgProviders.AllowPrivateURLs, tt.Allowed)
			}
		})
	}
}

// A gateway given the control-plane URL reloads Django's keys on start and,
// with an interval, keeps them in step; the shared admin token authenticates
// those calls unless a separate control-plane token is set.
func TestLoadFromEnv_ControlPlaneSync(t *testing.T) {
	t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend:8000")
	t.Setenv("AGENTCC_SYNC_ON_STARTUP", "true")
	t.Setenv("AGENTCC_SYNC_INTERVAL", "30s")
	t.Setenv("AGENTCC_ADMIN_TOKEN", "shared-admin-token")

	cfg, err := Load("")
	if err != nil {
		t.Fatalf("Load error: %v", err)
	}
	if !cfg.ControlPlane.SyncOnStartup || cfg.ControlPlane.SyncInterval != 30*time.Second {
		t.Errorf("sync_on_startup = %v, sync_interval = %v, want true, 30s", cfg.ControlPlane.SyncOnStartup, cfg.ControlPlane.SyncInterval)
	}
	if cfg.ControlPlane.AdminToken != "shared-admin-token" {
		t.Errorf("control plane token = %q, want the admin token", cfg.ControlPlane.AdminToken)
	}

	t.Setenv("AGENTCC_CONTROL_PLANE_TOKEN", "separate-token")
	cfg, err = Load("")
	if err != nil {
		t.Fatalf("Load error: %v", err)
	}
	if cfg.ControlPlane.AdminToken != "separate-token" {
		t.Errorf("control plane token = %q, want the explicit one", cfg.ControlPlane.AdminToken)
	}
}

func testLicensePublicKey(t *testing.T) string {
	t.Helper()
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	der, err := x509.MarshalPKIXPublicKey(&privateKey.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))
}

func TestAddr(t *testing.T) {
	cfg := DefaultConfig()
	if addr := cfg.Addr(); addr != "0.0.0.0:8080" {
		t.Errorf("Addr() = %q, want %q", addr, "0.0.0.0:8080")
	}
}

// A present internal API key must force auth ON, even when AGENTCC_AUTH_ENABLED=false
// is also set. Before the precedence fix the toggle ran last and could silently
// re-open a gateway that had a key configured.
func TestLoadFromEnvAuthFailsClosedWhenKeyPresent(t *testing.T) {
	t.Setenv("AGENTCC_AUTH_ENABLED", "false")
	t.Setenv("AGENTCC_INTERNAL_API_KEY", "test-internal-key")

	cfg := DefaultConfig()
	loadFromEnv(cfg)

	if !cfg.Auth.Enabled {
		t.Fatal("auth must be enabled when AGENTCC_INTERNAL_API_KEY is set, even with AGENTCC_AUTH_ENABLED=false")
	}
	var seeded *AuthKeyConfig
	for i := range cfg.Auth.Keys {
		if cfg.Auth.Keys[i].Key == "test-internal-key" {
			seeded = &cfg.Auth.Keys[i]
		}
	}
	if seeded == nil {
		t.Fatal("internal API key must be seeded into the key store")
	}
	// Must be typed "internal": a byok key (the keystore default) is barred from
	// the global providers, so the backend would authenticate then 403 its own route.
	if seeded.KeyType != "internal" {
		t.Fatalf("seeded key KeyType = %q, want \"internal\"", seeded.KeyType)
	}
	if seeded.Metadata["access_groups"] != "internal" {
		t.Fatalf("seeded key access group = %q, want internal", seeded.Metadata["access_groups"])
	}
}

// The explicit toggle still applies on its own when no key is configured.
func TestLoadFromEnvAuthToggleHonoredWhenNoKey(t *testing.T) {
	t.Setenv("AGENTCC_INTERNAL_API_KEY", "")
	t.Setenv("AGENTCC_AUTH_ENABLED", "false")

	cfg := DefaultConfig()
	loadFromEnv(cfg)

	if cfg.Auth.Enabled {
		t.Fatal("auth should stay off when no key is set and the toggle is false")
	}
}

// An explicit config.yaml entry for the same key must not be clobbered by the
// env seed — the keystore is last-write-wins by hash, so a duplicate would
// override (and could re-type) the operator's entry.
func TestLoadFromEnvDoesNotClobberExplicitInternalKey(t *testing.T) {
	t.Setenv("AGENTCC_INTERNAL_API_KEY", "test-internal-key")

	cfg := DefaultConfig()
	cfg.Auth.Keys = []AuthKeyConfig{{
		Name:    "ops-configured",
		Key:     "test-internal-key",
		Owner:   "ops",
		KeyType: "internal",
	}}
	loadFromEnv(cfg)

	n := 0
	for _, k := range cfg.Auth.Keys {
		if k.Key == "test-internal-key" {
			n++
			if k.Name != "ops-configured" {
				t.Fatalf("explicit config entry overwritten: Name = %q, want \"ops-configured\"", k.Name)
			}
		}
	}
	if n != 1 {
		t.Fatalf("got %d entries for the key, want 1 (env seed must not duplicate an explicit config key)", n)
	}
}

// A config file that states the server block without max_request_timeout gets
// the write timeout, the point past which no response can be written anyway.
func TestLoad_MaxRequestTimeoutDefaultsToWriteTimeout(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	body := "server:\n  write_timeout: 200s\n  default_request_timeout: 60s\n"
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("writing config: %v", err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Server.MaxRequestTimeout != 200*time.Second {
		t.Errorf("max_request_timeout = %v, want 200s (the write timeout)", cfg.Server.MaxRequestTimeout)
	}
}

// A ceiling above the write timeout is one no response can reach, so Load
// lowers it rather than refusing to start.
// A ceiling above write_timeout is kept. The timeout middleware holds the
// response write deadline at the ceiling, so write_timeout no longer decides
// how long a request may take — and lowering the ceiling to it would truncate
// the operator's own setting silently.
func TestLoad_MaxRequestTimeoutAboveTheWriteTimeoutIsKept(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	body := "server:\n  write_timeout: 300s\n  max_request_timeout: 600s\n"
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("writing config: %v", err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Server.MaxRequestTimeout != 600*time.Second {
		t.Errorf("max_request_timeout = %v, want 600s (the configured ceiling)", cfg.Server.MaxRequestTimeout)
	}
}

// In a YAML flow mapping, "input_per_token:1e-07" (no space after the colon)
// is one key with no value. The gateway reads no such key, so the price stays
// unset and the model's requests carry no cost. Load must name the key, and
// must not print the secret a mistyped key would otherwise carry.
func TestLoad_WarnsAboutKeysTheGatewayDoesNotRead(t *testing.T) {
	var logs bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })
	t.Setenv("AGENTCC_TEST_PORT", "8080")
	t.Setenv("AGENTCC_TEST_TOKEN", "do-not-log-me")

	path := filepath.Join(t.TempDir(), "config.yaml")
	body := `server:
  port: ${AGENTCC_TEST_PORT}
admin: { token:${AGENTCC_TEST_TOKEN} }
model_database:
  overrides:
    "vendor/model":
      pricing: { input_per_token:1e-07, output_per_token: 2e-07 }
`
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("writing config: %v", err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got := cfg.ModelDatabase.Overrides["vendor/model"].Pricing.InputPerToken; got != nil {
		t.Fatalf("input_per_token = %v, want unset", *got)
	}
	out := logs.String()
	// Every value a missing space pulled into a key is dropped, a ${VAR}
	// stand-in included: a reference and a literal compose, so
	// {token:${EMPTY_VAR}sk-live-...} would otherwise carry the secret whole.
	// The key with its line number is what finds the typo.
	for _, key := range []string{"input_per_token:<value omitted>", "token:<value omitted>"} {
		if !strings.Contains(out, key) {
			t.Errorf("log does not name the unread key %q:\n%s", key, out)
		}
	}
	// The variable's value must never appear either way.
	if strings.Contains(out, "do-not-log-me") {
		t.Errorf("the environment variable's value reached the log:\n%s", out)
	}
	if strings.Contains(out, "do-not-log-me") {
		t.Errorf("log carries the token's value:\n%s", out)
	}
	if strings.Contains(out, "cannot unmarshal") {
		t.Errorf("log flags the unexpanded ${AGENTCC_TEST_PORT}, which is not a key:\n%s", out)
	}
}

// A default request timeout above the ceiling is not something to normalize:
// the gateway could never serve it.
func TestLoad_DefaultAboveTheCeilingFails(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	body := "server:\n  write_timeout: 120s\n  default_request_timeout: 600s\n"
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("writing config: %v", err)
	}

	if _, err := Load(path); err == nil {
		t.Fatal("Load accepted a default request timeout above the write timeout")
	}
}

// A duration environment variable without a unit used to be dropped in
// silence, which reads as "the environment does not override the file".
func TestLoadFromEnv_DurationWithoutAUnitKeepsTheConfiguredValue(t *testing.T) {
	tests := []struct {
		value string
		want  time.Duration
	}{
		{"180", 90 * time.Second},
		{"180000", 90 * time.Second},
		{"", 90 * time.Second},
		{"180s", 180 * time.Second},
		{"3m", 3 * time.Minute},
	}
	for _, tt := range tests {
		t.Run("AGENTCC_DEFAULT_REQUEST_TIMEOUT="+tt.value, func(t *testing.T) {
			t.Setenv("AGENTCC_DEFAULT_REQUEST_TIMEOUT", tt.value)

			cfg := DefaultConfig()
			cfg.Server.DefaultRequestTimeout = 90 * time.Second
			loadFromEnv(cfg)

			if cfg.Server.DefaultRequestTimeout != tt.want {
				t.Errorf("default_request_timeout = %v, want %v", cfg.Server.DefaultRequestTimeout, tt.want)
			}
		})
	}
}

// The typo this warning exists to catch is what pulls a value into a key name,
// so the error text is exactly where a literal credential turns up. The ${VAR}
// stand-ins cover a credential referenced from the environment; this covers one
// written into the file.
func TestWarnUnreadKeys_DoesNotLogALiteralCredential(t *testing.T) {
	const secret = "sk-live-FAKE-SENTINEL-0123456789"

	tests := []struct {
		name string
		body string
		key  string // the key name an operator needs in order to find the typo
	}{
		{
			name: "a provider api_key in a flow mapping",
			body: "providers:\n  demo: {api_key:" + secret + ", api_format: openai}\n",
			key:  "api_key",
		},
		{
			name: "an admin token in a flow mapping",
			body: "admin: {token:" + secret + "}\n",
			key:  "token",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var logged strings.Builder
			restore := slog.Default()
			slog.SetDefault(slog.New(slog.NewTextHandler(&logged, &slog.HandlerOptions{Level: slog.LevelWarn})))
			defer slog.SetDefault(restore)

			warnUnreadKeys([]byte(tc.body))

			out := logged.String()
			if strings.Contains(out, secret) {
				t.Fatalf("the credential reached the log:\n%s", out)
			}
			if !strings.Contains(out, tc.key) {
				t.Errorf("the key %q is missing, so the operator cannot find the typo:\n%s", tc.key, out)
			}
		})
	}
}

// An ordinary unread key is still reported in full: it carries no value, and
// the whole point of the warning is to name it.
func TestWarnUnreadKeys_ReportsAnOrdinaryUnreadKey(t *testing.T) {
	var logged strings.Builder
	restore := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logged, &slog.HandlerOptions{Level: slog.LevelWarn})))
	defer slog.SetDefault(restore)

	warnUnreadKeys([]byte("server:\n  unknown_thing: 5\n"))

	if out := logged.String(); !strings.Contains(out, "unknown_thing") {
		t.Errorf("the unread key was not named:\n%s", out)
	}
}

// A ${VAR} reference and a literal compose into one key. The stand-in made the
// whole value look like a variable name, so keeping it published the literal
// suffix in full.
func TestWarnUnreadKeys_ComposedReferenceAndLiteral(t *testing.T) {
	const secret = "sk-live-COMPOSED-SENTINEL-0123456789"

	for _, tc := range []struct{ name, env, body string }{
		{
			name: "an empty reference followed by a literal",
			env:  "",
			body: "admin: { token:${AGENTCC_TEST_PREFIX}" + secret + " }\n",
		},
		{
			name: "a set reference followed by a literal",
			env:  "prefix-",
			body: "admin: { token:${AGENTCC_TEST_PREFIX}" + secret + " }\n",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("AGENTCC_TEST_PREFIX", tc.env)

			var logged strings.Builder
			restore := slog.Default()
			slog.SetDefault(slog.New(slog.NewTextHandler(&logged, &slog.HandlerOptions{Level: slog.LevelWarn})))
			defer slog.SetDefault(restore)

			warnUnreadKeys([]byte(tc.body))

			out := logged.String()
			if strings.Contains(out, secret) {
				t.Fatalf("the literal credential reached the log:\n%s", out)
			}
			if !strings.Contains(out, "token") {
				t.Errorf("the key is missing, so the operator cannot find the typo:\n%s", out)
			}
		})
	}
}
