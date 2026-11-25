package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/anthropicfmt"
	"github.com/futureagi/agentcc-gateway/internal/genaifmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestInspectionR3AllRegisteredUnknownRoutesHTTP(t *testing.T) {
	upstream := &dialectUpstream{reply: "uninspected-provider-content"}
	up := httptest.NewServer(upstream)
	defer up.Close()
	cfg := &tenant.GuardrailConfig{FailOpen: true, KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
	base, _ := runtimePolicyServer(t, up.URL, cfg, nil)
	base.asyncWorker.Stop()
	// Enable route expansion without configuring remote tools/agents. Every
	// synthetic upstream remains localhost; no external connector is started.
	base.cfg.MCP.Enabled = true
	base.cfg.A2A.Enabled = true
	srv := New(base.cfg, "", base.registry, base.engine, base.keyStore, nil, nil, nil, testModelDBPtr(), base.TenantStore, nil)
	defer srv.asyncWorker.Stop()
	gateway := httptest.NewServer(srv.httpServer.Handler)
	defer gateway.Close()
	inventory := srv.InspectionInventory()
	for _, row := range inventory {
		encoded, _ := json.Marshal(row)
		t.Logf("registered_inventory=%s", encoded)
	}
	tested := 0
	seenMCP, seenA2A, seenSpeech := false, false, false
	for _, row := range inventory {
		if row.Method == "INTERNAL" || row.Auth == inspectionAuthPublic || row.Auth == inspectionAuthAdmin || row.Contract.Output != models.InspectionUnknown {
			continue
		}
		t.Run(row.Method+" "+row.Pattern, func(t *testing.T) {
			parts := strings.Split(row.Pattern, "/")
			for i, part := range parts {
				if !strings.HasPrefix(part, "{") {
					continue
				}
				switch part {
				case "{model_action}":
					parts[i] = "gemini-3.8-flash:countTokens"
				case "{provider}":
					parts[i] = "gemini"
				case "{model}":
					parts[i] = "gpt-5.5"
				default:
					parts[i] = "synthetic"
				}
			}
			path := strings.Join(parts, "/")
			status, body := inspectionR3HTTP(t, gateway, row.Method, path, `{"jsonrpc":"2.0","id":1,"method":"tasks/get","params":{"id":"synthetic"}}`, inspectionR3Headers("a"))
			if status != 403 || !strings.Contains(body, "output_guardrail_unsupported") || strings.Contains(body, "uninspected-provider-content") || upstream.served() != 0 {
				t.Fatalf("status=%d provider_calls=%d body=%s", status, upstream.served(), body)
			}
			encoded, _ := json.Marshal(struct {
				RouteInspection
				Status        int `json:"status"`
				ProviderCalls int `json:"provider_calls"`
				ResponseBytes int `json:"gateway_error_bytes"`
				ContentBytes  int `json:"released_content_bytes"`
			}{row, status, upstream.served(), len(body), 0})
			t.Logf("inspection_inventory=%s", encoded)
			tested++
			seenMCP = seenMCP || row.Auth == inspectionAuthMCP
			seenA2A = seenA2A || row.Pattern == "/a2a"
			seenSpeech = seenSpeech || row.Auth == inspectionAuthSpeech
		})
	}
	if tested < 60 || !seenMCP || !seenA2A || !seenSpeech {
		t.Fatalf("incomplete expanded inventory: tested=%d mcp=%v a2a=%v speech=%v", tested, seenMCP, seenA2A, seenSpeech)
	}
	// Search is inventory/refusal coverage, not evidence of a shipped search
	// implementation. An unselected caller retains the no-provider capability.
	status, body := inspectionR3HTTP(t, gateway, "POST", "/v1/search", `{"query":"synthetic","search_provider":"openai/gpt-5.5"}`, inspectionR3Headers("b"))
	if status != 501 || !strings.Contains(body, "not_supported") || upstream.served() != 0 {
		t.Fatalf("unselected search status=%d provider_calls=%d body=%s", status, upstream.served(), body)
	}
}

