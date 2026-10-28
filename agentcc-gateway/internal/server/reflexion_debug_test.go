package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestReflexionHTTPFirstCandidateDebug(t *testing.T) {
	for _, dynamic := range []bool{false, true} {
		for _, debug := range []bool{false, true} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("tenant=%t/debug=%t/stream=%t", dynamic, debug, stream), func(t *testing.T) {
					u := &reflectionUpstream{}
					upstream := httptest.NewServer(http.HandlerFunc(u.serve))
					defer upstream.Close()
					g := &reflectionCheck{name: "output"}
					cfg := map[string]interface{}{"stage": "post", "on_block": "reflect", "reflect_debug": debug}
					plugin := reflectionPlugin(g, cfg, "block", "post")
					if dynamic {
						store := tenant.NewStore()
						store.Set("org-a", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"output": {Enabled: true, Action: "block", Config: cfg}}}})
						plugin = guardrails.NewPlugin(nil, map[string]guardrails.Guardrail{"output": g}, nil, nil, store)
					}
					observer := &reflectionObserver{initial: map[string]string{"org_id": "org-a"}}
					s := createTestServerWithPlugins(t, upstream.URL, plugin, observer)
					w := callReflectionServer(s, stream)
					if w.Code != http.StatusOK || len(u.calls()) != 1 || observer.count != 1 || !strings.Contains(w.Body.String(), "accepted answer") {
						t.Fatalf("first candidate did not pass: status=%d calls=%d observers=%d body=%s", w.Code, len(u.calls()), observer.count, w.Body.String())
					}
					if !debug {
						for _, key := range []string{"reflexion_debug", "provider_attempt_usage"} {
							if _, present := observer.metadata[key]; present {
								t.Fatalf("debug metadata published without opt-in: %v", observer.metadata)
							}
						}
						if w.Header().Get("x-agentcc-reflexion-attempts") != "" || w.Header().Get("x-agentcc-provider-attempts") != "" {
							t.Fatalf("debug headers published without opt-in: %v", w.Header())
						}
						return
					}
					if observer.metadata["reflexion_debug"] != "true" || w.Header().Get("x-agentcc-reflexion-attempts") != "0" || w.Header().Get("x-agentcc-provider-attempts") != "1" {
						t.Fatalf("first passing attempt lost configured debug diagnostics: headers=%v metadata=%v", w.Header(), observer.metadata)
					}
					var attempts []models.ProviderAttempt
					if err := json.Unmarshal([]byte(observer.metadata["provider_attempt_usage"]), &attempts); err != nil || len(attempts) != 1 || attempts[0].Usage == nil || attempts[0].Usage.TotalTokens != 30 {
						t.Fatalf("missing single-attempt accounting: attempts=%+v error=%v", attempts, err)
					}
				})
			}
		}
	}
}
