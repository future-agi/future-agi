package server

import (
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

// Independent review probes for the managed stream start and the chat
// stream's terminal ordering, kept as regressions.

const recheck3CheapExpensive = `{"data":{"id":"acme/token","endpoints":[{"tag":"cheap","provider_name":"Cheap Compute","pricing":{"prompt":"0.000002","completion":"0.000010"}},{"tag":"expensive","provider_name":"Expensive Compute","pricing":{"prompt":"0.000004","completion":"0.000020"}}]}}`

func recheck3Gateway(t *testing.T, endpoints string) *managedTestGateway {
	t.Helper()
	g := newManagedTestGateway(t, managedOptIn())
	all := billabilityModels(t)
	if endpoints != "" {
		eps, err := catalog.ParseOpenRouterEndpoints("acme/token", []byte(endpoints))
		if err != nil {
			t.Fatal(err)
		}
		all["acme/token"].Endpoints = eps
	}
	installBillabilityModels(all)(t, g)
	g.ledger.SetMicros("org-m", 1_000_000)
	return g
}

// An upstream that finishes without visible output (hidden reasoning used the
// whole budget, a content filter, an empty answer, a tool finish with no
// tool) completed a generation. The stream starts at the non-error finish
// chunk: HTTP 200, one dispatch, the reported usage billed once, or an
// estimate when none was reported. The nonstream control below answers the
// same generation with 200.
func TestRecheck3FinishOnlyStream(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, reason := range []string{"length", "content_filter", "stop", "tool_calls"} {
			for _, usage := range []string{"reported", "none"} {
				t.Run(surface+"/"+reason+"/"+usage, func(t *testing.T) {
					g := recheck3Gateway(t, "")
					up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
						w.Header().Set("Content-Type", "text/event-stream")
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]`)
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"`+reason+`"}]`)
						if usage == "reported" {
							writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11}`)
						}
						fmt.Fprint(w, "data: [DONE]\n\n")
					})
					g.managed.streamWith.Store(&up)
					status, body, complete := sendManagedWithin(t, g, surface, "acme/token", 10*time.Second)
					spent, held := settledAccount(t, g)
					calls := g.managed.calls()
					t.Logf("finish-only surface=%s reason=%s usage=%s status=%d complete=%t calls=%d spent=%d held=%d", surface, reason, usage, status, complete, calls, spent, held)
					if !complete || status != 200 || calls != 1 || held != 0 {
						t.Errorf("completed empty generation became a failure: complete=%t status=%d calls=%d held=%d body=%.300s", complete, status, calls, held, body)
					}
					if usage == "reported" && spent != 48 {
						t.Errorf("completed empty generation billed %d micros, want its reported usage once (48)", spent)
					}
					if usage == "none" && spent <= 0 {
						t.Errorf("completed empty generation without usage was not billed on an estimate: spent=%d", spent)
					}
				})
			}
		}
	}
}

func TestRecheck3EmptyNonstreamParity(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, reason := range []string{"length", "content_filter"} {
			t.Run(surface+"/"+reason, func(t *testing.T) {
				g := recheck3Gateway(t, "")
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":""},"finish_reason":%q}],"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11}}`, model, reason)
				})
				g.managed.responseWith.Store(&up)
				status, _, _ := sendManaged(t, g, surface, "acme/token", false, false, nil)
				spent, held := settledAccount(t, g)
				calls := g.managed.calls()
				t.Logf("nonstream-parity surface=%s reason=%s status=%d calls=%d spent=%d held=%d", surface, reason, status, calls, spent, held)
				if status != 200 || calls != 1 || spent != 48 || held != 0 {
					t.Errorf("nonstream empty generation: status=%d calls=%d spent=%d held=%d", status, calls, spent, held)
				}
			})
		}
	}
}

// A stream that started, then failed with an error envelope naming its
// endpoint and reporting usage: the envelope's evidence prices the request
// at the named endpoint (48), not at the reserve card. The adapter queues
// the evidence before the error; TestManagedChatDrainsQueuedEvidence pins
// the ordering without depending on when the adapter runs.
func TestRecheck3MidStreamEnvelope(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			g := recheck3Gateway(t, recheck3CheapExpensive)
			up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				w.Header().Set("Content-Type", "text/event-stream")
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":"hello there"}}]`)
				fmt.Fprint(w, `data: {"provider":"Cheap Compute","error":{"code":502,"message":"fixture failure"},"usage":{"prompt_tokens":8,"completion_tokens":3}}`+"\n\n")
			})
			g.managed.streamWith.Store(&up)
			status, _, _ := sendManaged(t, g, surface, "acme/token", true, false, nil)
			spent, held := settledAccount(t, g)
			t.Logf("mid-stream surface=%s status=%d calls=%d spent=%d held=%d", surface, status, g.managed.calls(), spent, held)
			if status != 200 || spent != 48 || held != 0 {
				t.Errorf("mid-stream named failure: status=%d spent=%d want=48 held=%d", status, spent, held)
			}
		})
	}
}