func TestInspectionR3ExactModelCatalogHTTP(t *testing.T) {
	upstream := &dialectUpstream{reply: "unused"}
	up := httptest.NewServer(upstream)
	defer up.Close()
	cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{
		runtimePolicy("runtime-post", "enforce", "block", true, nil),
		{ID: "pre", Mode: "enforce", KeyIDs: []string{"key_1"}, Checks: map[string]*tenant.GuardrailCheck{"custom-pre": {Enabled: true, Action: "block", Config: map[string]interface{}{"expression": "false"}}}},
	}}
	srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
	defer srv.asyncWorker.Stop()
	// A content-bearing sibling cannot inherit the catalog declaration.
	srv.router.Handle("GET", "/v1/models/{model}/generated", func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "uninspected-content") })
	gateway := httptest.NewServer(srv.httpServer.Handler)
	defer gateway.Close()
	for _, path := range []string{"/v1/models", "/v1/models/gpt-5.5"} {
		status, body := inspectionR3HTTP(t, gateway, "GET", path, "", inspectionR3Headers("a"))
		if status != 200 || !strings.Contains(body, "gpt-5.5") || upstream.served() != 0 {
			t.Fatalf("metadata-only path=%s status=%d calls=%d body=%s", path, status, upstream.served(), body)
		}
	}
	status, body := inspectionR3HTTP(t, gateway, "GET", "/v1/models/gpt-5.5/generated", "", inspectionR3Headers("a"))
	if status != 403 || strings.Contains(body, "uninspected-content") {
		t.Fatalf("content sibling status=%d body=%s", status, body)
	}
}

func TestInspectionR3DisabledControlsHTTP(t *testing.T) {
	for _, control := range []string{"auth-disabled", "engine-disabled", "no-selected-policy"} {
		t.Run(control, func(t *testing.T) {
			up := httptest.NewServer(&dialectUpstream{reply: "unused"})
			defer up.Close()
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
			base, _ := runtimePolicyServer(t, up.URL, cfg, nil)
			base.asyncWorker.Stop()
			engine := base.engine
			headers := inspectionR3Headers("a")
			switch control {
			case "auth-disabled":
				base.cfg.Auth.Enabled = false
				headers = nil
			case "engine-disabled":
				base.cfg.Guardrails.Enabled = false
				// As production wiring does, do not install the optional policy
				// plugin when the global guardrail engine is disabled.
				engine = pipeline.NewEngine(authplugin.New(base.keyStore, true))
			case "no-selected-policy":
				base.TenantStore.Set("org-runtime", &tenant.OrgConfig{})
			}
			srv := New(base.cfg, "", base.registry, engine, base.keyStore, nil, nil, nil, testModelDBPtr(), base.TenantStore, nil)
			defer srv.asyncWorker.Stop()
			srv.router.Handle("POST", "/forgotten", func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "control") })
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()
			status, body := inspectionR3HTTP(t, gateway, "POST", "/forgotten", "{}", headers)
			if status != 200 || body != "control" {
				t.Fatalf("status=%d body=%s", status, body)
			}
		})
	}
}

func TestInspectionR3NativeTerminalErrorHTTP(t *testing.T) {
	for _, dialect := range []string{"genai-error", "genai-api-error", "anthropic-error", "anthropic-api-error"} {
		t.Run(dialect, func(t *testing.T) {
			up := httptest.NewServer(&dialectUpstream{reply: "unused"})
			defer up.Close()
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
			srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
			defer srv.asyncWorker.Stop()
			srv.router.HandleInspected("GET", "/native-error", func(w http.ResponseWriter, r *http.Request) {
				err := &models.APIError{Status: 502, Message: "uninspected-error-content", Type: "uninspected-error-content", Code: "uninspected-error-content"}
				switch dialect {
				case "genai-error":
					genaifmt.WriteError(w, 502, "uninspected-error-content", err.Message)
				case "genai-api-error":
					genaifmt.WriteAPIError(w, err)
				case "anthropic-error":
					anthropicfmt.WriteError(w, 502, "uninspected-error-content", err.Message)
				case "anthropic-api-error":
					anthropicfmt.WriteAPIError(w, err)
				}
				fmt.Fprint(w, "uninspected-error-content")
			}, textInspection("test.native-error"))
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()
			status, body := inspectionR3HTTP(t, gateway, "GET", "/native-error", "", inspectionR3Headers("a"))
			wantEnvelope := `"type":"error"`
			if strings.HasPrefix(dialect, "genai") {
				wantEnvelope = `"status":"INTERNAL"`
			}
			if status != 502 || !strings.Contains(body, wantEnvelope) || strings.Contains(body, "uninspected-error-content") {
				t.Fatalf("status=%d body=%s", status, body)
			}
		})
	}
}
