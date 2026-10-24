package orgfactory_test

// These tests run against the real org-config factory the gateway wires: the
// "collector" is an httptest server on 127.0.0.1 standing in for a host an
// org picked, and the "gateway secret" is a fake value set only for the test,
// never a real credential and never a real variable name.
//
// An org config is caller-controlled. A credential or base_url it sets is
// taken literally; the gateway never resolves a ${VAR} in it against its own
// process environment. The two org tests assert the test value never leaves
// the process; the config-file test asserts the operator path expands ${VAR}.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	guardrails "github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/orgfactory"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Fake values planted for the test. Obviously not real credentials, and the
// variable names are test-only, so nothing here names a deployment's secrets.
const (
	fakeEnvVar    = "FAKE_GATEWAY_ENV_SECRET"
	fakeEnvVar2   = "FAKE_GATEWAY_ENV_SECRET_TWO"
	fakeEnvValue  = "planted-fake-value-not-a-real-secret"
	fakeEnvValue2 = "planted-fake-value-two-not-a-real-secret"
)

// collector records whether an org-chosen URL was reached and with what.
type collector struct {
	mu      sync.Mutex
	hit     bool
	path    string
	headers http.Header
	body    []byte
}

func (c *collector) handler(w http.ResponseWriter, r *http.Request) {
	buf := make([]byte, 1<<16)
	n, _ := r.Body.Read(buf)
	c.mu.Lock()
	c.hit = true
	c.path = r.URL.Path
	c.headers = r.Header.Clone()
	c.body = buf[:n]
	c.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(`{"pass":true,"score":0.0,"message":"ok"}`))
}

func orgRequestContext(orgID string) *models.RequestContext {
	return &models.RequestContext{
		RequestID: "org-env-req",
		Metadata:  map[string]string{"org_id": orgID},
		Timings:   map[string]time.Duration{},
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4",
			Messages: []models.Message{
				{Role: "user", Content: json.RawMessage(`"an org request that triggers the check"`)},
			},
		},
	}
}

// loopbackListed is an egress dialer that lists loopback, as an operator's
// mcp.egress.private_allow would. The org factory dials every callback an org
// config names through its dialer, and these tests' collector is an httptest
// server on 127.0.0.1: with loopback listed the guard lets the call through,
// so it is the environment refusal, not the egress guard, that keeps the
// collector from receiving the gateway's environment.
func loopbackListed(t *testing.T) *egress.Dialer {
	t.Helper()
	d, err := egress.New(egress.Options{PrivateAllow: []string{"127.0.0.0/8", "::1"}})
	if err != nil {
		t.Fatalf("egress.New: %v", err)
	}
	return d
}

func driveOrgCheck(t *testing.T, orgID string, check *tenant.GuardrailCheck) {
	store := tenant.NewStore()
	store.Set(orgID, &tenant.OrgConfig{
		Guardrails: &tenant.GuardrailConfig{
			Checks: map[string]*tenant.GuardrailCheck{check.Action + "-check": check},
		},
	})
	p := guardrails.NewPlugin(nil, map[string]guardrails.Guardrail{}, orgfactory.New(loopbackListed(t)), nil, store)
	p.ProcessRequest(context.Background(), orgRequestContext(orgID))
}

// A futureagi check an org pushes names ${VAR} for its credentials. The
// gateway must not expand them against its own environment: the org-chosen
// base_url must never receive the gateway's host-env value.
func TestOrgFutureAGIGuardrailNeverSendsGatewayEnv(t *testing.T) {
	t.Setenv(fakeEnvVar, fakeEnvValue)
	t.Setenv(fakeEnvVar2, fakeEnvValue2)

	col := &collector{}
	srv := httptest.NewServer(http.HandlerFunc(col.handler))
	defer srv.Close()

	driveOrgCheck(t, "org-fi", &tenant.GuardrailCheck{
		Enabled: true,
		Action:  "log",
		Config: map[string]interface{}{
			"provider":   "futureagi",
			"base_url":   srv.URL, // an org-chosen destination
			"api_key":    "${" + fakeEnvVar + "}",
			"secret_key": "${" + fakeEnvVar2 + "}",
			"eval_id":    "toxicity",
		},
	})

	col.mu.Lock()
	defer col.mu.Unlock()
	if col.hit {
		t.Fatalf("org-chosen URL was reached: path=%q X-Api-Key=%q X-Secret-Key=%q — an org config must not drive a call that carries the gateway's environment",
			col.path, col.headers.Get("X-Api-Key"), col.headers.Get("X-Secret-Key"))
	}
}

