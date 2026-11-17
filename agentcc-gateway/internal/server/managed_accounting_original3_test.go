package server

// Original third-recheck matrix retained alongside the stronger finish-only probes.
import (
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

const originalRecheck3CheapExpensive = `{"data":{"id":"acme/token","endpoints":[{"tag":"cheap","provider_name":"Cheap Compute","pricing":{"prompt":"0.000002","completion":"0.000010"}},{"tag":"expensive","provider_name":"Expensive Compute","pricing":{"prompt":"0.000004","completion":"0.000020"}}]}}`

// Two endpoints share one provider name at different prices: a named failure
// cannot be attributed to either, so it must price at the reserve (max) card.
const originalRecheck3Ambiguous = `{"data":{"id":"acme/token","endpoints":[{"tag":"cheap-a","provider_name":"Cheap Compute","pricing":{"prompt":"0.000002","completion":"0.000010"}},{"tag":"cheap-b","provider_name":"Cheap Compute","pricing":{"prompt":"0.000004","completion":"0.000020"}}]}}`

func originalRecheck3Gateway(t *testing.T, endpoints string) *managedTestGateway {
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

// An upstream that finishes without visible text (hidden reasoning used the
// whole budget, a content filter, or an empty answer) and reports its usage
// is a completed generation. The pre-commit contract started the stream at
// the non-error finish chunk: HTTP 200, one dispatch, the reported usage
// billed once. The nonstream control below keeps the same answer at 200.
func TestRecheck3OriginalFinishOnlyStream(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, reason := range []string{"length", "content_filter", "stop"} {
			for _, usage := range []string{"reported", "none"} {
				t.Run(surface+"/"+reason+"/"+usage, func(t *testing.T) {
					g := originalRecheck3Gateway(t, "")
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
					status, body, _ := sendManaged(t, g, surface, "acme/token", true, false, nil)
					spent, held := settledAccount(t, g)
					calls := g.managed.calls()
					t.Logf("OBS3 finish-only surface=%s reason=%s usage=%s status=%d calls=%d spent=%d held=%d", surface, reason, usage, status, calls, spent, held)
					if status != 200 || calls != 1 || held != 0 {
						t.Errorf("completed empty generation became a failure: status=%d calls=%d held=%d body=%.300s", status, calls, held, body)
					}
					if usage == "reported" && spent != 48 {
						t.Errorf("completed empty generation billed %d micros, want its reported usage once (48)", spent)
					}
				})
			}
		}
	}
}

func TestRecheck3OriginalEmptyNonstreamParity(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, reason := range []string{"length", "content_filter"} {
			t.Run(surface+"/"+reason, func(t *testing.T) {
				g := originalRecheck3Gateway(t, "")
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":""},"finish_reason":%q}],"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11}}`, model, reason)
				})
				g.managed.responseWith.Store(&up)
				status, _, _ := sendManaged(t, g, surface, "acme/token", false, false, nil)
				spent, held := settledAccount(t, g)
				calls := g.managed.calls()
				t.Logf("OBS3 nonstream-parity surface=%s reason=%s status=%d calls=%d spent=%d held=%d", surface, reason, status, calls, spent, held)
				if status != 200 || calls != 1 || spent != 48 || held != 0 {
					t.Errorf("nonstream empty generation: status=%d calls=%d spent=%d held=%d", status, calls, spent, held)
				}
			})
		}
	}
}

// Pre-output failures that carry endpoint evidence. Each attempt is priced on
// its own admitted endpoint set: a known unique endpoint at its card (48 per
// attempt), an unknown or ambiguous one at the reserve card (97 per attempt).
func TestRecheck3OriginalFailedIdentity(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tc := range []struct {
			name, endpoints string
			write           func(w http.ResponseWriter, model string)
			per             int64
		}{
			{"split-evidence", originalRecheck3CheapExpensive, func(w http.ResponseWriter, model string) {
				writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
				fmt.Fprint(w, `data: {"provider":"Cheap Compute","error":{"code":502,"message":"fixture failure"}}`+"\n\n")
			}, 48},
			{"case-space", originalRecheck3CheapExpensive, func(w http.ResponseWriter, _ string) {
				fmt.Fprint(w, `data: {"provider":"  cheap compute ","error":{"code":502,"message":"fixture failure"},"usage":{"prompt_tokens":8,"completion_tokens":3}}`+"\n\n")
			}, 48},
			{"unknown-name", originalRecheck3CheapExpensive, func(w http.ResponseWriter, _ string) {
				fmt.Fprint(w, `data: {"provider":"Rogue Compute","error":{"code":502,"message":"fixture failure"},"usage":{"prompt_tokens":8,"completion_tokens":3}}`+"\n\n")
			}, 97},
			{"no-name", originalRecheck3CheapExpensive, func(w http.ResponseWriter, _ string) {
				fmt.Fprint(w, `data: {"error":{"code":502,"message":"fixture failure"},"usage":{"prompt_tokens":8,"completion_tokens":3}}`+"\n\n")
			}, 97},
			{"ambiguous-name", originalRecheck3Ambiguous, func(w http.ResponseWriter, _ string) {
				fmt.Fprint(w, `data: {"provider":"Cheap Compute","error":{"code":502,"message":"fixture failure"},"usage":{"prompt_tokens":8,"completion_tokens":3}}`+"\n\n")
			}, 97},
		} {
			t.Run(surface+"/"+tc.name, func(t *testing.T) {
				g := originalRecheck3Gateway(t, tc.endpoints)
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "text/event-stream")
					tc.write(w, model)
				})
				g.managed.streamWith.Store(&up)
				var status int
				if surface == "/v1/messages" {
					// Keep this two-attempt accounting probe on the existing
					// operator-scoped retry fixture. Customer org retry policy
					// deliberately does not control credit-funded providers.
					status = sendManagedConfiguredRetry(t, g, "acme/token")
				} else {
					status, _, _ = sendManaged(t, g, surface, "acme/token", true, false, nil)
				}
				spent, held := settledAccount(t, g)
				calls := g.managed.calls()
				want := tc.per * int64(calls)
				t.Logf("OBS3 failed-identity surface=%s case=%s status=%d calls=%d spent=%d want=%d held=%d", surface, tc.name, status, calls, spent, want, held)
				wantCalls := 1
				if surface == "/v1/messages" {
					wantCalls = 2
				}
				if status != 502 || calls != wantCalls || spent != want || held != 0 {
					t.Errorf("status=%d calls=%d/%d spent=%d want=%d held=%d", status, calls, wantCalls, spent, want, held)
				}
			})
		}
	}
}

// Messages with retry enabled retries a pre-output 502 once. Each attempt's identity is its own:
// a named first attempt must not lend its card to an unnamed second, and an
// unnamed first must not borrow the second's.
func TestRecheck3OriginalIdentityNotInherited(t *testing.T) {
	for _, firstNamed := range []bool{true, false} {
		t.Run(fmt.Sprintf("first-named=%t", firstNamed), func(t *testing.T) {
			g := originalRecheck3Gateway(t, originalRecheck3CheapExpensive)
			var n atomic.Int32
			up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, _ string) {
				w.Header().Set("Content-Type", "text/event-stream")
				named := (n.Add(1) == 1) == firstNamed
				provider := ""
				if named {
					provider = `"provider":"Cheap Compute",`
				}
				fmt.Fprint(w, `data: {`+provider+`"error":{"code":502,"message":"fixture failure"},"usage":{"prompt_tokens":8,"completion_tokens":3}}`+"\n\n")
			})
			g.managed.streamWith.Store(&up)
			status := sendManagedConfiguredRetry(t, g, "acme/token")
			spent, held := settledAccount(t, g)
			calls := g.managed.calls()
			t.Logf("OBS3 no-inherit first-named=%t status=%d calls=%d spent=%d held=%d", firstNamed, status, calls, spent, held)
			if status != 502 || calls != 2 || spent != 48+97 || held != 0 {
				t.Errorf("status=%d calls=%d spent=%d want=145 held=%d", status, calls, spent, held)
			}
		})
	}
}

// A stream that started, then failed with an error envelope naming its
// endpoint and reporting usage. Run with -count to expose ordering races.
func TestRecheck3OriginalMidStreamEnvelope(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			g := originalRecheck3Gateway(t, originalRecheck3CheapExpensive)
			up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				w.Header().Set("Content-Type", "text/event-stream")
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":"hello there"}}]`)
				fmt.Fprint(w, `data: {"provider":"Cheap Compute","error":{"code":502,"message":"fixture failure"},"usage":{"prompt_tokens":8,"completion_tokens":3}}`+"\n\n")
			})
			g.managed.streamWith.Store(&up)
			status, _, _ := sendManaged(t, g, surface, "acme/token", true, false, nil)
			spent, held := settledAccount(t, g)
			t.Logf("OBS3 mid-stream surface=%s status=%d calls=%d spent=%d held=%d", surface, status, g.managed.calls(), spent, held)
			if status != 200 || spent != 48 || held != 0 {
				t.Errorf("mid-stream named failure: status=%d spent=%d want=48 held=%d", status, spent, held)
			}
		})
	}
}

