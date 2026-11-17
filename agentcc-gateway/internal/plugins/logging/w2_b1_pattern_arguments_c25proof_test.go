package logging

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Arguments contain another JSON document, not plain message text.
// Inspect both publication copies after decoding that document, not just the
// escaped bytes of the enclosing webhook or trace record.
func TestW2B1PatternEscapedToolArguments(t *testing.T) {
	const email = "reviewer@example.invalid"
	const escapedEmail = "reviewer" + `\` + "u0040example.invalid"
	const quoted = "private\"\nvalue"
	const benign = `{"count":9007199254740993,"ok":true}`
	for _, fixture := range []struct {
		name, args string
		invalid    bool
	}{
		{"escaped-value", `{"email":"` + escapedEmail + `"}`, false},
		{"escaped-key-and-array", `{"` + escapedEmail + `":[{"email":"` + escapedEmail + `"}]}`, false},
		{"escaped-quote-newline", `{"query":"private\"\nvalue"}`, false},
		{"partial-stream-json", `{"email":"` + escapedEmail, true},
		{"trailing-document", `{"ok":true} {"email":"` + escapedEmail + `"}`, true},
	} {
		for _, source := range []string{"org", "global"} {
			for _, shape := range []string{"request-and-response", "response-only-stream"} {
				t.Run(fixture.name+"/"+source+"/"+shape, func(t *testing.T) {
					rc := newRC()
					rc.Privacy.ContentMode = models.ContentModeFull
					rc.Metadata[tenant.MetadataKeyOrgID] = "w2-b1-org"
					rc.IsStream = shape == "response-only-stream"
					msg := models.Message{Role: "assistant", ToolCalls: []models.ToolCall{
						{ID: "call-1", Type: "function", Function: models.FunctionCall{Name: "lookup", Arguments: fixture.args}},
						{ID: "call-2", Type: "function", Function: models.FunctionCall{Name: "count", Arguments: benign}},
					}}
					rc.Request = nil
					if shape == "request-and-response" {
						rc.Request = &models.ChatCompletionRequest{Model: rc.Model, Messages: []models.Message{msg}}
					}
					rc.Response = &models.ChatCompletionResponse{Model: rc.Model, Choices: []models.Choice{{Message: msg}}}
					beforeReq, _ := json.Marshal(rc.Request)
					beforeResp, _ := json.Marshal(rc.Response)
					patterns := []privacy.PatternConfig{{Name: "email", Pattern: `reviewer@example[.]invalid`}, {Name: "quoted", Pattern: quoted}}
					var store *tenant.Store
					global := privacy.New(privacy.ModePatterns, patterns)
					if source == "org" {
						store = tenant.NewStore()
						store.Set("w2-b1-org", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{
							Enabled: true, Mode: privacy.ModePatterns, ContentMode: "full",
							Patterns: []*tenant.RedactPatternConfig{{Name: "email", Pattern: patterns[0].Pattern}, {Name: "quoted", Pattern: quoted}},
						}})
						global = privacy.New(privacy.ModePatterns, nil) // org patterns must win
					}
					trace, entry := w2B1Log(t, rc, store, global)
					check := func(label string, message models.Message) {
						t.Helper()
						if len(message.ToolCalls) != 2 {
							t.Fatalf("%s lost tool calls", label)
						}
						args := message.ToolCalls[0].Function.Arguments
						if !strings.Contains(args, "[REDACTED") {
							t.Errorf("%s lacks a masking indication: %s", label, args)
						}
						if fixture.invalid {
							if args != "[REDACTED]" {
								t.Errorf("%s retained uninterpretable arguments: %s", label, args)
							}
						} else if args != "[REDACTED]" {
							var decoded any
							if err := json.Unmarshal([]byte(args), &decoded); err != nil {
								t.Fatalf("%s retained invalid argument JSON: %v", label, err)
							}
							normalized, _ := json.Marshal(decoded)
							for _, secret := range []string{email, quoted} {
								encoded, _ := json.Marshal(secret)
								if bytes.Contains(normalized, encoded[1:len(encoded)-1]) {
									t.Errorf("%s exposes decoded sensitive arguments: %s", label, normalized)
								}
							}
						}
						if got := message.ToolCalls[1].Function.Arguments; got != benign {
							t.Errorf("%s changed benign arguments or integer precision: %s", label, got)
						}
					}
					check("trace response", trace.ResponseBody.Choices[0].Message)
					if trace.RequestBody != nil {
						check("trace request", trace.RequestBody.Messages[0])
					}
					respBytes, _ := json.Marshal(entry["response_body"])
					var response models.ChatCompletionResponse
					if err := json.Unmarshal(respBytes, &response); err != nil || len(response.Choices) != 1 {
						t.Fatalf("invalid webhook response: %s (%v)", respBytes, err)
					}
					check("webhook response", response.Choices[0].Message)
					if rc.Request != nil {
						reqBytes, _ := json.Marshal(entry["request_body"])
						var request models.ChatCompletionRequest
						if err := json.Unmarshal(reqBytes, &request); err != nil || len(request.Messages) != 1 {
							t.Fatalf("invalid webhook request: %s (%v)", reqBytes, err)
						}
						check("webhook request", request.Messages[0])
					}
					afterReq, _ := json.Marshal(rc.Request)
					afterResp, _ := json.Marshal(rc.Response)
					if !bytes.Equal(beforeReq, afterReq) || !bytes.Equal(beforeResp, afterResp) || rc.Metadata["privacy_redacted"] != "" {
						t.Error("redaction mutated live arguments or parallel-plugin metadata")
					}
					if entry["model"] != rc.Model || entry["content_mode"] != "full" || trace.Metadata["privacy_redacted"] != "true" {
						t.Error("redaction lost retained request metadata")
					}
				})
			}
		}
	}
}
