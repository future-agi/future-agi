package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestManagedAccountingPrefixBounds(t *testing.T) {
	for _, tc := range []struct {
		name                    string
		count, bytes            int
		output, failure, wantOK bool
	}{
		{"tiny", 1, 1, true, false, true},
		{"many-at-limit", maxStreamPrefixChunks, 1, true, false, true},
		{"too-many", maxStreamPrefixChunks + 1, 1, true, false, false},
		{"too-large", 1, maxStreamPrefixBytes, true, false, false},
		{"eof", 2, 1, false, false, false},
		{"error", 2, 1, false, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			chunks := make(chan models.StreamChunk, tc.count+1)
			errs := make(chan error, 1)
			for i := 0; i < tc.count; i++ {
				chunks <- models.StreamChunk{Choices: []models.StreamChoice{{Index: i % 2, Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{Index: i % 3, ID: strings.Repeat("x", tc.bytes), Type: "function"}}}}}}
			}
			if tc.output {
				text := "x"
				chunks <- models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
			}
			if tc.failure {
				errs <- fmt.Errorf("fixture failure")
			}
			close(errs)
			close(chunks)
			chunk, prefixes, err := waitForManagedStreamStart(context.Background(), chunks, errs, true)
			if (err == nil) != tc.wantOK {
				t.Fatalf("error=%v success=%t", err, tc.wantOK)
			}
			if tc.wantOK {
				if len(prefixes) != tc.count {
					t.Fatalf("prefixes=%d want=%d", len(prefixes), tc.count)
				}
				for i, p := range prefixes {
					if p.Choices[0].Index != i%2 || p.Choices[0].Delta.ToolCalls[0].Index != i%3 {
						t.Fatal("prefix order changed")
					}
				}
			} else if len(prefixes) != 0 || chunk.Usage != nil {
				t.Fatal("failed prefix became output or invented usage")
			}
		})
	}
}

func TestManagedAccountingInterleavedToolPrefixes(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			installBillabilityModels(billabilityModels(t))(t, g)
			g.ledger.SetMicros("org-m", 1_000_000)
			up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				w.Header().Set("Content-Type", "text/event-stream")
				for _, delta := range []string{
					`{"tool_calls":[{"index":0,"type":"function"},{"index":1,"type":"function"}]}`,
					`{"tool_calls":[{"index":1,"id":"call_second"}]}`,
					`{"tool_calls":[{"index":0,"id":"call_first"}]}`,
					`{"tool_calls":[{"index":0,"function":{"name":"lookup_a"}}]}`,
					`{"tool_calls":[{"index":1,"function":{"arguments":"{\"b\":"}}]}`,
					`{"tool_calls":[{"index":1,"function":{"name":"lookup_b"}}]}`,
					`{"tool_calls":[{"index":0,"function":{"arguments":"{\"a\":"}}]}`,
					`{"tool_calls":[{"index":1,"function":{"arguments":"2}"}},{"index":0,"function":{"arguments":"1}"}}]}`,
				} {
					writeManagedChunk(w, model, `"choices":[{"index":0,"delta":`+delta+`}]`)
				}
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
				fmt.Fprint(w, "data: [DONE]\n\n")
			})
			g.managed.streamWith.Store(&up)
			status, body, _ := sendManaged(t, g, surface, "acme/token", true, false, nil)
			spent, held := settledAccount(t, g)
			if status != 200 || spent != 48 || held != 0 {
				t.Fatalf("status=%d spent=%d held=%d", status, spent, held)
			}
			type tool struct{ id, name, args string }
			tools := map[int]*tool{}
			for _, line := range strings.Split(body, "\n") {
				if !strings.HasPrefix(line, "data:") {
					continue
				}
				raw := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
				if raw == "[DONE]" {
					continue
				}
				if surface == "/v1/chat/completions" {
					var c models.StreamChunk
					if json.Unmarshal([]byte(raw), &c) != nil {
						continue
					}
					for _, choice := range c.Choices {
						for _, d := range choice.Delta.ToolCalls {
							if tools[d.Index] == nil {
								tools[d.Index] = &tool{}
							}
							v := tools[d.Index]
							v.id += d.ID
							if d.Function != nil {
								v.name += d.Function.Name
								v.args += d.Function.Arguments
							}
						}
					}
				} else {
					var e struct {
						Type  string                          `json:"type"`
						Index int                             `json:"index"`
						Block struct{ Type, ID, Name string } `json:"content_block"`
						Delta struct {
							Partial string `json:"partial_json"`
						} `json:"delta"`
					}
					if json.Unmarshal([]byte(raw), &e) != nil {
						continue
					}
					if e.Type == "content_block_start" && e.Block.Type == "tool_use" {
						tools[e.Index] = &tool{id: e.Block.ID, name: e.Block.Name}
					}
					if e.Type == "content_block_delta" && e.Delta.Partial != "" {
						if tools[e.Index] == nil {
							t.Fatal("arguments before identity")
						}
						tools[e.Index].args += e.Delta.Partial
					}
				}
			}
			byID := map[string]*tool{}
			for _, v := range tools {
				byID[v.id] = v
			}
			for _, want := range []tool{{"call_first", "lookup_a", `{"a":1}`}, {"call_second", "lookup_b", `{"b":2}`}} {
				got := byID[want.id]
				if got == nil || *got != want {
					t.Errorf("tool %s=%+v want=%+v", want.id, got, want)
				}
			}
		})
	}
}
