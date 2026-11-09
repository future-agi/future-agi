package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// MAX_TOKENS can exhaust the entire output budget on thinking: content is {},
// candidatesTokenCount is absent, but thoughtsTokenCount is measured output.
func TestUsagePresenceGeminiThoughts(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				for _, tc := range []struct {
					name, usage string
					completion  int
					estimated   bool
				}{
					{"consistent-total", `{"promptTokenCount":10,"totalTokenCount":510,"thoughtsTokenCount":500}`, 500, false},
					{"derived-candidates", `{"promptTokenCount":10,"totalTokenCount":515,"thoughtsTokenCount":500}`, 505, false},
					{"missing-total", `{"promptTokenCount":10,"thoughtsTokenCount":500}`, 500, true},
					{"null-total", `{"promptTokenCount":10,"totalTokenCount":null,"thoughtsTokenCount":500}`, 500, true},
					{"inconsistent-total", `{"promptTokenCount":10,"totalTokenCount":5,"thoughtsTokenCount":500}`, 500, true},
					{"control", `{"promptTokenCount":10,"candidatesTokenCount":5,"totalTokenCount":515,"thoughtsTokenCount":500}`, 505, false},
				} {
					for _, empty := range []bool{false, true} {
						t.Run(fmt.Sprintf("managed=%t/%s/stream=%t/%s/empty=%t", managed, upInt2SurfaceName(surface), stream, tc.name, empty), func(t *testing.T) {
							a := upInt2Adapters[3]
							body := upInt2Body(a, tc.usage)
							if empty {
								body = `{"candidates":[{"content":{},"finishReason":"MAX_TOKENS"}],"usageMetadata":` + tc.usage + `}`
							}
							r, cost := presenceGeminiBody(t, body, surface, stream, managed)
							if managed {
								upInt2Served(t, "gemini", r)
								minimum := int64((20+tc.completion*10)*105+50) / 100
								if r.spent < minimum || (!tc.estimated && r.spent != minimum) {
									t.Errorf("settled=%d, measured evidence requires %d; records=%v", r.spent, minimum, r.records)
								}
								if (r.records[0]["usage_estimated"] == "1") != tc.estimated {
									t.Errorf("estimate provenance: %v", r.records)
								}
							} else if r.mode != "byok" || r.spent != 0 || r.held != 0 || len(r.records) != 0 {
								t.Fatalf("BYOK spent managed credits: %+v", r)
							}
							if cost.usage == nil || cost.usage.CompletionTokens < tc.completion || cost.usage.CompletionDetails.ReasoningTokens != 500 {
								t.Errorf("lost measured thoughts: %+v", cost.usage)
							}
						})
					}
				}
			}
		}
	}
}

func presenceGeminiBody(t *testing.T, body, surface string, stream, managed bool) (upInt2Result, costRun) {
	t.Helper()
	return presenceGeminiFrames(t, surface, stream, managed, body)
}

// presenceGeminiFrames answers one Gemini call: the single body as JSON, or
// every frame as its own SSE event and then a clean close. No finishReason is
// added, so a stream without one ends exactly as the frames leave it.
func presenceGeminiFrames(t *testing.T, surface string, stream, managed bool, frames ...string) (upInt2Result, costRun) {
	t.Helper()
	if !stream && len(frames) != 1 {
		t.Fatalf("a non-streamed answer is one body, got %d", len(frames))
	}
	a := upInt2Adapters[3]
	var calls atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := ":generateContent"
		if stream {
			path = ":streamGenerateContent"
		}
		if r.Method != http.MethodPost || !strings.Contains(r.URL.Path, path) {
			w.WriteHeader(404)
			return
		}
		_, _ = io.Copy(io.Discard, r.Body)
		calls.Add(1)
		if stream {
			w.Header().Set("Content-Type", "text/event-stream")
			for _, frame := range frames {
				fmt.Fprintf(w, "data: %s\n\n", frame)
				w.(http.Flusher).Flush()
			}
		} else {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, frames[0])
		}
	}))
	defer up.Close()
	probe := &costProbe{}
	g := upInt2Gateway(t, a, up.URL, probe)
	if !managed {
		g.tenants.Set("org-m", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{upInt2Provider: {
			Enabled: true, APIKey: "synthetic-byok", APIFormat: a.format, Models: []string{a.model},
		}}})
	}
	status, response, header := sendManaged(t, g, surface, a.model, stream, false, nil)
	spent, held := settledAccount(t, g)
	result := upInt2Result{status: status, calls: int(calls.Load()), mode: header.Get("x-agentcc-billing-mode"), body: response,
		spent: spent, held: held, records: g.outbox("org-m")}
	if status != 200 || result.calls != 1 {
		t.Fatalf("dispatch control: %+v", result)
	}
	return result, probe.last(t, 1)
}