// Tool streams: identity must survive, metadata alone must not bill.
func TestRecheck3OriginalToolStreams(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tc := range []struct {
			name   string
			deltas []string
			want   []string
		}{
			{"no-arguments", []string{
				`{"tool_calls":[{"index":0,"id":"call_noargs","type":"function","function":{"name":"ping"}}]}`,
			}, []string{"call_noargs", "ping"}},
			{"id-then-text-then-function", []string{
				`{"role":"assistant","tool_calls":[{"index":0,"id":"call_after_text","type":"function"}]}`,
				`{"content":"hello there"}`,
				`{"tool_calls":[{"index":0,"function":{"name":"lookup","arguments":"{\"q\":1}"}}]}`,
			}, []string{"call_after_text", "lookup", "hello there"}},
			{"two-id-prefixes", []string{
				`{"tool_calls":[{"index":0,"id":"call_one","type":"function"}]}`,
				`{"tool_calls":[{"index":1,"id":"call_two","type":"function"}]}`,
				`{"tool_calls":[{"index":0,"function":{"name":"first","arguments":"{}"}}]}`,
				`{"tool_calls":[{"index":1,"function":{"name":"second","arguments":"{}"}}]}`,
			}, []string{"call_one", "call_two", "first", "second"}},
		} {
			t.Run(surface+"/"+tc.name, func(t *testing.T) {
				g := originalRecheck3Gateway(t, "")
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "text/event-stream")
					for _, d := range tc.deltas {
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":`+d+`}]`)
					}
					writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
					fmt.Fprint(w, "data: [DONE]\n\n")
				})
				g.managed.streamWith.Store(&up)
				status, body, _ := sendManaged(t, g, surface, "acme/token", true, false, nil)
				spent, held := settledAccount(t, g)
				missing := []string{}
				for _, w := range tc.want {
					if !strings.Contains(body, w) {
						missing = append(missing, w)
					}
				}
				t.Logf("OBS3 tool-stream surface=%s case=%s status=%d calls=%d spent=%d held=%d missing=%v", surface, tc.name, status, g.managed.calls(), spent, held, missing)
				if status != 200 || spent != 48 || held != 0 || len(missing) > 0 || strings.Contains(body, `"type":"error"`) {
					t.Errorf("status=%d spent=%d held=%d missing=%v body=%.600s", status, spent, held, missing, body)
				}
			})
		}
	}
}

