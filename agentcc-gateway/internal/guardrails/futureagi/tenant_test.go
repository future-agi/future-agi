package futureagi

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestTenantFutureAGIEnvironmentIsLiteral(t *testing.T) {
	t.Setenv("FI_API_KEY", "operator-key")
	t.Setenv("FI_SECRET_KEY", "operator-secret")
	t.Setenv("FI_BASE_URL", "https://operator.example")
	cfg := map[string]interface{}{
		"api_key": "${FI_API_KEY}", "secret_key": "${FI_SECRET_KEY}",
		"base_url": "https://tenant.example", "eval_id": "safety",
	}
	for _, tenant := range []bool{false, true} {
		var g *FutureAGIGuardrail
		wantKey, wantSecret := "operator-key", "operator-secret"
		if tenant {
			g = NewTenant("check", cfg)
			wantKey, wantSecret = "${FI_API_KEY}", "${FI_SECRET_KEY}"
		} else {
			g = New("check", cfg)
		}
		var sent bool
		g.client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
			sent = true
			if r.Header.Get("X-Api-Key") != wantKey || r.Header.Get("X-Secret-Key") != wantSecret {
				t.Errorf("tenant=%v sent gateway environment credentials", tenant)
			}
			return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(`{"result":[]}`)), Header: make(http.Header)}, nil
		})
		result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
		if !sent || !result.Pass {
			t.Fatalf("tenant=%v request sent=%v, result=%+v", tenant, sent, result)
		}
	}
	cfg["base_url"] = "${FI_BASE_URL}"
	if got := NewTenant("check", cfg).baseURL; got != "${FI_BASE_URL}" {
		t.Fatalf("tenant base URL expanded to %q", got)
	}
	if got := New("check", cfg).baseURL; got != "https://operator.example" {
		t.Fatalf("operator base URL did not expand: %q", got)
	}
}

func TestTenantFutureAGIMissingCredentialsPolicy(t *testing.T) {
	var logs bytes.Buffer
	originalLogger := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	t.Cleanup(func() { slog.SetDefault(originalLogger) })
	t.Setenv("FI_API_KEY", "operator-key")
	t.Setenv("FI_SECRET_KEY", "operator-secret")
	input := makeInput([]models.Message{makeMsg("user", "hello")})
	for _, credentials := range []string{"neither", "api_key", "secret_key"} {
		cfg := map[string]interface{}{"provider": "futureagi", "eval_id": "safety"}
		if credentials != "neither" {
			cfg[credentials] = "tenant-credential"
		}
		g := NewTenant("check", cfg)
		g.client.Transport = roundTripFunc(func(*http.Request) (*http.Response, error) {
			t.Error("incomplete tenant credentials must not make an evaluator request")
			return nil, fmt.Errorf("unexpected request")
		})
		if result := g.Check(context.Background(), input); result.Pass || !result.OperationalError {
			t.Errorf("credentials=%s: want an operational failure, got %+v", credentials, result)
		}
		for _, failOpen := range []bool{false, true} {
			for _, threshold := range []float64{0.8, 1} {
				t.Run(fmt.Sprintf("%s/open=%v/threshold=%g", credentials, failOpen, threshold), func(t *testing.T) {
					registry := map[string]guardrails.Guardrail{"check": g}
					engineCfg := config.GuardrailsConfig{
						FailOpen: failOpen,
						Rules:    []config.GuardrailRuleConfig{{Name: "check", Stage: "pre", Action: "block", Threshold: threshold}},
					}
					engine := guardrails.NewEngine(engineCfg, registry)
					result := engine.RunPre(context.Background(), input, nil, policy.RequestPolicyNone)
					if result.Blocked != !failOpen {
						t.Errorf("engine blocked=%v, want %v", result.Blocked, !failOpen)
					}

					store := tenant.NewStore()
					store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{
						FailOpen: failOpen,
						Checks: map[string]*tenant.GuardrailCheck{"check": {
							Enabled: true, Action: "block", ConfidenceThreshold: threshold, Config: cfg,
						}},
					}})
					// Like every dynamic guardrail error, the operator's fail_open decides.
					operator := guardrails.NewEngine(config.GuardrailsConfig{FailOpen: failOpen}, nil)
					factory := func(string, map[string]interface{}) guardrails.Guardrail { return g }
					plugin := guardrails.NewPlugin(operator, nil, factory, nil, store)
					rc := &models.RequestContext{Request: input.Request, Metadata: map[string]string{"org_id": "org"}}
					logs.Reset()
					got := plugin.ProcessRequest(context.Background(), rc)
					if (got.Error != nil) != !failOpen {
						t.Errorf("managed error=%v, want blocked=%v", got.Error, !failOpen)
					}
					if !strings.Contains(logs.String(), "dynamic guardrail error") || !strings.Contains(logs.String(), "missing api_key or secret_key") {
						t.Errorf("operational failure was not logged: %s", logs.String())
					}
				})
			}
		}
	}
}

func TestTenantFutureAGIDoesNotInheritOperatorCredentials(t *testing.T) {
	t.Setenv("FI_API_KEY", "operator-key")
	t.Setenv("FI_SECRET_KEY", "operator-secret")
	t.Setenv("FI_BASE_URL", "https://operator.example")
	for _, cfg := range []map[string]interface{}{nil, {}, {"eval_id": "safety"}} {
		g := NewTenant("check", cfg)
		if g.apiKey != "" || g.secretKey != "" {
			t.Fatalf("tenant inherited operator credentials: %+v", g)
		}
		// The platform URL is not a secret; a tenant without base_url uses it.
		// A nil config reads no environment at all, as before.
		if cfg != nil && g.baseURL != "https://operator.example" {
			t.Fatalf("tenant base URL = %q, want the operator platform", g.baseURL)
		}
	}
	g := New("check", map[string]interface{}{"eval_id": "safety"})
	if g.apiKey != "operator-key" || g.secretKey != "operator-secret" || g.baseURL != "https://operator.example" {
		t.Fatal("operator environment fallback stopped working")
	}
}
