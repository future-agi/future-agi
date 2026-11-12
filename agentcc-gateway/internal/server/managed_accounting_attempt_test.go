package server

import (
	"fmt"
	"net/http"
	"strconv"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

func TestManagedAccountingReportedFailureRetryNeedsAnotherHold(t *testing.T) {
	for _, balance := range []int64{42000, 84000} {
		t.Run(strconv.FormatInt(balance, 10), func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			installBillabilityModels(billabilityModels(t))(t, g)
			g.ledger.SetMicros("org-m", balance)
			var calls atomic.Int32
			up := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
				w.Header().Set("Content-Type", "text/event-stream")
				if calls.Add(1) == 1 {
					writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":0,"completion_tokens":0,"cost":0.04}`)
					fmt.Fprint(w, "data: {\"error\":{\"code\":502,\"message\":\"upstream failed\"}}\n\n")
					return
				}
				textThenEnd(w, r, model)
			})
			g.managed.streamWith.Store(&up)
			status := sendManagedConfiguredRetry(t, g, "acme/request")
			spent, held := settledAccount(t, g)
			wantCalls := int32(balance / 42000)
			if calls.Load() != wantCalls || spent != balance || held != 0 {
				t.Fatalf("status=%d calls=%d want=%d spent=%d want=%d held=%d", status, calls.Load(), wantCalls, spent, balance, held)
			}
			if (status == 200) != (balance == 84000) {
				t.Errorf("status=%d balance=%d", status, balance)
			}
			if got := g.account(t, "org-m").AvailableMicros(); got < 0 {
				t.Errorf("overdraft=%d", got)
			}
			recs := g.outbox("org-m")
			if len(recs) != 1 || recs[0]["hold_micros"] != fmt.Sprint(balance) {
				t.Fatalf("reservation does not cover each billed attempt: %v", recs)
			}
			if (recs[0]["usage_estimated"] == "1") != (balance == 84000) {
				t.Errorf("request estimate provenance=%v", recs)
			}
		})
	}
}

func TestManagedAccountingServedEndpointIdentity(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/%t", surface, stream), func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				all := billabilityModels(t)
				eps, err := catalog.ParseOpenRouterEndpoints("acme/token", []byte(`{"data":{"id":"acme/token","endpoints":[{"tag":"alpha-compute","provider_name":"Alpha Compute","pricing":{"prompt":"0.000002","completion":"0.000010"}},{"tag":"beta","provider_name":"Beta","pricing":{"prompt":"0.000004","completion":"0.000020"}}]}}`))
				if err != nil {
					t.Fatal(err)
				}
				all["acme/token"].Endpoints = eps
				installBillabilityModels(all)(t, g)
				g.ledger.SetMicros("org-m", 1_000_000)
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					if stream {
						w.Header().Set("Content-Type", "text/event-stream")
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"hello there"}}]`)
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`)
						writeManagedChunk(w, model, `"provider":"ALPHA COMPUTE","choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
						fmt.Fprint(w, "data: [DONE]\n\n")
					} else {
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprintf(w, `{"model":%q,"provider":"ALPHA COMPUTE","choices":[{"index":0,"message":{"role":"assistant","content":"hello there"},"finish_reason":"stop"}],"usage":{"prompt_tokens":8,"completion_tokens":3}}`, model)
					}
				})
				if stream {
					g.managed.streamWith.Store(&up)
				} else {
					g.managed.responseWith.Store(&up)
				}
				status, _, _ := sendManaged(t, g, surface, "acme/token", stream, false, nil)
				spent, held := settledAccount(t, g)
				recs := g.outbox("org-m")
				if status != 200 || spent != 48 || held != 0 || len(recs) != 1 {
					t.Fatalf("status=%d spent=%d held=%d records=%v", status, spent, held, recs)
				}
				if recs[0]["served_upstream_provider"] != "ALPHA COMPUTE" || recs[0]["cost_source"] != "catalog" {
					t.Errorf("served identity lost: %v", recs)
				}
			})
		}
	}
}