// Belt-and-braces: even if a future change lets the call out, the planted
// environment value must never appear in it.
func TestOrgFutureAGIGuardrailNeverLeaksPlantedValue(t *testing.T) {
	t.Setenv(fakeEnvVar, fakeEnvValue)
	t.Setenv(fakeEnvVar2, fakeEnvValue2)

	col := &collector{}
	srv := httptest.NewServer(http.HandlerFunc(col.handler))
	defer srv.Close()

	driveOrgCheck(t, "org-fi2", &tenant.GuardrailCheck{
		Enabled: true,
		Action:  "log",
		Config: map[string]interface{}{
			"provider":   "futureagi",
			"base_url":   srv.URL,
			"api_key":    "${" + fakeEnvVar + "}",
			"secret_key": "${" + fakeEnvVar2 + "}",
			"eval_id":    "toxicity",
		},
	})

	col.mu.Lock()
	defer col.mu.Unlock()
	if col.headers.Get("X-Api-Key") == fakeEnvValue || col.headers.Get("X-Secret-Key") == fakeEnvValue2 {
		t.Fatalf("the gateway's planted environment value was sent to an org-chosen URL (api-match=%v secret-match=%v)",
			col.headers.Get("X-Api-Key") == fakeEnvValue, col.headers.Get("X-Secret-Key") == fakeEnvValue2)
	}
}

// An external-provider check an org pushes names ${VAR} for its api_key. The
// same rule: the org-chosen endpoint must never be reached with a value read
// from the gateway's environment.
func TestOrgExternalGuardrailNeverSendsGatewayEnv(t *testing.T) {
	t.Setenv(fakeEnvVar, fakeEnvValue)

	col := &collector{}
	srv := httptest.NewServer(http.HandlerFunc(col.handler))
	defer srv.Close()

	driveOrgCheck(t, "org-ext", &tenant.GuardrailCheck{
		Enabled: true,
		Action:  "log",
		Config: map[string]interface{}{
			"provider": "lakera",
			"endpoint": srv.URL + "/v2/guard",
			"api_key":  "${" + fakeEnvVar + "}",
		},
	})

	col.mu.Lock()
	defer col.mu.Unlock()
	if col.hit {
		t.Fatalf("org-chosen endpoint was reached: path=%q Authorization=%q — an org config must not drive a call that carries the gateway's environment",
			col.path, col.headers.Get("Authorization"))
	}
}

// The config.yaml path is operator-trusted and must keep expanding ${VAR}:
// futureagi.NewConfigFileRule (the registry-loop constructor) resolves a
// ${VAR} api_key from the environment and the call carries it.
func TestConfigFileFutureAGIGuardrailStillExpandsEnv(t *testing.T) {
	t.Setenv(fakeEnvVar, fakeEnvValue)
	t.Setenv(fakeEnvVar2, fakeEnvValue2)

	col := &collector{}
	srv := httptest.NewServer(http.HandlerFunc(col.handler))
	defer srv.Close()

	g := futureagi.NewConfigFileRule("config-file-rule", map[string]interface{}{
		"provider":   "futureagi",
		"base_url":   srv.URL,
		"api_key":    "${" + fakeEnvVar + "}",
		"secret_key": "${" + fakeEnvVar2 + "}",
		"eval_id":    "toxicity",
	})
	raw, _ := json.Marshal("operator config-file rule evaluates this")
	g.Check(context.Background(), &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model:    "gpt-4",
			Messages: []models.Message{{Role: "user", Content: raw}},
		},
	})

	col.mu.Lock()
	defer col.mu.Unlock()
	if !col.hit {
		t.Fatal("config-file futureagi rule did not reach its configured base_url")
	}
	if got := col.headers.Get("X-Api-Key"); got != fakeEnvValue {
		t.Fatalf("config-file rule did not expand ${VAR}: X-Api-Key=%q, want the environment value", got)
	}
	if got := col.headers.Get("X-Secret-Key"); got != fakeEnvValue2 {
		t.Fatalf("config-file rule did not expand ${VAR}: X-Secret-Key=%q, want the environment value", got)
	}
}

// The same for an external-provider rule in config.yaml:
// external.NewConfigFileRule, the constructor the
// registry loop in cmd/agentcc calls for an external rule, resolves a ${VAR}
// endpoint and api_key from the environment, and the call goes to the
// resolved URL carrying the resolved key. A rename or a swap to the org-safe
// external.New (which refuses "${" and makes no call) fails this test.
func TestConfigFileExternalGuardrailStillExpandsEnv(t *testing.T) {
	col := &collector{}
	srv := httptest.NewServer(http.HandlerFunc(col.handler))
	defer srv.Close()
	t.Setenv(fakeEnvVar, srv.URL+"/config-file-lakera")
	t.Setenv(fakeEnvVar2, fakeEnvValue2)

	g := external.NewConfigFileRule("config-file-lakera", map[string]interface{}{
		"provider": "lakera",
		"endpoint": "${" + fakeEnvVar + "}",
		"api_key":  "${" + fakeEnvVar2 + "}",
	})
	raw, _ := json.Marshal("operator config-file rule evaluates this")
	g.Check(context.Background(), &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model:    "gpt-4",
			Messages: []models.Message{{Role: "user", Content: raw}},
		},
	})

	col.mu.Lock()
	defer col.mu.Unlock()
	if !col.hit {
		t.Fatal("config-file external rule did not reach the endpoint its ${VAR} names")
	}
	if col.path != "/config-file-lakera" {
		t.Fatalf("config-file external rule called path %q, want the environment value's path", col.path)
	}
	if got := col.headers.Get("Authorization"); got != "Bearer "+fakeEnvValue2 {
		t.Fatalf("config-file external rule did not expand ${VAR}: Authorization=%q, want the environment value", got)
	}
}
