package models

import (
	"testing"
)

// TestParseAssistantsBody: the model and stream of an Assistants body are
// read as OpenAI reads them (the exact-case member, the last of a name; null
// is absent), and a body chat refuses is refused (400 invalid_json): one
// that is not a JSON object, or a "model" (any case) that is not a string or
// a "stream" that is not a bool, even where a later member of the name is.
func TestParseAssistantsBody(t *testing.T) {
	for _, tt := range []struct {
		name, body string
		model      string
		stream     bool
		refused    bool
	}{
		{"a model", `{"model":"gpt-4o","instructions":"help"}`, "gpt-4o", false, false},
		{"no model", `{"instructions":"help"}`, "", false, false},
		{"empty object", `{}`, "", false, false},
		{"empty body", ``, "", false, false},
		{"blank body", " \n", "", false, false},
		{"a provider prefix", `{"model":"openai/gpt-4o"}`, "openai/gpt-4o", false, false},
		{"two models, the last", `{"model":"gpt-4o-mini","model":"gpt-4o"}`, "gpt-4o", false, false},
		{"null model", `{"model":null}`, "", false, false},
		{"a model, then null", `{"model":"gpt-4o","model":null}`, "", false, false},
		{"another case is not the model", `{"MODEL":"gpt-4o"}`, "", false, false},
		{"stream", `{"stream":true}`, "", true, false},
		{"stream false, then true", `{"stream":false,"stream":true}`, "", true, false},
		{"malformed", `{invalid`, "", false, true},
		{"a list", `[{"model":"gpt-4o"}]`, "", false, true},
		{"null", `null`, "", false, true},
		{"a number model, then a string", `{"model":0,"model":"gpt-4o"}`, "", false, true},
		{"an object model, then a string", `{"model":{},"model":"gpt-4o"}`, "", false, true},
		{"a string model, then a number", `{"model":"gpt-4o","model":0}`, "", false, true},
		{"a number MODEL", `{"MODEL":0,"model":"gpt-4o"}`, "", false, true},
		{"a number stream, then true", `{"stream":0,"stream":true}`, "", false, true},
		{"a string stream", `{"stream":"true"}`, "", false, true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			b, err := ParseAssistantsBody([]byte(tt.body))
			if tt.refused {
				apiErr, ok := err.(*APIError)
				if !ok || apiErr.Status != 400 || apiErr.Code != "invalid_json" {
					t.Fatalf("err = %v, want 400 invalid_json", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("err = %v", err)
			}
			if b.Model != tt.model || b.Stream != tt.stream {
				t.Errorf("model %q, stream %v; want %q, %v", b.Model, b.Stream, tt.model, tt.stream)
			}
		})
	}
}

// TestAssistantsBodyWithModel: a body with a "model" member is sent written
// again, with one "model" (the one given, or the member as it came for ""),
// its other members kept; a body without one is sent as it came.
func TestAssistantsBodyWithModel(t *testing.T) {
	for _, tt := range []struct {
		name, body, model, want string
	}{
		{"a model replaced", `{"model":"openai/gpt-4o","instructions":"help"}`, "gpt-4o", `{"instructions":"help","model":"gpt-4o"}`},
		{"two models, one sent", `{"model":"gpt-4o-mini","model":"gpt-4o"}`, "gpt-4o", `{"model":"gpt-4o"}`},
		{"null kept, one sent", `{"model":"gpt-4o","model":null}`, "", `{"model":null}`},
		{"no model, as it came", `{ "instructions" : "help" }`, "gpt-4o", `{ "instructions" : "help" }`},
		{"empty body, as it came", ``, "", ``},
	} {
		t.Run(tt.name, func(t *testing.T) {
			b, err := ParseAssistantsBody([]byte(tt.body))
			if err != nil {
				t.Fatal(err)
			}
			got, err := b.WithModel(tt.model)
			if err != nil || string(got) != tt.want {
				t.Errorf("WithModel(%q) = %s, %v; want %s", tt.model, got, err, tt.want)
			}
		})
	}
}

func TestExtractRunUsage(t *testing.T) {
	tests := []struct {
		name string
		body string
		want *AssistantsRunUsage
	}{
		{
			name: "valid usage",
			body: `{"id":"run_123","status":"completed","usage":{"prompt_tokens":100,"completion_tokens":50,"total_tokens":150}}`,
			want: &AssistantsRunUsage{PromptTokens: 100, CompletionTokens: 50, TotalTokens: 150},
		},
		{
			name: "missing usage",
			body: `{"id":"run_123","status":"in_progress"}`,
			want: nil,
		},
		{
			name: "null usage",
			body: `{"id":"run_123","usage":null}`,
			want: nil,
		},
		{
			name: "malformed json",
			body: `{invalid`,
			want: nil,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ExtractRunUsage([]byte(tt.body))
			if tt.want == nil {
				if got != nil {
					t.Errorf("ExtractRunUsage() = %v, want nil", got)
				}
				return
			}
			if got == nil {
				t.Fatal("ExtractRunUsage() = nil, want non-nil")
			}
			if got.PromptTokens != tt.want.PromptTokens || got.CompletionTokens != tt.want.CompletionTokens || got.TotalTokens != tt.want.TotalTokens {
				t.Errorf("ExtractRunUsage() = %+v, want %+v", got, tt.want)
			}
		})
	}
}

