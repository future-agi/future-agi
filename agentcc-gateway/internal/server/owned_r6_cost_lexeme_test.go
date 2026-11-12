package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ownedR6LaterLexemes are explicit cost values a later usage object may carry
// after a valid provider total: null, a string, a negative number and other
// valid JSON that is not a number.
var ownedR6LaterLexemes = map[string]string{
	"null": `null`, "string": `"0.00005"`, "negative": `-1`, "boolean": `true`, "object": `{"usd":0.00005}`,
}

// ownedR6ValidThenLater streams a valid provider total followed by a later
// usage object carrying lexeme. A failing stream reports its total before an
// error event; a served stream reports it after its output.
func ownedR6ValidThenLater(w http.ResponseWriter, model, lexeme string, fail bool) {
	w.Header().Set("Content-Type", "text/event-stream")
	if !fail {
		fmt.Fprintf(w, "data: {\"id\":\"gen-r6\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"generated\"}}]}\n\n", model)
	}
	fmt.Fprintf(w, "data: {\"id\":\"gen-r6\",\"model\":%q,\"choices\":[],\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":1,\"cost\":0.00005}}\n\n", model)
	if fail {
		fmt.Fprintf(w, "data: {\"id\":\"gen-r6\",\"model\":%q,\"error\":{\"code\":500,\"message\":\"billable failure\"},\"usage\":{\"cost\":%s}}\n\n", model, lexeme)
	} else {
		fmt.Fprintf(w, "data: {\"id\":\"gen-r6\",\"model\":%q,\"choices\":[],\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":1,\"cost\":%s}}\n\n", model, lexeme)
	}
	fmt.Fprint(w, "data: [DONE]\n\n")
}

// A later invalid cost contests the interim total. Both single and chained
// attempts now resolve to their full hold through the same liability table.
func TestOwnedR6LaterInvalidCostLexemeKeepsEarlierTotal(t *testing.T) {
	const primary, final = "openai/o1-pro", "google/gemini-3.5-flash"
	for name, lexeme := range ownedR6LaterLexemes {
		for _, shape := range []string{"single", "chain"} {
			for _, surface := range managedSurfaces {
				t.Run(fmt.Sprintf("%s/%s%s", name, shape, surface), func(t *testing.T) {
					g, _, _, prefix := ownedR4Gateway(t)
					cfg := managedOptIn()
					cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}}
					want, calls := int64(0), 1
					if shape == "chain" {
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {final}}}
						want, calls = 11, 2
					}
					g.tenants.Set("org-m", cfg)
					managed := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
						if model == primary {
							ownedR6ValidThenLater(w, model, lexeme, shape == "chain")
							return
						}
						r601Response("0.0000095", true)(w, r, model)
					})
					g.managed.responseWith.Store(&managed)
					g.managed.streamWith.Store(&managed)
					status := sendBillabilityBody(t, g, surface, ownedR6Body(primary, true))
					m := ownedR6Read(t, prefix)
					for _, a := range m.journal.Attempts {
						if a.Model == primary && a.Dispatched {
							want += a.Micros
						}
					}
					if status != http.StatusOK || g.managed.calls() != calls || m.spent != want || m.held != 0 || len(m.rows) != 1 {
						t.Fatalf("earlier valid total lost: status=%d calls=%d spent=%d want=%d held=%d rows=%d hold=%d", status, g.managed.calls(), m.spent, want, m.held, len(m.rows), m.dispatchedHold())
					}
				})
			}
		}
	}
}

// A single valid JSON cost the exact decimal grammar does not accept (negative
// zero, a three- or four-digit exponent, an over-long number) is still the
// provider's valid total. It is parsed exactly, never repriced through a float.
func TestOwnedR6ReportedCostOutsideExactGrammar(t *testing.T) {
	const primary, final = "openai/o1-pro", "google/gemini-3.5-flash"
	long := "0.00005" + strings.Repeat("0", 96)
	for _, tc := range []struct {
		name, lexeme string
		billed       int64
	}{
		{"negative-zero", "-0", 0}, {"tiny-exponent", "1e-100", 0}, {"padded-exponent", "5e-0005", 53}, {"long", long, 53},
	} {
		for _, shape := range []string{"single", "chain"} {
			for _, surface := range managedSurfaces {
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%s%s/stream=%t", tc.name, shape, surface, stream), func(t *testing.T) {
						g, _, _, prefix := ownedR4Gateway(t)
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}}
						want, calls := tc.billed, 1
						if shape == "chain" {
							cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {final}}}
							want, calls = tc.billed+11, 2
						}
						g.tenants.Set("org-m", cfg)
						managed := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
							switch {
							case model == primary && shape == "chain":
								ownedR4Failure(w, model, tc.lexeme, stream)
							case model == primary:
								r601Response(tc.lexeme, stream)(w, r, model)
							default:
								r601Response("0.0000095", stream)(w, r, model)
							}
						})
						g.managed.responseWith.Store(&managed)
						g.managed.streamWith.Store(&managed)
						status := sendBillabilityBody(t, g, surface, ownedR6Body(primary, stream))
						m := ownedR6Read(t, prefix)
						if status != http.StatusOK || g.managed.calls() != calls || m.spent != want || m.held != 0 || len(m.rows) != 1 {
							t.Fatalf("valid total mispriced: status=%d calls=%d spent=%d want=%d held=%d rows=%d hold=%d", status, g.managed.calls(), m.spent, want, m.held, len(m.rows), m.dispatchedHold())
						}
					})
				}
			}
		}
	}
}
