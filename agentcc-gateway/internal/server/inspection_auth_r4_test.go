package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Synthetic localhost requests exercise the actual middleware, registered
// catalog handlers, declaration admission, and credential precedence.
func TestInspectionR4CatalogAuthorizationHTTP(t *testing.T) {
	for _, selected := range []bool{false, true} {
		cfg := &tenant.GuardrailConfig{}
		if selected {
			cfg.KeyPolicies = []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}
		}
		up := &dialectUpstream{reply: "unused"}
		upstream := httptest.NewServer(up)
		srv, _ := runtimePolicyServer(t, upstream.URL, cfg, nil)
		srv.router.handleInspected("GET", "/catalog-principal", func(w http.ResponseWriter, r *http.Request) {
			org, key := models.InspectionFromContext(r.Context()).Principal()
			fmt.Fprintf(w, "%s/%s", org, key)
		}, RouteInspection{Auth: inspectionAuthCatalog})
		gateway := httptest.NewServer(srv.httpServer.Handler)
		for _, tc := range []struct{ bearer, other, principal string }{
			{"a", "b", "org-runtime/key_1"},
			{"b", "a", "org-runtime/key_2"},
		} {
			status, body := inspectionR3HTTP(t, gateway, "GET", "/catalog-principal", "", map[string]string{
				"Authorization":      "bEaReR sk-agentcc-runtime-" + tc.bearer,
				"x-api-key":          "sk-agentcc-runtime-" + tc.other,
				"x-agentcc-metadata": `{"auth_key_id":"caller-spoof","org_id":"caller-spoof"}`,
			})
			if selected && tc.bearer == "a" {
				// A probe is not the catalog's exact metadata-only operation:
				// selected unknown output must still refuse before its handler.
				if status != 403 || !strings.Contains(body, "output_guardrail_unsupported") {
					t.Fatalf("selected winner status=%d body=%s", status, body)
				}
			} else if status != 200 || body != tc.principal {
				t.Fatalf("principal status=%d got=%s want=%s", status, body, tc.principal)
			}
		}
		for _, path := range []string{"/v1/models", "/v1/models/gpt-5.5"} {
			for _, scheme := range []string{"Bearer", "bearer", "BEARER", "bEaReR", "Token", "token"} {
				t.Run(fmt.Sprintf("selected=%v/%s/%s", selected, path, scheme), func(t *testing.T) {
					status, body := inspectionR3HTTP(t, gateway, "GET", path, "", map[string]string{"Authorization": scheme + " sk-agentcc-runtime-a"})
					visible := scheme == "Bearer"
					want := http.StatusOK
					if path == "/v1/models/gpt-5.5" && !visible {
						want = http.StatusNotFound
					}
					if status != want || up.served() != 0 {
						t.Fatalf("status=%d want=%d provider_calls=%d body=%s", status, want, up.served(), body)
					}
					inspectionR4AssertModelCatalogue(t, path, []byte(body), visible, selected)
				})
			}
			for _, tc := range []struct {
				name           string
				headers        map[string]string
				status         int
				catalogVisible bool
			}{
				{"missing", nil, 401, false},
				{"invalid", map[string]string{"Authorization": "bearer invalid"}, 401, false},
				// Admission tries every candidate. Catalogue visibility separately
				// uses dev's exact Bearer lookup, otherwise x-api-key.
				{"invalid-priority-fallback", map[string]string{"Authorization": "bearer invalid", "x-api-key": "sk-agentcc-runtime-a"}, 200, true},
				{"valid-priority", map[string]string{"Authorization": "bearer sk-agentcc-runtime-a", "x-api-key": "invalid"}, 200, false},
				{"exact-invalid-priority", map[string]string{"Authorization": "Bearer invalid", "x-api-key": "sk-agentcc-runtime-a"}, 200, false},
				{"exact-valid-priority", map[string]string{"Authorization": "Bearer sk-agentcc-runtime-a", "x-api-key": "invalid"}, 200, true},
				{"api-key", map[string]string{"x-api-key": "sk-agentcc-runtime-a"}, 200, true},
				{"caller-metadata", map[string]string{"x-agentcc-metadata": `{"auth_key_id":"key_1","org_id":"org-runtime"}`}, 401, false},
			} {
				t.Run(fmt.Sprintf("selected=%v/%s/%s", selected, path, tc.name), func(t *testing.T) {
					status, body := inspectionR3HTTP(t, gateway, "GET", path, "", tc.headers)
					want := tc.status
					if want == http.StatusOK && path == "/v1/models/gpt-5.5" && !tc.catalogVisible {
						want = http.StatusNotFound
					}
					if status != want {
						t.Fatalf("status=%d want=%d body=%s", status, want, body)
					}
					if tc.status == http.StatusOK {
						inspectionR4AssertModelCatalogue(t, path, []byte(body), tc.catalogVisible, selected)
					}
				})
			}
		}
		// Exact Bearer keeps the BYOK catalogue despite an internal x-api-key.
		// Non-exact/Token headers instead select that x-api-key for visibility,
		// as dev does; the middleware principal above remains independently bound.
		srv.handlers.keyStore.Get("key_1").KeyType = "byok"
		srv.TenantStore.Set("org-runtime", &tenant.OrgConfig{Guardrails: cfg, Providers: map[string]*tenant.ProviderConfig{
			"synthetic-org": {Enabled: true, APIKey: "synthetic", Models: []string{"org-only-model"}},
		}})
		for _, scheme := range []string{"Bearer", "bearer", "bEaReR", "Token"} {
			headers := map[string]string{"Authorization": scheme + " sk-agentcc-runtime-a", "x-api-key": "sk-agentcc-runtime-b"}
			globalVisible := scheme != "Bearer"
			status, body := inspectionR3HTTP(t, gateway, "GET", "/v1/models", "", headers)
			if status != 200 || !strings.Contains(body, "org-only-model") || strings.Contains(body, "gpt-5.5") != globalVisible {
				t.Fatalf("scheme=%s global_visible=%t status=%d body=%s", scheme, globalVisible, status, body)
			}
			status, body = inspectionR3HTTP(t, gateway, "GET", "/v1/models/gpt-5.5", "", headers)
			want := http.StatusNotFound
			if globalVisible {
				want = http.StatusOK
			}
			if status != want {
				t.Fatalf("scheme=%s global model detail status=%d want=%d", scheme, status, want)
			}
			inspectionR4AssertModelCatalogue(t, "/v1/models/gpt-5.5", []byte(body), globalVisible, selected)
		}
		for _, headers := range []map[string]string{
			{"Authorization": "Bearer sk-agentcc-runtime-a"},
			{"x-api-key": "sk-agentcc-runtime-a"},
		} {
			status, body := inspectionR3HTTP(t, gateway, "GET", "/v1/models", "", headers)
			if status != 200 || !strings.Contains(body, "org-only-model") || strings.Contains(body, "gpt-5.5") {
				t.Fatalf("BYOK-only visibility status=%d body=%s", status, body)
			}
			status, body = inspectionR3HTTP(t, gateway, "GET", "/v1/models/gpt-5.5", "", headers)
			if status != http.StatusNotFound {
				t.Fatalf("BYOK-only global model detail status=%d", status)
			}
			inspectionR4AssertModelCatalogue(t, "/v1/models/gpt-5.5", []byte(body), false, selected)
		}
		expired := time.Now().Add(-time.Hour)
		srv.handlers.keyStore.Get("key_1").ExpiresAt = &expired
		status, _ := inspectionR3HTTP(t, gateway, "GET", "/v1/models", "", map[string]string{"Authorization": "bearer sk-agentcc-runtime-a"})
		if status != 401 {
			t.Fatalf("expired status=%d", status)
		}
		gateway.Close()
		srv.asyncWorker.Stop()
		upstream.Close()
	}
}

func inspectionR4AssertModelCatalogue(t *testing.T, path string, body []byte, visible, selected bool) {
	t.Helper()
	if !selected || visible || path != "/v1/models/gpt-5.5" {
		runtimeR5AssertModelCatalogue(t, path, body, visible)
		return
	}
	// Selected inspection retains the handler's 404 but sanitizes its error.
	// The callers assert status separately; do not require the legacy body here.
	var detail struct {
		ID    string `json:"id"`
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &detail); err != nil {
		t.Fatal(err)
	}
	if detail.ID != "" || detail.Error.Code != "request_failed" ||
		detail.Error.Message != "Request failed before inspected content could be released" ||
		strings.Contains(string(body), "gpt-5.5") {
		t.Fatalf("selected hidden catalog model did not retain sanitized error: %s", body)
	}
}
