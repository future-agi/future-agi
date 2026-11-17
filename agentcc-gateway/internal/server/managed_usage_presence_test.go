package server

import (
	"fmt"
	"net/http"
	"testing"
)

func TestManagedAccountingUsagePresenceMatrix(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			for _, output := range []string{"text", "tool"} {
				for _, tc := range []struct {
					name, usage string
					text, tool  int64
					estimated   bool
				}{
					{"omitted", "", 48, 38, true},
					{"empty", `{}`, 48, 38, true},
					{"null", `null`, 48, 38, true},
					{"null-counters", `{"prompt_tokens":null,"completion_tokens":null}`, 48, 38, true},
					{"unsupported-only", `{"other_tokens":0}`, 48, 38, true},
					{"total-only", `{"total_tokens":0}`, 48, 38, true},
					{"prompt-only", `{"prompt_tokens":8}`, 48, 38, true},
					{"output-only", `{"completion_tokens":1}`, 27, 27, true},
					{"partial-zero", `{"prompt_tokens":0}`, 32, 21, true},
					{"zero-counters", `{"prompt_tokens":0,"completion_tokens":0}`, 0, 0, false},
					{"zero-cost", `{"cost":0}`, 0, 0, false},
					{"reported-counters", `{"prompt_tokens":8,"completion_tokens":3}`, 48, 48, false},
					{"reported-cost", `{"cost":0.001}`, 1050, 1050, false},
					{"invalid-cost", `{"cost":-1}`, 48, 38, true},
				} {
					t.Run(fmt.Sprintf("%s/stream=%t/%s/%s", surface, stream, output, tc.name), func(t *testing.T) {
						g := newManagedTestGateway(t, managedOptIn())
						installBillabilityModels(billabilityModels(t))(t, g)
						g.ledger.SetMicros("org-m", 1_000_000)
						up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
							u := ""
							if tc.usage != "" {
								u = `,"usage":` + tc.usage
							}
							message := `{"role":"assistant","content":"hello there"}`
							delta := `{"content":"hello there"}`
							if output == "tool" {
								message = `{"role":"assistant","tool_calls":[{"id":"call_presence","type":"function","function":{"name":"lookup","arguments":"{}"}}]}`
								delta = `{"tool_calls":[{"index":0,"id":"call_presence","type":"function","function":{"name":"lookup","arguments":"{}"}}]}`
							}
							if !stream {
								w.Header().Set("Content-Type", "application/json")
								fmt.Fprintf(w, `{"model":%q,"choices":[{"index":0,"message":%s,"finish_reason":"stop"}]%s}`, model, message, u)
								return
							}
							w.Header().Set("Content-Type", "text/event-stream")
							writeManagedChunk(w, model, `"choices":[{"index":0,"delta":`+delta+`}]`)
							writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`+u)
							fmt.Fprint(w, "data: [DONE]\n\n")
						})
						g.managed.streamWith.Store(&up)
						g.managed.responseWith.Store(&up)
						status, _, _ := sendManaged(t, g, surface, "acme/token", stream, false, nil)
						spent, held := settledAccount(t, g)
						want := tc.text
						if output == "tool" {
							want = tc.tool
						}
						recs := g.outbox("org-m")
						if status != 200 || spent != want || held != 0 || len(recs) != 1 {
							t.Fatalf("status=%d spent=%d want=%d held=%d records=%d", status, spent, want, held, len(recs))
						}
						if got := recs[0]["usage_estimated"] == "1"; got != tc.estimated {
							t.Errorf("estimate provenance=%t want=%t", got, tc.estimated)
						}
					})
				}
			}
		}
	}
}

func TestManagedAccountingEmptyUsageTrustedFree(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/%t/free", surface, stream), func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				installBillabilityModels(billabilityModels(t))(t, g)
				g.ledger.SetMicros("org-m", 1_000_000) // Admission still requires an active funded account; settlement must remain zero.
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					if stream {
						w.Header().Set("Content-Type", "text/event-stream")
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"hello there"},"finish_reason":"stop"}],"usage":{}`)
						fmt.Fprint(w, "data: [DONE]\n\n")
					} else {
						fmt.Fprintf(w, `{"model":%q,"choices":[{"index":0,"message":{"content":"hello there"},"finish_reason":"stop"}],"usage":{}}`, model)
					}
				})
				g.managed.streamWith.Store(&up)
				g.managed.responseWith.Store(&up)
				status, _, _ := sendManaged(t, g, surface, "acme/listed:free", stream, false, nil)
				spent, held := settledAccount(t, g)
				if status != 200 || spent != 0 || held != 0 {
					t.Fatalf("status=%d spent=%d held=%d", status, spent, held)
				}
			})
		}
	}
}
