package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

func TestManagedMessagesDropUnsupportedReasoning(t *testing.T) {
	for _, thinking := range []bool{false, true} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("thinking=%v/stream=%v", thinking, stream), func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				g.ledger.SetMicros("org-m", auth.USDToMicros(1))
				upstream := sdkCanonicalStream("thinking")
				g.managed.streamWith.Store(&upstream)
				var response upstreamStream = func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"id":"msg_test","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"Hello world","reasoning_content":"Consider"},"finish_reason":"stop"}],"usage":{"prompt_tokens":9,"completion_tokens":4,"total_tokens":13}}`, model)
				}
				g.managed.responseWith.Store(&response)
				extra, wantDrops := "", ""
				if thinking {
					extra, wantDrops = `,"thinking":{"type":"adaptive"}`, "thinking_unsupported"
				}
				w := messagesRoutingCall(g.srv, managedOrgKey, "anthropic/claude-sonnet-5.5", stream, extra)
				if w.Code != http.StatusOK {
					t.Fatalf("status %d: %s", w.Code, w.Body)
				}
				if drops := w.Header().Get("x-agentcc-translation-drops"); drops != wantDrops {
					t.Fatalf("translation drops = %q, want %q", drops, wantDrops)
				}
				body := w.Body.String()
				for _, forbidden := range []string{"Consider", "thinking", "reasoning_content", "signature"} {
					if strings.Contains(body, forbidden) {
						t.Errorf("unsupported reasoning in wire response: %s", body)
					}
				}
				var usage map[string]any
				text := ""
				if stream {
					for _, line := range strings.Split(body, "\n") {
						if !strings.HasPrefix(line, "data: ") {
							continue
						}
						var event struct {
							Type  string         `json:"type"`
							Usage map[string]any `json:"usage"`
							Delta struct {
								Text string `json:"text"`
							} `json:"delta"`
						}
						if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &event); err != nil {
							t.Fatal(err)
						}
						text += event.Delta.Text
						if event.Type == "message_delta" {
							usage = event.Usage
						}
					}
				} else {
					var message struct {
						Content []struct {
							Text string `json:"text"`
						} `json:"content"`
						Usage map[string]any `json:"usage"`
					}
					if err := json.Unmarshal(w.Body.Bytes(), &message); err != nil {
						t.Fatal(err)
					}
					for _, block := range message.Content {
						text += block.Text
					}
					usage = message.Usage
				}
				if text != "Hello world" || !reflect.DeepEqual(usage, map[string]any{"input_tokens": float64(9), "output_tokens": float64(4)}) {
					t.Fatalf("text=%q usage=%v; want Hello world and reported 9/4", text, usage)
				}
			})
		}
	}
}