// presenceZeroOutput is what Gemini sends for a finished answer with no
// output: its JSON omits zero counters, so only promptTokenCount and an equal
// totalTokenCount are present. That total proves zero candidates.
const presenceZeroOutput = `{"promptTokenCount":10,"totalTokenCount":10}`

var presenceZeroOutputBodies = []struct{ name, body string }{
	{"safety-empty-content", `{"candidates":[{"content":{},"finishReason":"SAFETY"}],"usageMetadata":` + presenceZeroOutput + `}`},
	{"stop-empty-parts", `{"candidates":[{"content":{"role":"model","parts":[]},"finishReason":"STOP"}],"usageMetadata":` + presenceZeroOutput + `}`},
	{"prompt-blocked", `{"promptFeedback":{"blockReason":"SAFETY"},"usageMetadata":` + presenceZeroOutput + `}`},
}

// Exact controls on Gemini's real zero-output wire shapes: managed settles
// the measured 10-token prompt exactly as the OpenAI wire does for
// {"prompt_tokens":10,"completion_tokens":0} (21 micros, not estimated, never
// the admission hold), and BYOK prices and flags it as the OpenAI wire does.
func TestUsagePresenceGeminiZeroOutput(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				for _, tc := range presenceZeroOutputBodies {
					t.Run(fmt.Sprintf("managed=%t/%s/stream=%t/%s", managed, upInt2SurfaceName(surface), stream, tc.name), func(t *testing.T) {
						oracleSurface := surface
						if !managed {
							// BYOK Messages may be a byte passthrough; the price of the
							// same usage does not depend on the surface.
							oracleSurface = "/v1/chat/completions"
						}
						oracle, oracleCost := presenceRun(t, upInt2Adapters[0], oracleSurface, stream, managed, `{"prompt_tokens":10,"completion_tokens":0}`)
						got, gotCost := presenceGeminiBody(t, tc.body, surface, stream, managed)
						if u := gotCost.usage; u == nil || !u.HasPromptTokens() || !u.HasCompletionTokens() || u.PromptTokens != 10 || u.CompletionTokens != 0 {
							t.Errorf("zero output is not measured: %+v", u)
						}
						if !managed {
							if got.mode != "byok" || got.spent != 0 || got.held != 0 || len(got.records) != 0 {
								t.Fatalf("BYOK spent managed credits: %+v", got)
							}
							if gotCost.cost != oracleCost.cost || gotCost.usageEstimated != oracleCost.usageEstimated {
								t.Errorf("BYOK cost=%q estimated=%q; the OpenAI wire prices the same usage %q estimated=%q",
									gotCost.cost, gotCost.usageEstimated, oracleCost.cost, oracleCost.usageEstimated)
							}
							return
						}
						upInt2Served(t, "oracle", oracle)
						upInt2Compare(t, got, oracle)
						rec := got.records[0]
						if got.spent != 21 || rec["usage_estimated"] != "0" || rec["kind"] != "spend" {
							t.Errorf("settled %d (estimated=%s kind=%s hold=%s), want exactly 21 measured", got.spent, rec["usage_estimated"], rec["kind"], rec["hold_micros"])
						}
						presenceReconciles(t, got)
					})
				}
			}
		}
	}
}

