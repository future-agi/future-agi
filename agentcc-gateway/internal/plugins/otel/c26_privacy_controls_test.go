package otel

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

func TestC26PrivacyToolArgumentRetentionModes(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull, privacy.ModeNone, "absent"} {
			for _, contentMode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
				t.Run(source+"/"+mode+"/"+string(contentMode), func(t *testing.T) {
					p, _ := bodyPlugin(t, true)
					t.Cleanup(p.Close)
					rc := newTestRC()
					rc.Privacy.ContentMode = contentMode
					msg := models.Message{Role: "assistant", ToolCalls: []models.ToolCall{
						{ID: "call", Type: "function", Function: models.FunctionCall{Name: "lookup", Arguments: `{"value":"` + c26EscapedMarker() + `"}`}},
						{ID: "empty", Type: "function", Function: models.FunctionCall{Name: "empty", Arguments: ""}},
					}}
					rc.Request.Messages = []models.Message{msg}
					rc.Response.Choices = []models.Choice{{Message: msg}}
					c26Policy(p, rc, source, mode)
					before := c26Snapshot(t, rc)
					wantIn, wantOut := bodyJSON(rc)
					attrs := c26Export(t, p, rc)
					for key, want := range map[string]string{"input.value": wantIn, "output.value": wantOut} {
						value, ok := attrs[key].(string)
						if !ok {
							t.Fatalf("missing %s", key)
						}
						switch mode {
						case privacy.ModeFull:
							if value != "[REDACTED]" {
								t.Errorf("full masking changed for %s: %s", key, value)
							}
						case privacy.ModePatterns:
							var message models.Message
							if key == "input.value" {
								var request models.ChatCompletionRequest
								if err := json.Unmarshal([]byte(value), &request); err != nil {
									t.Fatal(err)
								}
								if len(request.Messages) != 1 {
									t.Fatal("lost request message")
								}
								message = request.Messages[0]
							} else {
								var response models.ChatCompletionResponse
								if err := json.Unmarshal([]byte(value), &response); err != nil {
									t.Fatal(err)
								}
								if len(response.Choices) != 1 {
									t.Fatal("lost response message")
								}
								message = response.Choices[0].Message
							}
							if len(message.ToolCalls) != 2 {
								t.Fatal("lost tool calls")
							}
							c26Masked(t, message.ToolCalls[0].Function.Arguments)
							if !json.Valid([]byte(message.ToolCalls[0].Function.Arguments)) {
								t.Error("invalid masked arguments")
							}
							if message.ToolCalls[1].Function.Arguments != "" {
								t.Error("invented empty arguments")
							}
						default:
							if value != want {
								t.Errorf("disabled export changed %s bytes", key)
							}
						}
					}
					c26Metadata(t, attrs, rc)
					if !bytes.Equal(before, c26Snapshot(t, rc)) {
						t.Error("live context or retention policy changed")
					}
				})
			}
		}
	}
}

func TestC26PrivacySearchInvalidJSON(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModeFull, privacy.ModePatterns} {
			for _, fixture := range []struct{ name, raw string }{
				{"partial", `["` + c26EscapedMarker()},
				{"trailing", `"ordinary" "` + c26EscapedMarker() + `"`},
			} {
				t.Run(source+"/"+mode+"/"+fixture.name, func(t *testing.T) {
					p, _ := bodyPlugin(t, false)
					t.Cleanup(p.Close)
					rc := newTestRC()
					rc.EndpointType = "search"
					rc.Privacy.ContentMode = models.ContentModeFull
					rc.SearchRequest = &models.SearchRequest{QueryRaw: json.RawMessage(fixture.raw), MaxResults: 2}
					c26Policy(p, rc, source, mode)
					attrs := c26Export(t, p, rc)
					if attrs["gen_ai.retrieval.query"] != "[REDACTED]" {
						t.Errorf("uninterpretable query retained: %v", attrs["gen_ai.retrieval.query"])
					}
					if string(rc.SearchRequest.QueryRaw) != fixture.raw {
						t.Error("live query changed")
					}
					if attrs["gen_ai.retrieval.top_k"] != float64(2) {
						t.Error("query metadata changed")
					}
				})
			}
		}
	}
}

func TestC26PrivacyDecodedChatContent(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, contentMode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
			t.Run(source+"/"+string(contentMode), func(t *testing.T) {
				p, _ := bodyPlugin(t, true)
				t.Cleanup(p.Close)
				rc := newTestRC()
				rc.Privacy.ContentMode = contentMode
				msg := models.Message{Role: "user", Content: c26JSON(t, c26QuotedMarker())}
				rc.Request.Messages = []models.Message{msg}
				rc.Response.Choices = []models.Choice{{Message: msg}}
				c26Policy(p, rc, source, privacy.ModePatterns)
				before := c26Snapshot(t, rc)
				attrs := c26Export(t, p, rc)
				for _, key := range []string{"input.value", "output.value"} {
					value, ok := attrs[key].(string)
					if !ok {
						t.Fatalf("missing %s", key)
					}
					c26Masked(t, value)
				}
				for _, key := range []string{"llm.input_messages.0.message.content", "llm.output_messages.0.message.content"} {
					value, ok := attrs[key].(string)
					if !ok {
						t.Fatalf("missing %s", key)
					}
					c26Masked(t, value)
				}
				if !bytes.Equal(before, c26Snapshot(t, rc)) {
					t.Error("live content changed")
				}
			})
		}
	}
}

func TestC26PrivacyAbsentBodyControls(t *testing.T) {
	for _, mode := range []string{privacy.ModeFull, privacy.ModePatterns, privacy.ModeNone, "absent"} {
		t.Run(mode, func(t *testing.T) {
			p, _ := bodyPlugin(t, true)
			t.Cleanup(p.Close)
			rc := newTestRC()
			rc.Privacy.ContentMode = models.ContentModeFull
			rc.Request = nil
			rc.IsStream = true // usage-only response, before assembly of any choices
			c26Policy(p, rc, "global", mode)
			attrs := c26Export(t, p, rc)
			for _, key := range []string{"input.value", "output.value"} {
				if _, exists := attrs[key]; exists {
					t.Errorf("invented %s", key)
				}
			}
			c26Metadata(t, attrs, rc)
		})
	}
}
