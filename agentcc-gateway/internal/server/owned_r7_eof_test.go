package server

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// HTTP 200 followed by EOF without any output or a rejection envelope is an
// accepted attempt with unknown liability. It must not be treated as a clean
// provider HTTP/SSE refusal merely because the gateway synthesizes status 502.
func TestOwnedR7AcceptedStreamEOFIsNotARejection(t *testing.T) {
	const primary, final, bridge = "openai/o1-pro", "google/gemini-3.5-flash", "owned/bridge"
	for _, body := range []string{"empty", "role-only"} {
		for _, shape := range []string{"single", "then-byok", "then-managed"} {
			for _, surface := range managedSurfaces {
				t.Run(fmt.Sprintf("%s/%s%s", body, shape, surface), func(t *testing.T) {
					g, _, _, prefix := ownedR4Gateway(t)
					cfg := managedOptIn()
					cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}}
					extra := int64(0)
					switch shape {
					case "then-byok":
						cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-owned-bridge", APIFormat: "openai", Models: []string{bridge}, Enabled: true}}
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {bridge}}}
					case "then-managed":
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {final}}}
						extra = 11
					}
					g.tenants.Set("org-m", cfg)
					answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
						if model != primary {
							r601Response("0.0000095", true)(w, r, model)
							return
						}
						w.Header().Set("Content-Type", "text/event-stream")
						w.WriteHeader(http.StatusOK)
						if body == "role-only" {
							fmt.Fprint(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\"}}]}\n\n")
						}
						// No error envelope, no [DONE], and no generation/usage evidence.
					})
					own := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) { r601Response("1000", true)(w, r, model) })
					g.managed.responseWith.Store(&answer)
					g.managed.streamWith.Store(&answer)
					g.operator.responseWith.Store(&own)
					g.operator.streamWith.Store(&own)
					status := sendBillabilityBody(t, g, surface, ownedR6Body(primary, true))
					m := ownedR6Read(t, prefix)
					var hold int64
					for _, a := range m.journal.Attempts {
						if a.Model == primary && a.Dispatched {
							hold += a.Micros
						}
					}
					if hold <= 0 {
						t.Fatal("missing admission hold")
					}
					if m.spent != hold+extra || m.held != 0 || len(m.rows) != 1 {
						t.Fatalf("accepted EOF lost liability status=%d spent=%d want=%d primary_hold=%d held=%d rows=%d", status, m.spent, hold+extra, hold, m.held, len(m.rows))
					}
				})
			}
		}
	}
}
