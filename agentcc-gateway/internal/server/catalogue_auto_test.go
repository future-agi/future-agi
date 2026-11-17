package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Auto owns its runners-up: it must build a same-price-family plan, not reject
// a valid primary as though the caller had declared a mixed fallback chain.
//
// Only /v1/chat/completions runs auto routing (resolveRoute). /v1/messages
// does not run route selection (managed_messages.go); there "auto" is a
// literal model id, so it is not part of this contract.
func TestCAT2AutoPricingFamily(t *testing.T) {
	const surface = "/v1/chat/completions"
	pools := []struct {
		name       string
		candidates []string
		mixed      bool
		onlyCAT2   bool
	}{
		{"token-primary", []string{"openai/gpt-5.4-mini", "google/gemini-3.5-flash"}, true, false},
		{"inclusive-primary", []string{"google/gemini-3.5-flash", "openai/o1-pro"}, true, false},
		{"prefixed-inclusive", []string{"openrouter/google/gemini-3.5-flash", "openai/o1-pro"}, true, false},
		{"token-pair", []string{"openai/gpt-5.4-mini", "openai/o1-pro"}, false, false},
		{"inclusive-pair", []string{"google/gemini-3.5-flash", "google/gemini-3.6-flash"}, false, true},
	}
	for _, enabled := range []bool{false, true} {
		for _, pool := range pools {
			for _, failPrimary := range []bool{false, true} {
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("on=%t/%s/fail=%t/stream=%t", enabled, pool.name, failPrimary, stream), func(t *testing.T) {
						g, _, _ := cat2OperatorGateway(t, enabled)
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false},
							Auto: &tenant.AutoRoutingConfig{Preference: "cost", Candidates: pool.candidates}}
						if enabled && pool.mixed {
							// A filtered-to-empty auto chain must override any old org
							// chain, not accidentally resurrect the crossing runner-up.
							cfg.Routing.ModelFallbacks = map[string][]string{
								pool.candidates[0]: {pool.candidates[1]}, pool.candidates[1]: {pool.candidates[0]},
							}
						}
						g.tenants.Set("org-m", cfg)
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
							if failPrimary && g.managed.calls() == 1 {
								w.WriteHeader(http.StatusServiceUnavailable)
								fmt.Fprint(w, `{"error":{"message":"synthetic primary failure"}}`)
								return
							}
							r601Response("0.0000095", stream)(w, r, model)
						})
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						snaps := make(chan *models.TraceSnapshot, 1)
						g.srv.handlers.SetGenerationRecorder(func(s *models.TraceSnapshot) { snaps <- s })
						body := fmt.Sprintf(`{"model":"auto","max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"synthetic"}]}`, stream)
						status := sendBillabilityBody(t, g, surface, body)
						wantStatus, wantCalls := http.StatusOK, 1
						if !enabled && pool.onlyCAT2 {
							wantStatus, wantCalls = http.StatusBadRequest, 0
						} else if failPrimary {
							if pool.mixed {
								// No same-family runner-up: the primary's upstream 5xx is
								// the answer, mapped to 502 (mapProviderStatus).
								wantStatus = http.StatusBadGateway
							} else {
								wantCalls = 2
							}
						}
						if status != wantStatus || g.managed.calls() != wantCalls {
							t.Fatalf("status=%d calls=%d want status=%d calls=%d", status, g.managed.calls(), wantStatus, wantCalls)
						}
						if wantStatus != http.StatusOK {
							// A refused or failed request writes no generation record
							// (failChat); calls already proves no crossing hop ran.
							return
						}
						select {
						case snap := <-snaps:
							fallbacks := snap.Metadata["auto_fallbacks"]
							if pool.mixed && fallbacks != "" {
								t.Fatalf("crossing runner-up retained in auto decision: %s", fallbacks)
							}
							if !pool.mixed && (fallbacks == "" || strings.Contains(fallbacks, ",")) {
								t.Fatalf("same-family runner-up lost: %q", fallbacks)
							}
							if enabled && pool.name == "inclusive-primary" && snap.Metadata["auto_model"] != pool.candidates[0] {
								t.Fatal("fixture did not exercise an inclusive primary")
							}
						case <-time.After(5 * time.Second):
							t.Fatal("no generation record")
						}
					})
				}
			}
		}
	}
}
