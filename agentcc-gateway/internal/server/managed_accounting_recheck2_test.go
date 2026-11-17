package server

import (
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"net/http"
	"testing"
)

func TestRecheck2EmptyStreamUsage(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tc := range []struct {
			name, usage string
			want        int64
		}{
			{"absent-counters", `{}`, 48},
			{"explicit-zero-counters", `{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}`, 0},
			{"explicit-zero-cost", `{"cost":0}`, 0},
		} {
			t.Run(surface+"/"+tc.name, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				installBillabilityModels(billabilityModels(t))(t, g)
				g.ledger.SetMicros("org-m", 1_000_000)
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "text/event-stream")
					writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"hello there"}}]`)
					writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`)
					writeManagedChunk(w, model, `"choices":[],"usage":`+tc.usage)
					fmt.Fprint(w, "data: [DONE]\n\n")
				})
				g.managed.streamWith.Store(&up)
				status, _, _ := sendManaged(t, g, surface, "acme/token", true, false, nil)
				spent, held := settledAccount(t, g)
				t.Logf("OBS status=%d spent=%d want=%d held=%d", status, spent, tc.want, held)
				if status != 200 || spent != tc.want || held != 0 {
					t.Errorf("absent usage dimensions are not explicit zero measurements: spent=%d want=%d", spent, tc.want)
				}
			})
		}
	}
}

func TestRecheck2FailedEndpointLocations(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, location := range []string{"usage-event", "error-envelope"} {
			t.Run(surface+"/"+location, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				all := billabilityModels(t)
				eps, err := catalog.ParseOpenRouterEndpoints("acme/token", []byte(`{"data":{"id":"acme/token","endpoints":[{"tag":"cheap","provider_name":"Cheap Compute","pricing":{"prompt":"0.000002","completion":"0.000010"}},{"tag":"expensive","provider_name":"Expensive Compute","pricing":{"prompt":"0.000004","completion":"0.000020"}}]}}`))
				if err != nil {
					t.Fatal(err)
				}
				all["acme/token"].Endpoints = eps
				installBillabilityModels(all)(t, g)
				g.ledger.SetMicros("org-m", 1_000_000)
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "text/event-stream")
					if location == "usage-event" {
						writeManagedChunk(w, model, `"provider":"Cheap Compute","choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
						fmt.Fprint(w, `data: {"error":{"code":502,"message":"fixture failure"}}`+"\n\n")
					} else {
						fmt.Fprint(w, `data: {"provider":"Cheap Compute","error":{"code":502,"message":"fixture failure"},"usage":{"prompt_tokens":8,"completion_tokens":3}}`+"\n\n")
					}
				})
				g.managed.streamWith.Store(&up)
				status, _, _ := sendManaged(t, g, surface, "acme/token", true, false, nil)
				spent, held := settledAccount(t, g)
				// No retry is configured: Messages follows chat's retry policy.
				wantCalls := 1
				want := int64(48 * wantCalls)
				t.Logf("OBS status=%d calls=%d spent=%d want=%d held=%d", status, g.managed.calls(), spent, want, held)
				if status != 502 || g.managed.calls() != wantCalls || spent != want || held != 0 {
					t.Errorf("known failed endpoint used another endpoint's price: spent=%d want=%d", spent, want)
				}
			})
		}
	}
}
