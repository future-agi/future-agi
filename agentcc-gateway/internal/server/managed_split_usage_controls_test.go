package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// All fixtures are local, and all timings are client deadlines, not ordering
// assumptions. preparedStream queues its entire transcript before returning.
func TestSolFinishOnlyPlacements(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, reason := range []string{"stop", "length", "content_filter", "tool_calls"} {
			for _, placement := range []string{"first", "same-finish", "trailing", "explicit-zero", "no-usage"} {
				t.Run(surface+"/"+reason+"/"+placement, func(t *testing.T) {
					g := recheck3Gateway(t, "")
					up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
						w.Header().Set("Content-Type", "text/event-stream")
						if placement != "first" {
							writeManagedChunk(w, model, `"system_fingerprint":"fp_sol","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]`)
						}
						usage := ""
						if placement == "same-finish" || placement == "first" {
							usage = `,"usage":{"prompt_tokens":8,"completion_tokens":3}`
						}
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":""},"finish_reason":"`+reason+`"}]`+usage)
						if placement == "trailing" {
							writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
						} else if placement == "explicit-zero" {
							writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":0,"completion_tokens":0}`)
						}
						fmt.Fprint(w, "data: [DONE]\n\n")
					})
					g.managed.streamWith.Store(&up)
					status, body, complete := sendManagedWithin(t, g, surface, "acme/token", 5*time.Second)
					spent, held := settledAccount(t, g)
					want := int64(48)
					if placement == "explicit-zero" {
						want = 0
					}
					if status != 200 || !complete || held != 0 || g.managed.calls() != 1 || (placement != "no-usage" && spent != want) || (placement == "no-usage" && spent <= 0) {
						t.Fatalf("status=%d complete=%t dispatches=%d spent=%d want=%d held=%d body=%.400s", status, complete, g.managed.calls(), spent, want, held, body)
					}
				})
			}
		}
	}
}

