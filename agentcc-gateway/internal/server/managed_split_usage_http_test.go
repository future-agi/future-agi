package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

// Reproduce through the real OpenAI SSE adapter, rather than the prepared
// Provider implementation used to pin channel ordering in other probes.
func TestSolHTTPFinishOnlySplitUsage(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tail := range []int{0, 300} {
			for _, reason := range []string{"stop", "length"} {
				t.Run(fmt.Sprintf("%s/tail=%d/%s", surface, tail, reason), func(t *testing.T) {
					g := recheck3Gateway(t, "")
					up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
						w.Header().Set("Content-Type", "text/event-stream")
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]`)
						writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":8}`)
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"`+reason+`"}]`)
						for i := 0; i < tail; i++ {
							writeManagedChunk(w, model, `"choices":[]`)
						}
						writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11}`)
						fmt.Fprint(w, "data: [DONE]\n\n")
					})
					g.managed.streamWith.Store(&up)
					status, body, complete := sendManagedWithin(t, g, surface, "acme/token", 5*time.Second)
					spent, held := settledAccount(t, g)
					t.Logf("sol HTTP split status=%d complete=%t spent=%d held=%d calls=%d", status, complete, spent, held, g.managed.calls())
					if !complete || status != 200 || g.managed.calls() != 1 || spent != 48 || held != 0 {
						t.Errorf("real adapter lost trailing usage: status=%d complete=%t calls=%d spent=%d want=48 held=%d body=%.300s", status, complete, g.managed.calls(), spent, held, body)
					}
					assertSplitUsageWire(t, surface, body)
				})
			}
		}
	}
}

func TestSolHTTPSparseMetadataModel(t *testing.T) {
	g := recheck3Gateway(t, "")
	up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"provider\":\"Cheap Compute\",\"choices\":[]}\n\n")
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":"hello there"}}]`)
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
		fmt.Fprint(w, "data: [DONE]\n\n")
	})
	g.managed.streamWith.Store(&up)
	status, body, complete := sendManagedWithin(t, g, "/v1/messages", "acme/token", 5*time.Second)
	if !complete || status != 200 {
		t.Fatalf("complete=%t status=%d", complete, status)
	}
	for _, line := range strings.Split(body, "\n") {
		if strings.HasPrefix(line, "data: ") && strings.Contains(line, `"type":"message_start"`) {
			var event struct {
				Message struct {
					Model string `json:"model"`
				} `json:"message"`
			}
			if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &event); err != nil {
				t.Fatal(err)
			}
			if event.Message.Model != "acme/token" {
				t.Errorf("message_start model=%q want acme/token", event.Message.Model)
			}
			return
		}
	}
	t.Fatal("missing message_start")
}
