package logging

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// c2.5's chat redactor must copy tool calls as well as message content, and
// response-only records must not bypass masking because RequestBody is nil.
func TestW2B1ChatToolCopies(t *testing.T) {
	for _, mode := range []string{privacy.ModeFull, privacy.ModePatterns, privacy.ModeNone} {
		for _, shape := range []string{"request-and-response", "response-only"} {
			t.Run(mode+"/"+shape, func(t *testing.T) {
				rc := newRC()
				rc.Privacy.ContentMode = models.ContentModeFull
				rc.IsStream = true
				msg := models.Message{Role: "assistant", ToolCalls: []models.ToolCall{
					{ID: "call-1", Type: "function", Function: models.FunctionCall{Name: "lookup", Arguments: `{"query":"` + w2B1Marker + `"}`}},
					{ID: "call-2", Type: "function", Function: models.FunctionCall{Name: "count", Arguments: `{"count":2}`}},
				}}
				rc.Request = nil
				if shape == "request-and-response" {
					rc.Request = &models.ChatCompletionRequest{Model: rc.Model, Messages: []models.Message{msg}}
				}
				rc.Response = &models.ChatCompletionResponse{Model: rc.Model, Choices: []models.Choice{{Message: msg}}}
				beforeReq, _ := json.Marshal(rc.Request)
				beforeResp, _ := json.Marshal(rc.Response)
				r := privacy.New(mode, []privacy.PatternConfig{{Name: "private", Pattern: w2B1Marker}})
				trace, entry := w2B1Log(t, rc, nil, r)

				for _, sink := range []struct {
					name  string
					value any
				}{{"trace queue", trace}, {"webhook", entry}} {
					encoded, err := json.Marshal(sink.value)
					if err != nil {
						t.Fatal(err)
					}
					containsMarker := bytes.Contains(encoded, []byte(w2B1Marker))
					if containsMarker != (mode == privacy.ModeNone) {
						t.Errorf("%s has incorrect masking under %s: %s", sink.name, mode, encoded)
					}
				}

				messages := []models.Message{trace.ResponseBody.Choices[0].Message}
				if trace.RequestBody != nil {
					messages = append(messages, trace.RequestBody.Messages...)
				}
				for _, message := range messages {
					if len(message.ToolCalls) != 2 {
						t.Fatalf("lost tool calls: %+v", message)
					}
					for i, got := range message.ToolCalls {
						want := msg.ToolCalls[i]
						if mode == privacy.ModeFull {
							want.ID = "[REDACTED]"
							want.Function.Name = "[REDACTED]"
							want.Function.Arguments = "[REDACTED]"
						} else if mode == privacy.ModePatterns && i == 0 {
							want.Function.Arguments = `{"query":"[REDACTED:private]"}`
						}
						if got != want {
							t.Errorf("tool %d = %+v, want %+v", i, got, want)
						}
					}
				}
				afterReq, _ := json.Marshal(rc.Request)
				afterResp, _ := json.Marshal(rc.Response)
				if !bytes.Equal(beforeReq, afterReq) || !bytes.Equal(beforeResp, afterResp) {
					t.Error("redaction mutated live request or response tool calls")
				}
			})
		}
	}
}
