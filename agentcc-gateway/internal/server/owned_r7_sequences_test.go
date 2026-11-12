package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Permanent regression sequences from the round-six independent review.
// Providers are synthetic loopback HTTP servers.

func TestOwnedR7CleanFailureWithEmptyUsage(t *testing.T) {
	const primary, final, bridge = "openai/o1-pro", "google/gemini-3.5-flash", "owned/bridge"
	for _, evidence := range []string{"absent", "empty", "null-cost", "empty-tools"} {
		for _, shape := range []string{"single", "then-managed", "then-byok", "then-byok-mixed", "retry-rejected"} {
			for _, surface := range managedSurfaces {
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%s%s/stream=%t", evidence, shape, surface, stream), func(t *testing.T) {
						g, _, _, prefix := ownedR4Gateway(t)
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}}
						want, calls, byok := int64(0), 1, 0
						switch shape {
						case "then-managed":
							cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {final}}}
							want, calls = 11, 2
						case "then-byok", "then-byok-mixed":
							cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-owned-bridge", APIFormat: "openai", Models: []string{bridge}, Enabled: true}}
							cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {bridge}}}
							// MIXED-HOP-GUARD-1: the offered BYOK hop is refused by default;
							// the request answers with the managed primary's error.
							byok = 0
							if shape == "then-byok-mixed" {
								// With managed.mixed_billing_chains on the BYOK hop serves.
								allowMixedBillingChains(g)
								byok = 1
							}
						case "retry-rejected":
							cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "2ms", OnStatusCodes: []int{500, 502, 503}}}
							calls = 2
						}
						g.tenants.Set("org-m", cfg)
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
							if model != primary {
								r601Response("0.0000095", stream)(w, r, model)
								return
							}
							extra := ""
							switch evidence {
							case "empty":
								extra = `,"usage":{"prompt_tokens":0,"completion_tokens":0}`
							case "null-cost":
								extra = `,"usage":{"prompt_tokens":0,"completion_tokens":0,"cost":null}`
							case "empty-tools":
								extra = `,"usage":{"prompt_tokens":0,"completion_tokens":0,"server_tool_use":{}}`
							}
							if stream {
								w.Header().Set("Content-Type", "text/event-stream")
								fmt.Fprintf(w, "data: {\"error\":{\"code\":500,\"message\":\"request rejected before generation\"}%s}\n\ndata: [DONE]\n\n", extra)
							} else {
								w.Header().Set("Content-Type", "application/json")
								w.WriteHeader(500)
								fmt.Fprintf(w, `{"error":{"code":500,"message":"request rejected before generation"}%s}`, extra)
							}
						})
						own := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) { r601Response("1000", stream)(w, r, model) })
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						g.operator.responseWith.Store(&own)
						g.operator.streamWith.Store(&own)
						status := sendBillabilityBody(t, g, surface, ownedR6Body(primary, stream))
						m := ownedR6Read(t, prefix)
						if g.managed.calls() != calls || g.operator.calls() != byok {
							t.Fatalf("calls managed=%d byok=%d expected=%d/%d", g.managed.calls(), g.operator.calls(), calls, byok)
						}
						if m.spent != want || m.held != 0 || len(m.rows) != 1 {
							t.Fatalf("clean rejected request status=%d spent=%d want=%d held=%d rows=%d", status, m.spent, want, m.held, len(m.rows))
						}
					})
				}
			}
		}
	}
}

func TestOwnedR7UnknownLiabilityChainParity(t *testing.T) {
	const primary, final, bridge = "openai/o1-pro", "google/gemini-3.5-flash", "owned/bridge"
	for _, shape := range []string{"single", "then-managed", "then-byok"} {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s%s/stream=%t", shape, surface, stream), func(t *testing.T) {
					g, _, _, prefix := ownedR4Gateway(t)
					cfg := managedOptIn()
					cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}}
					extra := int64(0)
					switch shape {
					case "then-managed":
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {final}}}
						extra = 11
					case "then-byok":
						cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-owned-bridge", APIFormat: "openai", Models: []string{bridge}, Enabled: true}}
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {bridge}}}
					}
					g.tenants.Set("org-m", cfg)
					answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
						if model == primary {
							panic(http.ErrAbortHandler)
						}
						r601Response("0.0000095", stream)(w, r, model)
					})
					own := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) { r601Response("1000", stream)(w, r, model) })
					g.managed.responseWith.Store(&answer)
					g.managed.streamWith.Store(&answer)
					g.operator.responseWith.Store(&own)
					g.operator.streamWith.Store(&own)
					status := sendBillabilityBody(t, g, surface, ownedR6Body(primary, stream))
					m := ownedR6Read(t, prefix)
					var hold int64
					for _, a := range m.journal.Attempts {
						if a.Model == primary && a.Dispatched {
							hold += a.Micros
						}
					}
					if hold <= 0 {
						t.Fatal("missing owned admission hold")
					}
					if m.spent != hold+extra || m.held != 0 || len(m.rows) != 1 {
						t.Fatalf("unknown liability lost status=%d spent=%d want=%d primary_hold=%d held=%d rows=%d", status, m.spent, hold+extra, hold, m.held, len(m.rows))
					}
				})
			}
		}
	}
}

func TestOwnedR7EquivalentProviderDecimals(t *testing.T) {
	const primary, final = "openai/o1-pro", "google/gemini-3.5-flash"
	for name, raw := range map[string]string{"short": "0.00000949999999999999999", "padded-exponent": "0.00000949999999999999999e+000", "long": "0.0000094" + strings.Repeat("9", 100)} {
		for _, shape := range []string{"single", "chain"} {
			for _, surface := range managedSurfaces {
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%s%s/stream=%t", name, shape, surface, stream), func(t *testing.T) {
						g, _, _, prefix := ownedR4Gateway(t)
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}}
						want := int64(9)
						if shape == "chain" {
							cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {final}}}
							want += 11
						}
						g.tenants.Set("org-m", cfg)
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
							if model != primary {
								r601Response("0.0000095", stream)(w, r, model)
								return
							}
							if shape == "chain" {
								ownedR4Failure(w, model, raw, stream)
							} else {
								r601Response(raw, stream)(w, r, model)
							}
						})
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						status := sendBillabilityBody(t, g, surface, ownedR6Body(primary, stream))
						m := ownedR6Read(t, prefix)
						if status != http.StatusOK || m.spent != want || m.held != 0 || len(m.rows) != 1 {
							t.Fatalf("wire decimal changed charge status=%d spent=%d want=%d held=%d rows=%d", status, m.spent, want, m.held, len(m.rows))
						}
					})
				}
			}
		}
	}
}
