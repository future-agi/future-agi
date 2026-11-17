package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Round-seven review: a valid provider total on either side of math/big's 1e6
// exponent limit is charged exactly, never the full admission hold. It covers a
// token hop, the CAT-2 catalogue hop, and a billable failed token hop before a
// catalogue hop (0.0000095 is 10 upstream micros, 11 billed). The long mantissa
// is 9.4999... upstream micros: 9 billed alone, 20 in the chain. Providers are
// synthetic loopback servers.
func TestOwnedR8BoundaryDecimalHTTP(t *testing.T) {
	const token, catalogue = "openai/o1-pro", "google/gemini-3.5-flash"
	for _, tc := range []struct {
		name, raw string
		billed    int64
	}{
		{"exp-999999", "1e-999999", 0},
		{"exp-1000001", "1e-1000001", 0},
		{"exp-2097152", "1e-2097152", 0},
		{"fraction-shifted", "0.00000949999999999999999e-999990", 0},
		{"long-mantissa", "0.0000094" + strings.Repeat("9", 1000000), 9},
	} {
		for _, shape := range []string{"token-single", "catalogue-single", "token-chain"} {
			for _, surface := range managedSurfaces {
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%s%s/stream=%t", tc.name, shape, surface, stream), func(t *testing.T) {
						g, _, _, prefix := ownedR4Gateway(t)
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}}
						model, want, calls := token, tc.billed, 1
						switch shape {
						case "catalogue-single":
							model = catalogue
						case "token-chain":
							cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{token: {catalogue}}}
							want, calls = tc.billed+11, 2
						}
						g.tenants.Set("org-m", cfg)
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, m string) {
							if shape == "token-chain" {
								if m == token {
									ownedR4Failure(w, m, tc.raw, stream)
									return
								}
								r601Response("0.0000095", stream)(w, r, m)
								return
							}
							r601Response(tc.raw, stream)(w, r, m)
						})
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						status := sendBillabilityBody(t, g, surface, ownedR6Body(model, stream))
						m := ownedR6Read(t, prefix)
						if status != http.StatusOK || g.managed.calls() != calls || m.spent != want || m.held != 0 || len(m.rows) != 1 {
							t.Fatalf("boundary decimal mispriced status=%d calls=%d spent=%d want=%d held=%d rows=%d hold=%d", status, g.managed.calls(), m.spent, want, m.held, len(m.rows), m.dispatchedHold())
						}
					})
				}
			}
		}
	}
}
