package server

import (
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
					if status != http.StatusOK || !strings.Contains(body, "gpt-5.5") || up.served() != 0 {
						t.Fatalf("status=%d provider_calls=%d body=%s", status, up.served(), body)
					}
				})
			}
			for _, tc := range []struct {
				name    string
				headers map[string]string
				status  int
			}{
				{"missing", nil, 401},
				{"invalid", map[string]string{"Authorization": "bearer invalid"}, 401},
				// Catalog has no second rejecting auth gate on dev: KeyAuth tries
				// every candidate. R5 restores that acceptance, not a strict override.
				{"invalid-priority-fallback", map[string]string{"Authorization": "bearer invalid", "x-api-key": "sk-agentcc-runtime-a"}, 200},
				{"valid-priority", map[string]string{"Authorization": "bearer sk-agentcc-runtime-a", "x-api-key": "invalid"}, 200},
				{"api-key", map[string]string{"x-api-key": "sk-agentcc-runtime-a"}, 200},
				{"caller-metadata", map[string]string{"x-agentcc-metadata": `{"auth_key_id":"key_1","org_id":"org-runtime"}`}, 401},
			} {
				t.Run(fmt.Sprintf("selected=%v/%s/%s", selected, path, tc.name), func(t *testing.T) {
					status, body := inspectionR3HTTP(t, gateway, "GET", path, "", tc.headers)
					if status != tc.status {
						t.Fatalf("status=%d want=%d body=%s", status, tc.status, body)
					}
				})
			}
		}
		// A conflicting internal key must not make the winning BYOK key see
		// global models. Both admission and catalog filtering use that winner.
		srv.handlers.keyStore.Get("key_1").KeyType = "byok"
		srv.TenantStore.Set("org-runtime", &tenant.OrgConfig{Guardrails: cfg, Providers: map[string]*tenant.ProviderConfig{
			"synthetic-org": {Enabled: true, APIKey: "synthetic", Models: []string{"org-only-model"}},
		}})
		for _, scheme := range []string{"Bearer", "bearer", "bEaReR", "Token"} {
			headers := map[string]string{"Authorization": scheme + " sk-agentcc-runtime-a", "x-api-key": "sk-agentcc-runtime-b"}
			status, body := inspectionR3HTTP(t, gateway, "GET", "/v1/models", "", headers)
			if status != 200 || !strings.Contains(body, "org-only-model") || strings.Contains(body, "gpt-5.5") {
				t.Fatalf("BYOK visibility status=%d body=%s", status, body)
			}
			status, _ = inspectionR3HTTP(t, gateway, "GET", "/v1/models/gpt-5.5", "", headers)
			if status != 404 {
				t.Fatalf("BYOK global model detail status=%d", status)
			}
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