// Gemini repeats usageMetadata on every frame and an interim frame's total
// can lag text it already carried, so only a frame that closes generation
// proves an omitted candidates count. A stream that ends after a zero-output
// interim frame keeps its streamed output unmeasured: estimated, and for a
// managed call never below the admission hold, never a measured zero.
func TestUsagePresenceGeminiInterimFrames(t *testing.T) {
	text := func(s, usage string) string {
		member := ""
		if usage != "" {
			member = `,"usageMetadata":` + usage
		}
		return `{"candidates":[{"content":{"role":"model","parts":[{"text":"` + s + `"}]}}]` + member + `}`
	}
	end := func(reason, usage string) string {
		return `{"candidates":[{"content":{},"finishReason":"` + reason + `"}],"usageMetadata":` + usage + `}`
	}
	for _, managed := range []bool{false, true} {
		for _, surface := range managedSurfaces {
			for _, tc := range []struct {
				name   string
				frames []string
				// floor is the measured output (thoughts) the settlement must cover;
				// exact > 0 is a complete control that must settle exactly.
				floor int
				exact int64
			}{
				{"cut-after-zero-interim", []string{text("hello", presenceZeroOutput), text(" there, and the rest of a longer answer", "")}, 0, 0},
				{"cut-after-thoughts-interim", []string{text("hello", `{"promptTokenCount":10,"thoughtsTokenCount":500,"totalTokenCount":510}`), text(" there", "")}, 500, 0},
				{"zero-interim-then-terminal", []string{text("hello", presenceZeroOutput), end("STOP", `{"promptTokenCount":10,"totalTokenCount":15}`)}, 5, 74},
			} {
				t.Run(fmt.Sprintf("managed=%t/%s/%s", managed, upInt2SurfaceName(surface), tc.name), func(t *testing.T) {
					r, cost := presenceGeminiFrames(t, surface, true, managed, tc.frames...)
					u := cost.usage
					if u == nil || !u.HasPromptTokens() || u.PromptTokens != 10 || u.CompletionTokens < tc.floor {
						t.Fatalf("settled usage lost measured evidence: %+v", u)
					}
					switch {
					case tc.exact > 0:
						if !u.HasCompletionTokens() || cost.usageEstimated == "true" || u.CompletionTokens != tc.floor {
							t.Errorf("the finishing frame's consistent total was not measured: estimated=%q %+v", cost.usageEstimated, u)
						}
					case managed:
						// The streamed output is estimated on top of the thoughts.
						if !u.HasCompletionTokens() || cost.usageEstimated != "true" || u.CompletionTokens <= tc.floor {
							t.Errorf("an interim total settled the streamed output as measured: estimated=%q %+v", cost.usageEstimated, u)
						}
					default:
						// BYOK keeps the unreported candidates unmeasured.
						if u.HasCompletionTokens() || u.CompletionTokens != tc.floor {
							t.Errorf("an interim total measured the streamed output: estimated=%q %+v", cost.usageEstimated, u)
						}
					}
					if !managed {
						if r.mode != "byok" || r.spent != 0 || r.held != 0 || len(r.records) != 0 {
							t.Fatalf("BYOK spent managed credits: %+v", r)
						}
						return
					}
					upInt2Served(t, "gemini", r)
					rec := r.records[0]
					hold, err := strconv.ParseInt(rec["hold_micros"], 10, 64)
					if err != nil || hold <= 0 {
						t.Fatalf("invalid admission: %v", rec)
					}
					measured := int64((20+tc.floor*10)*105+50) / 100
					switch {
					case tc.exact > 0:
						if r.spent != tc.exact || rec["usage_estimated"] != "0" {
							t.Errorf("complete control settled %d (estimated=%s), want exactly %d", r.spent, rec["usage_estimated"], tc.exact)
						}
					case r.spent < max(hold, measured+1) || rec["usage_estimated"] != "1" || rec["kind"] != "spend":
						t.Errorf("cut stream settled %d (estimated=%s kind=%s), want at least the hold %d and more than the measured %d",
							r.spent, rec["usage_estimated"], rec["kind"], hold, measured)
					}
					presenceReconciles(t, r)
				})
			}
		}
	}
}

// presenceReconciles checks that the request's one attempt debit, upstream
// and markup add up to what the org's account settled.
func presenceReconciles(t *testing.T, r upInt2Result) {
	t.Helper()
	rec := r.records[0]
	var attempts []billing.AttemptSummary
	if err := json.Unmarshal([]byte(rec["attempts"]), &attempts); err != nil || len(attempts) != 1 || attempts[0].BilledMicros != r.spent {
		t.Fatalf("attempt debit does not reconcile: %v (%v)", attempts, err)
	}
	upstream, err := strconv.ParseInt(rec["upstream_micros"], 10, 64)
	if err != nil {
		t.Fatal(err)
	}
	markup, err := strconv.ParseInt(rec["markup_micros"], 10, 64)
	if err != nil || upstream+markup != r.spent || attempts[0].UpstreamMicros != upstream {
		t.Fatalf("request and attempt money do not reconcile: %v", rec)
	}
	if r.mode != models.BillingModeManaged {
		t.Fatalf("billing mode %q", r.mode)
	}
}
