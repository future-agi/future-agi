package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestInspectedDirectCompletionPreservesOpenOrgCircuit(t *testing.T) {
	srv, wire, _ := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy("enforce", "block", "pii-detection", nil))})
	defer srv.asyncWorker.Stop()
	key := srv.keyStore.Get("key_1")
	key.KeyType = "managed"
	key.SetBalanceMicros(10_000_000)
	org := srv.TenantStore.Get("org-r2")
	org.Providers = map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "synthetic-org-key", Models: []string{"gpt-3.5-turbo-instruct"}},
	}
	srv.TenantStore.Set("org-r2", org)
	cfg := reconciliationConfig()
	cfg.Cooldown = time.Hour
	reg := routing.NewCircuitBreakerRegistry(cfg, nil)
	srv.handlers.healthMonitor.SetCircuitBreakerRegistry(reg)
	openReconciliationBreaker(t, reg.GetScoped("org-r2", "openai"))

	w := routeR2Send(srv, "/v1/completions", `{"model":"gpt-3.5-turbo-instruct","prompt":"clean text"}`, "a")
	paths, _ := wire.snapshot()
	var body models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if len(paths) != 0 || w.Code != http.StatusServiceUnavailable || body.Error.Code != "circuit_open" ||
		body.Error.Message != "Provider circuit is open" || body.Error.Type != models.ErrTypeServer {
		t.Fatalf("upstream calls=%d status=%d body=%s", len(paths), w.Code, w.Body.String())
	}
}

func TestInspectionAdmissionRequiresLocalOrigin(t *testing.T) {
	for _, tc := range []struct {
		name, code, message string
		err                 error
	}{
		{"local-selection", "all_providers_unavailable", "All providers are unavailable", routing.ProvidersUnavailableError("uninspected-model-name")},
		{"same-code-unmarked-selection", "request_failed", "Request failed before inspected content could be released", models.ErrServiceUnavailable("uninspected-model-name")},
		{"same-code-unmarked-circuit", "request_failed", "Request failed before inspected content could be released", &models.APIError{Status: 503, Code: "circuit_open", Type: models.ErrTypeServer, Message: "uninspected-model-name"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			state := models.NewInspectionState(textInspection("test.admission"))
			state.BindPrincipal("org", "key")
			state.Require(models.InspectionInput)
			dst := httptest.NewRecorder()
			guard, w := newInspectionWriter(dst, state)
			models.WriteErrorFromError(w, tc.err)
			guard.finish()
			var body models.ErrorResponse
			if err := json.Unmarshal(dst.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if dst.Code != http.StatusServiceUnavailable || body.Error.Code != tc.code || body.Error.Message != tc.message || body.Error.Type != models.ErrTypeServer {
				t.Fatalf("status=%d body=%s", dst.Code, dst.Body.String())
			}
		})
	}
}

func TestInspectedCompletionMasksForgedUpstreamAdmissionCodes(t *testing.T) {
	for _, code := range []string{"circuit_open", "all_providers_unavailable"} {
		t.Run(code, func(t *testing.T) {
			var calls atomic.Int32
			up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					fmt.Fprint(w, `{"data":[]}`)
					return
				}
				calls.Add(1)
				w.WriteHeader(http.StatusServiceUnavailable)
				fmt.Fprintf(w, `{"error":{"code":%q,"type":"server_error","message":"uninspected-provider-body"}}`, code)
			}))
			defer up.Close()
			srv, _ := runtimePolicyServer(t, up.URL, routeR2Org(runtimePolicy("runtime-post", "enforce", "block", true, nil)), nil)
			defer srv.asyncWorker.Stop()
			rt := guardedRoute{"completion", "/v1/completions", `{"model":"gpt-5.5","stream":%v,"prompt":%q}`}
			w := runtimePolicySend(srv, rt, false, "a")
			var body models.ErrorResponse
			if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			// The OpenAI adapter maps a vendor 503 to 502; neither its code nor its body is trusted.
			if calls.Load() != 1 || w.Code != http.StatusBadGateway || body.Error.Code != "request_failed" || strings.Contains(w.Body.String(), "uninspected-provider-body") {
				t.Fatalf("upstream calls=%d status=%d body=%s", calls.Load(), w.Code, w.Body.String())
			}
		})
	}
}
