package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	configTestAdmin       = "config-test-admin-token-0123456789abcdef0123456789abcdef"
	configTestProviderKey = "sk-config-test-operator-provider-key-must-never-show"
	configTestWebhook     = "config-test-webhook-secret-must-never-show"
	configTestCPToken     = "config-test-control-plane-token-must-never-show"
)

// configTestServer is a gateway with the settings the console's Settings cards
// show set to values a default would not give.
func configTestServer(t *testing.T, mutate func(*config.Config)) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Admin.Token = configTestAdmin
	cfg.Server.Port = 18080
	cfg.Server.Host = "127.0.0.2"
	cfg.Server.ReadTimeout = 61 * time.Second
	cfg.Server.WriteTimeout = 301 * time.Second
	cfg.Server.IdleTimeout = 121 * time.Second
	cfg.Server.MaxRequestTimeout = 299 * time.Second
	cfg.Logging.Level = "debug"
	cfg.Logging.RequestLogging.Enabled = true
	cfg.Logging.RequestLogging.IncludeBodies = true
	cfg.Privacy.DefaultContentMode = "metadata"
	cfg.ControlPlane.AdminToken = configTestCPToken
	cfg.ControlPlane.WebhookSecret = configTestWebhook
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: "http://127.0.0.1:9", APIKey: configTestProviderKey, APIFormat: "openai", Models: []string{"gpt-4o"}}
	if mutate != nil {
		mutate(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	srv.ready.Store(true)
	return srv
}

func getConfigForTest(t *testing.T, srv *Server) (map[string]json.RawMessage, string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/-/config", nil)
	req.Header.Set("Authorization", "Bearer "+configTestAdmin)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("GET /-/config -> %d %s", w.Code, w.Body.String())
	}
	var out map[string]json.RawMessage
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatalf("GET /-/config body %q: %v", w.Body.String(), err)
	}
	return out, w.Body.String()
}

// TestConfigEndpointFields: GET /-/config carries the server timeouts, the
// logging flags and the batch facts the console's Settings cards show,
// and no secret.
func TestConfigEndpointFields(t *testing.T) {
	srv := configTestServer(t, nil)
	out, raw := getConfigForTest(t, srv)

	var server struct {
		Port                int    `json:"port"`
		Host                string `json:"host"`
		ReadTimeoutMS       *int64 `json:"read_timeout_ms"`
		WriteTimeoutMS      *int64 `json:"write_timeout_ms"`
		IdleTimeoutMS       *int64 `json:"idle_timeout_ms"`
		MaxRequestTimeoutMS *int64 `json:"max_request_timeout_ms"`
	}
	if err := json.Unmarshal(out["server"], &server); err != nil {
		t.Fatalf("server block %s: %v", out["server"], err)
	}
	if server.Port != 18080 || server.Host != "127.0.0.2" {
		t.Errorf("server port/host = %d %q, want 18080 127.0.0.2", server.Port, server.Host)
	}
	for name, got := range map[string]*int64{
		"read_timeout_ms": server.ReadTimeoutMS, "write_timeout_ms": server.WriteTimeoutMS,
		"idle_timeout_ms": server.IdleTimeoutMS, "max_request_timeout_ms": server.MaxRequestTimeoutMS,
	} {
		want := map[string]int64{"read_timeout_ms": 61000, "write_timeout_ms": 301000, "idle_timeout_ms": 121000, "max_request_timeout_ms": 299000}[name]
		if got == nil || *got != want {
			t.Errorf("server.%s = %v, want %d (in %s)", name, got, want, out["server"])
		}
	}

	var logging struct {
		Level          *string `json:"level"`
		RequestLogging *bool   `json:"request_logging"`
		IncludeBodies  *bool   `json:"include_bodies"`
		ContentMode    *string `json:"content_mode"`
	}
	if err := json.Unmarshal(out["logging"], &logging); err != nil {
		t.Fatalf("logging block %s: %v", out["logging"], err)
	}
	if logging.Level == nil || *logging.Level != "debug" || logging.RequestLogging == nil || !*logging.RequestLogging ||
		logging.IncludeBodies == nil || !*logging.IncludeBodies || logging.ContentMode == nil || *logging.ContentMode != "metadata" {
		t.Errorf("logging = %s, want level debug, request_logging true, include_bodies true, content_mode metadata", out["logging"])
	}

	var batch struct {
		Storage               *string `json:"storage"`
		DefaultMaxConcurrency *int    `json:"default_max_concurrency"`
		MaxBodyBytes          *int    `json:"max_body_bytes"`
	}
	if err := json.Unmarshal(out["batch"], &batch); err != nil {
		t.Fatalf("batch block %s: %v", out["batch"], err)
	}
	if batch.Storage == nil || *batch.Storage != "memory" || batch.DefaultMaxConcurrency == nil || *batch.DefaultMaxConcurrency != 10 ||
		batch.MaxBodyBytes == nil || *batch.MaxBodyBytes != 10485760 {
		t.Errorf("batch = %s, want storage memory, default_max_concurrency 10, max_body_bytes 10485760", out["batch"])
	}

	for _, secret := range []string{configTestAdmin, configTestProviderKey, configTestWebhook, configTestCPToken} {
		if strings.Contains(raw, secret) {
			t.Errorf("GET /-/config carries a secret: %q in %s", secret, raw)
		}
	}

	// Request logging off and no content mode set read as such.
	srv = configTestServer(t, func(c *config.Config) {
		c.Logging.RequestLogging.Enabled = false
		c.Logging.RequestLogging.IncludeBodies = false
		c.Privacy.DefaultContentMode = ""
	})
	out, _ = getConfigForTest(t, srv)
	if err := json.Unmarshal(out["logging"], &logging); err != nil {
		t.Fatal(err)
	}
	if *logging.RequestLogging || *logging.IncludeBodies || *logging.ContentMode != "full" {
		t.Errorf("logging with request logging off and no default content mode = %s, want request_logging false, include_bodies false, content_mode full", out["logging"])
	}
}

// TestOrgSectionsEnforced: org_sections_enforced is exactly what the server
// was told the node installed, and absent when nothing told it (the console
// reads an absent field as "enforcement unknown").
func TestOrgSectionsEnforced(t *testing.T) {
	srv := configTestServer(t, nil)
	out, _ := getConfigForTest(t, srv)
	if v, ok := out["org_sections_enforced"]; ok {
		t.Errorf("org_sections_enforced = %s on a server nothing told; want the field absent", v)
	}

	srv.SetOrgSectionsEnforced([]string{})
	out, _ = getConfigForTest(t, srv)
	if v := string(out["org_sections_enforced"]); v != "[]" {
		t.Errorf("org_sections_enforced with no org plugin = %q, want []", v)
	}

	want := []string{"alerting", "budgets", "ip_acl", "rate_limiting", "tool_policy"}
	srv.SetOrgSectionsEnforced(want)
	want[0] = "changed by the caller" // the server keeps its own copy
	out, _ = getConfigForTest(t, srv)
	var got []string
	if err := json.Unmarshal(out["org_sections_enforced"], &got); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, []string{"alerting", "budgets", "ip_acl", "rate_limiting", "tool_policy"}) {
		t.Errorf("org_sections_enforced = %v", got)
	}
}