func TestExtractMessageContent(t *testing.T) {
	tests := []struct {
		name string
		body string
		want string
	}{
		{"string content", `{"role":"user","content":"hello world"}`, "hello world"},
		{
			"array content with text",
			`{"role":"user","content":[{"type":"text","text":"hello"},{"type":"text","text":"world"}]}`,
			"hello\nworld",
		},
		{
			"mixed content types",
			`{"role":"user","content":[{"type":"text","text":"hello"},{"type":"image_url","image_url":{"url":"http://x"}}]}`,
			"hello",
		},
		{"empty content", `{"role":"user"}`, ""},
		{"malformed json", `{invalid`, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ExtractMessageContent([]byte(tt.body))
			if got != tt.want {
				t.Errorf("ExtractMessageContent() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestExtractThreadMessagesContent(t *testing.T) {
	tests := []struct {
		name string
		body string
		want string
	}{
		{
			"multiple messages",
			`{"messages":[{"role":"user","content":"msg1"},{"role":"user","content":"msg2"}]}`,
			"msg1\nmsg2",
		},
		{"no messages", `{}`, ""},
		{"empty messages", `{"messages":[]}`, ""},
		{
			"mixed content types",
			`{"messages":[{"role":"user","content":[{"type":"text","text":"hello"}]},{"role":"user","content":"world"}]}`,
			"hello\nworld",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ExtractThreadMessagesContent([]byte(tt.body))
			if got != tt.want {
				t.Errorf("ExtractThreadMessagesContent() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestExtractRunAdditionalMessagesContent(t *testing.T) {
	tests := []struct {
		name string
		body string
		want string
	}{
		{
			"with additional messages",
			`{"model":"gpt-4o","additional_messages":[{"role":"user","content":"extra"}]}`,
			"extra",
		},
		{"without additional messages", `{"model":"gpt-4o"}`, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ExtractRunAdditionalMessagesContent([]byte(tt.body))
			if got != tt.want {
				t.Errorf("ExtractRunAdditionalMessagesContent() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestExtractThreadAndRunMessagesContent(t *testing.T) {
	tests := []struct {
		name string
		body string
		want string
	}{
		{
			"nested thread messages",
			`{"model":"gpt-4o","thread":{"messages":[{"role":"user","content":"hello from thread"}]}}`,
			"hello from thread",
		},
		{"no thread", `{"model":"gpt-4o"}`, ""},
		{"empty thread messages", `{"model":"gpt-4o","thread":{"messages":[]}}`, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ExtractThreadAndRunMessagesContent([]byte(tt.body))
			if got != tt.want {
				t.Errorf("ExtractThreadAndRunMessagesContent() = %q, want %q", got, tt.want)
			}
		})
	}
}
