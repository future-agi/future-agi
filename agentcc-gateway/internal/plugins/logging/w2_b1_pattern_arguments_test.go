package logging

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// These defensive controls check retained copies with ordinary synthetic values.
// They do not constitute a pre-fix reproduction of the encoded-input findings.
func TestW2B1PatternToolArgumentsControls(t *testing.T) {
	const benign = `{"count":9007199254740993,"ok":true}`
	for _, fixture := range []struct {
		name, arguments, masked string
	}{
		{"object", `{"label":"sample-sensitive"}`, `{"label":"[REDACTED:sample]"}`},
		{"nested", `{"sample-sensitive":[{"label":"sample-sensitive"}]}`, `{"[REDACTED:sample]":[{"label":"[REDACTED:sample]"}]}`},
		{"incomplete", `{"count":`, "[REDACTED]"},
		{"empty", "", ""},
	} {
		for _, source := range []string{"org", "global"} {
			for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull, privacy.ModeNone} {
				for _, responseOnly := range []bool{false, true} {
					shape := "request-and-response"
					if responseOnly {
						shape = "response-only-stream"
					}
					t.Run(fixture.name+"/"+source+"/"+mode+"/"+shape, func(t *testing.T) {
						rc := newRC()
						rc.Privacy.ContentMode = models.ContentModeFull
						rc.Metadata[tenant.MetadataKeyOrgID] = "w2-b1-org"
						rc.Metadata["privacy_mode"] = privacy.ModeNone
						rc.IsStream = responseOnly
						msg := models.Message{Role: "assistant", ToolCalls: []models.ToolCall{
							{ID: "call-1", Type: "function", Function: models.FunctionCall{Name: "lookup", Arguments: fixture.arguments}},
							{ID: "call-2", Type: "function", Function: models.FunctionCall{Name: "count", Arguments: benign}},
						}}
						rc.Request = nil
						if !responseOnly {
							rc.Request = &models.ChatCompletionRequest{Model: rc.Model, Messages: []models.Message{msg}}
						}
						rc.Response = &models.ChatCompletionResponse{Model: rc.Model, Choices: []models.Choice{{Message: msg}}}
						beforeReq, _ := json.Marshal(rc.Request)
						beforeResp, _ := json.Marshal(rc.Response)
						patterns := []privacy.PatternConfig{{Name: "sample", Pattern: "sample-sensitive"}}
						global := privacy.New(mode, patterns)
						var store *tenant.Store
						if source == "org" {
							store = tenant.NewStore()
							store.Set("w2-b1-org", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{
								Enabled: true, Mode: mode, ContentMode: "full",
								Patterns: []*tenant.RedactPatternConfig{{Name: "sample", Pattern: "sample-sensitive"}},
							}})
							global = privacy.New(privacy.ModeNone, nil)
						}
						trace, entry := w2B1Log(t, rc, store, global)
						want, wantBenign := fixture.masked, benign
						if mode == privacy.ModeNone {
							want = fixture.arguments
						} else if mode == privacy.ModeFull {
							if fixture.arguments != "" {
								want = "[REDACTED]"
							}
							wantBenign = "[REDACTED]"
						}
						check := func(label string, message models.Message) {
							t.Helper()
							if len(message.ToolCalls) != 2 {
								t.Fatalf("%s lost tool calls", label)
							}
							if got := message.ToolCalls[0].Function.Arguments; got != want {
								t.Errorf("%s arguments = %q, want %q", label, got, want)
							}
							if got := message.ToolCalls[1].Function.Arguments; got != wantBenign {
								t.Errorf("%s benign arguments = %q, want %q", label, got, wantBenign)
							}
						}
						if trace.ResponseBody == nil || len(trace.ResponseBody.Choices) != 1 {
							t.Fatal("missing trace response")
						}
						check("trace response", trace.ResponseBody.Choices[0].Message)
						respBytes, _ := json.Marshal(entry["response_body"])
						var response models.ChatCompletionResponse
						if err := json.Unmarshal(respBytes, &response); err != nil || len(response.Choices) != 1 {
							t.Fatalf("invalid webhook response: %v", err)
						}
						check("webhook response", response.Choices[0].Message)
						if !responseOnly {
							if trace.RequestBody == nil || len(trace.RequestBody.Messages) != 1 {
								t.Fatal("missing trace request")
							}
							check("trace request", trace.RequestBody.Messages[0])
							reqBytes, _ := json.Marshal(entry["request_body"])
							var request models.ChatCompletionRequest
							if err := json.Unmarshal(reqBytes, &request); err != nil || len(request.Messages) != 1 {
								t.Fatalf("invalid webhook request: %v", err)
							}
							check("webhook request", request.Messages[0])
						}
						wantRedacted := "true"
						if mode == privacy.ModeNone {
							wantRedacted = ""
						}
						if entry["model"] != rc.Model || entry["content_mode"] != "full" || trace.Metadata["privacy_redacted"] != wantRedacted {
							t.Error("incorrect retained request metadata")
						}
						afterReq, _ := json.Marshal(rc.Request)
						afterResp, _ := json.Marshal(rc.Response)
						if !bytes.Equal(beforeReq, afterReq) || !bytes.Equal(beforeResp, afterResp) || rc.Metadata["privacy_redacted"] != "" {
							t.Error("logging mutated live content or parallel-plugin metadata")
						}
					})
				}
			}
		}
	}
}