func TestSolMetadataFailureEvidence(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, ending := range []string{"eof", "error"} {
			for _, reported := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/reported=%t", surface, ending, reported), func(t *testing.T) {
					g := recheck3Gateway(t, recheck3CheapExpensive)
					up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
						w.Header().Set("Content-Type", "text/event-stream")
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]`)
						writeManagedChunk(w, model, `"provider":"Cheap Compute","choices":[]`)
						if reported {
							writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
						}
						if ending == "error" {
							fmt.Fprint(w, "data: {\"error\":{\"code\":502,\"message\":\"sol fixture failure\"}}\n\n")
						}
					})
					g.managed.streamWith.Store(&up)
					status, _, complete := sendManagedWithin(t, g, surface, "acme/token", 5*time.Second)
					spent, held := settledAccount(t, g)
					want := int64(0)
					if reported {
						want = 48 * int64(g.managed.calls())
					}
					if !complete || status != 502 || spent != want || held != 0 {
						t.Fatalf("complete=%t status=%d spent=%d want=%d held=%d", complete, status, spent, want, held)
					}
				})
			}
		}
	}
}

func TestSolQueuedSplitEvidence(t *testing.T) {
	for _, identityLast := range []bool{false, true} {
		t.Run(fmt.Sprintf("identity-last=%t", identityLast), func(t *testing.T) {
			g := recheck3Gateway(t, recheck3CheapExpensive)
			p := &preparedStream{err: models.ErrUpstreamProvider(502, "sol queued failure"), script: func(model string) []models.StreamChunk {
				text := "hello there"
				out := []models.StreamChunk{{Model: model, Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}}
				identity := models.StreamChunk{UpstreamProvider: "Cheap Compute"}
				if !identityLast {
					out = append(out, identity)
				}
				for _, raw := range []string{`{"usage":{"prompt_tokens":8}}`, `{"usage":{"completion_tokens":3}}`, `{"usage":{}}`} {
					var chunk models.StreamChunk
					if err := json.Unmarshal([]byte(raw), &chunk); err != nil {
						t.Fatal(err)
					}
					out = append(out, chunk)
				}
				if identityLast {
					out = append(out, identity)
				}
				return out
			}}
			prepareManagedStream(t, g, p)
			status, body, complete := sendManagedWithin(t, g, "/v1/chat/completions", "acme/token", 5*time.Second)
			spent, held := settledAccount(t, g)
			if !complete || status != 200 || spent != 48 || held != 0 || p.calls.Load() != 1 {
				t.Fatalf("complete=%t status=%d spent=%d held=%d calls=%d body=%.400s", complete, status, spent, held, p.calls.Load(), body)
			}
			w4AssertTerminalOrder(t, body, "sol queued failure")
		})
	}
}

func TestSolMetadataPrefixExactAndIndependentBounds(t *testing.T) {
	empty, text := "", "answer"
	role := models.StreamChunk{ID: "sol_role", Model: "acme/token", SystemFingerprint: "fp_sol", Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant", Content: &empty}}}}
	tool := models.StreamChunk{ID: "sol_tool", Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{ID: "sol_call", Type: "function"}}}}}}
	for _, n := range []int{0, 1, maxStreamPrefixChunks, maxStreamPrefixChunks + 1} {
		t.Run(fmt.Sprint(n), func(t *testing.T) {
			chunks := make(chan models.StreamChunk, n+3)
			for i := 0; i < n; i++ {
				c := role
				c.ID = fmt.Sprint(i)
				chunks <- c
			}
			chunks <- tool
			chunks <- models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
			close(chunks)
			errs := make(chan error)
			close(errs)
			first, prefixes, err := waitForManagedStreamStart(context.Background(), chunks, errs, true)
			want := n
			if want > maxStreamPrefixChunks {
				want = maxStreamPrefixChunks
			}
			if err != nil || first == nil || len(prefixes) != want+1 {
				t.Fatalf("err=%v prefixes=%d want=%d", err, len(prefixes), want+1)
			}
			for i := 0; i < want; i++ {
				c := role
				c.ID = fmt.Sprint(i)
				a, _ := json.Marshal(c)
				b, _ := json.Marshal(prefixes[i])
				if string(a) != string(b) {
					t.Fatalf("metadata %d changed", i)
				}
			}
			if prefixes[want].Choices[0].Delta.ToolCalls[0].ID != "sol_call" {
				t.Fatal("tool identity lost")
			}
		})
	}
}

func TestSolTranslatorFailureLongTail(t *testing.T) {
	g := recheck3Gateway(t, recheck3CheapExpensive)
	p := &preparedStream{script: func(model string) []models.StreamChunk {
		text := "hello there"
		finish := "tool_calls"
		out := []models.StreamChunk{
			{Model: model, Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{ID: "nameless", Type: "function"}}}}}},
			{Model: model, Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}},
			{Model: model, Choices: []models.StreamChoice{{FinishReason: &finish}}},
		}
		for i := 0; i < 300; i++ {
			out = append(out, models.StreamChunk{Model: model})
		}
		var evidence models.StreamChunk
		if err := json.Unmarshal([]byte(`{"provider":"Cheap Compute","usage":{"prompt_tokens":8,"completion_tokens":3}}`), &evidence); err != nil {
			t.Fatal(err)
		}
		return append(out, evidence)
	}}
	prepareManagedStream(t, g, p)
	status, body, complete := sendManagedWithin(t, g, "/v1/messages", "acme/token", 5*time.Second)
	spent, held := settledAccount(t, g)
	if !complete || status != 200 || spent != 48 || held != 0 || !strings.Contains(body, "incomplete tool identity") || strings.Contains(body, "event: message_stop") {
		t.Fatalf("complete=%t status=%d spent=%d held=%d body=%.500s", complete, status, spent, held, body)
	}
}

// Sparse identity on pre-output metadata is legal canonical data. Replaying
// it must not replace the completion's concrete model/id in message_start.
func TestSolSparseMetadataMessagesIdentity(t *testing.T) {
	for _, prefix := range []string{"provider", "usage", "role"} {
		t.Run(prefix, func(t *testing.T) {
			g := recheck3Gateway(t, "")
			p := &preparedStream{script: func(model string) []models.StreamChunk {
				meta := models.StreamChunk{}
				switch prefix {
				case "provider":
					meta.UpstreamProvider = "Cheap Compute"
				case "usage":
					prompt := 8
					meta.Usage = models.NewUsage(&prompt, nil)
				case "role":
					meta.Choices = []models.StreamChoice{{Delta: models.Delta{Role: "assistant"}}}
				}
				text, finish := "hello there", "stop"
				return []models.StreamChunk{meta, {ID: "sol_concrete", Model: model, Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}, {ID: "sol_concrete", Model: model, Choices: []models.StreamChoice{{FinishReason: &finish}}, Usage: models.MeasuredUsage(8, 3)}}
			}}
			prepareManagedStream(t, g, p)
			status, body, complete := sendManagedWithin(t, g, "/v1/messages", "acme/token", 5*time.Second)
			if !complete || status != 200 {
				t.Fatalf("status=%d complete=%t", status, complete)
			}
			for _, line := range strings.Split(body, "\n") {
				if strings.HasPrefix(line, "data: ") && strings.Contains(line, `"type":"message_start"`) {
					var event struct {
						Message struct {
							ID    string `json:"id"`
							Model string `json:"model"`
						} `json:"message"`
					}
					if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &event); err != nil {
						t.Fatal(err)
					}
					if event.Message.ID != "sol_concrete" || event.Message.Model != "acme/token" {
						t.Errorf("message_start identity=%q model=%q, want sol_concrete/acme/token", event.Message.ID, event.Message.Model)
					}
					return
				}
			}
			t.Fatal("no message_start")
		})
	}
}
