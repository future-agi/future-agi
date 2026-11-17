package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

// Stream metadata (here a tool call's id, with no name or arguments yet) is
// not a generation. When the stream ends after it, by EOF or an error,
// before any output or finish, the request fails and nothing is billed
// unless the upstream reported real usage.
func TestManagedAccountingMetadataOnlyNoBill(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, ending := range []string{"eof", "error", "empty-usage-error"} {
			t.Run(surface+"/"+ending, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				installBillabilityModels(billabilityModels(t))(t, g)
				g.ledger.SetMicros("org-m", 1_000_000)
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "text/event-stream")
					writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"metadata_only","type":"function"}]}}]`)
					switch ending {
					case "error":
						fmt.Fprint(w, "data: {\"error\":{\"code\":502,\"message\":\"fixture failure\"}}\n\n")
					case "empty-usage-error":
						fmt.Fprint(w, "data: {\"usage\":{},\"error\":{\"code\":502,\"message\":\"fixture failure\"}}\n\n")
					}
				})
				g.managed.streamWith.Store(&up)
				status, _, _ := sendManaged(t, g, surface, "acme/token", true, false, nil)
				spent, held := settledAccount(t, g)
				if status != 502 || spent != 0 || held != 0 {
					t.Fatalf("metadata became generation: status=%d spent=%d held=%d", status, spent, held)
				}
				for _, rec := range g.outbox("org-m") {
					if rec["usage_estimated"] == "1" {
						t.Fatal("metadata failure invented an estimate")
					}
				}
			})
		}
	}
}

// The same metadata followed by a non-error finish_reason is a completed
// generation. The rule above stops at the finish: the upstream ended the
// generation and charges for it, and the nonstream path answers the same
// generation with 200. This case used to sit in the table above expecting
// 502 and nothing billed. That expectation is why a 502 for every completed
// empty generation (finish length, content_filter, stop) went unnoticed; on
// Messages the 502 was retried, a second dispatch and a second charge for a
// generation the upstream had already completed.
//
// The two APIs differ only in how they hand over a tool call that never got
// a name: chat passes the upstream's chunks through (the id, then the
// finish), while the Messages translator refuses it at the finish with an
// error event in the started stream. Both bill what the upstream charged:
// its reported usage, or an estimate when it reported none.
func TestManagedAccountingMetadataThenFinishStarts(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, usage := range []string{"reported", "none"} {
			t.Run(surface+"/"+usage, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				installBillabilityModels(billabilityModels(t))(t, g)
				g.ledger.SetMicros("org-m", 1_000_000)
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "text/event-stream")
					writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"metadata_only","type":"function"}]}}]`)
					writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]`)
					if usage == "reported" {
						writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
					}
					fmt.Fprint(w, "data: [DONE]\n\n")
				})
				g.managed.streamWith.Store(&up)
				status, body, complete := sendManagedWithin(t, g, surface, "acme/token", 10*time.Second)
				spent, held := settledAccount(t, g)
				calls := g.managed.calls()
				recs := g.outbox("org-m")
				t.Logf("metadata-then-finish surface=%s usage=%s status=%d complete=%t calls=%d spent=%d held=%d", surface, usage, status, complete, calls, spent, held)
				if !complete || status != 200 || calls != 1 || held != 0 {
					t.Fatalf("completed generation did not start: complete=%t status=%d calls=%d held=%d body=%.400s", complete, status, calls, held, body)
				}
				switch {
				case usage == "reported" && spent != 48:
					t.Errorf("billed %d micros, want the reported usage once (48)", spent)
				case usage == "none" && (spent <= 0 || len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["usage_estimated"] != "1"):
					t.Errorf("want one estimated spend for a generation without usage: spent=%d records=%v", spent, recs)
				}
				if surface == "/v1/chat/completions" {
					if !strings.Contains(body, `"id":"metadata_only"`) || !strings.Contains(body, `"finish_reason":"tool_calls"`) || !strings.Contains(body, "[DONE]") {
						t.Errorf("chat stream lost the upstream's chunks: %.600s", body)
					}
				} else if !strings.Contains(body, "event: message_start") || !strings.Contains(body, "incomplete tool identity") {
					t.Errorf("want the Messages translator's refusal of the nameless tool in the started stream: %.600s", body)
				}
			})
		}
	}
}