// Usage evidence spread over several stream events, and nonstream reports
// with invalid fields next to valid counters.
func TestRecheck3OriginalUsageEvidence(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tc := range []struct {
			name   string
			stream bool
			usages []string
			want   int64
		}{
			{"stream-prompt-then-completion", true, []string{`{"prompt_tokens":8}`, `{"completion_tokens":3}`}, 48},
			{"stream-full-then-empty", true, []string{`{"prompt_tokens":8,"completion_tokens":3}`, `{}`}, 48},
			{"stream-cost-then-empty", true, []string{`{"cost":0.001}`, `{}`}, 1050},
			{"stream-empty-on-text-chunk", true, []string{`{}`}, 48},
			{"nonstream-negative-prompt", false, []string{`{"prompt_tokens":-1,"completion_tokens":3}`}, 48},
			{"nonstream-invalid-cost-valid-counters", false, []string{`{"prompt_tokens":8,"completion_tokens":3,"cost":-1}`}, 48},
			{"nonstream-null-cost-valid-counters", false, []string{`{"prompt_tokens":8,"completion_tokens":3,"cost":null}`}, 48},
		} {
			t.Run(surface+"/"+tc.name, func(t *testing.T) {
				g := originalRecheck3Gateway(t, "")
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					if !tc.stream {
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprintf(w, `{"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hello there"},"finish_reason":"stop"}],"usage":%s}`, model, tc.usages[0])
						return
					}
					w.Header().Set("Content-Type", "text/event-stream")
					if tc.name == "stream-empty-on-text-chunk" {
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"hello there"}}],"usage":{}`)
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`)
					} else {
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"hello there"}}]`)
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":`+tc.usages[0])
						for _, u := range tc.usages[1:] {
							writeManagedChunk(w, model, `"choices":[],"usage":`+u)
						}
					}
					fmt.Fprint(w, "data: [DONE]\n\n")
				})
				g.managed.streamWith.Store(&up)
				g.managed.responseWith.Store(&up)
				status, _, _ := sendManaged(t, g, surface, "acme/token", tc.stream, false, nil)
				spent, held := settledAccount(t, g)
				t.Logf("OBS3 usage-evidence surface=%s case=%s status=%d calls=%d spent=%d want=%d held=%d", surface, tc.name, status, g.managed.calls(), spent, tc.want, held)
				if status != 200 || spent != tc.want || held != 0 {
					t.Errorf("status=%d spent=%d want=%d held=%d", status, spent, tc.want, held)
				}
			})
		}
	}
}
