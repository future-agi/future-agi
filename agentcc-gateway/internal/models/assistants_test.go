package models

import (
	"encoding/json"
	"testing"
)

func TestExtractModelFromBody(t *testing.T) {
	tests := []struct {
		name string
		body string
		want string
	}{
		{"valid model", `{"model":"gpt-4o","instructions":"help"}`, "gpt-4o"},
		{"missing model", `{"instructions":"help"}`, ""},
		{"empty body", `{}`, ""},
		{"malformed json", `{invalid`, ""},
		{"empty string", ``, ""},
		{"model with prefix", `{"model":"openai/gpt-4o"}`, "openai/gpt-4o"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ExtractModelFromBody([]byte(tt.body))
			if got != tt.want {
				t.Errorf("ExtractModelFromBody() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestReplaceModelInBody(t *testing.T) {
	tests := []struct {
		name     string
		body     string
		newModel string
		wantErr  bool
		check    func(t *testing.T, result []byte)
	}{
		{
			name:     "replace existing model",
			body:     `{"model":"openai/gpt-4o","instructions":"help"}`,
			newModel: "gpt-4o",
			check: func(t *testing.T, result []byte) {
				got := ExtractModelFromBody(result)
				if got != "gpt-4o" {
					t.Errorf("model = %q, want %q", got, "gpt-4o")
				}
			},
		},
		{
			name:     "body without model unchanged",
			body:     `{"instructions":"help"}`,
			newModel: "gpt-4o",
			check: func(t *testing.T, result []byte) {
				got := ExtractModelFromBody(result)
				if got != "" {
					t.Errorf("model = %q, want empty", got)
				}
			},
		},
		{
			name:     "preserves other fields",
			body:     `{"model":"old","name":"test","instructions":"help"}`,
			newModel: "new",
			check: func(t *testing.T, result []byte) {
				got := ExtractModelFromBody(result)
				if got != "new" {
					t.Errorf("model = %q, want %q", got, "new")
				}
				// Check instructions are preserved.
				var m struct{ Instructions string `json:"instructions"` }
				if err := jsonUnmarshal(result, &m); err == nil && m.Instructions != "help" {
					t.Errorf("instructions not preserved")
				}
			},
		},
		{
			name:    "malformed json",
			body:    `{invalid`,
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := ReplaceModelInBody([]byte(tt.body), tt.newModel)
			if (err != nil) != tt.wantErr {
				t.Errorf("error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if tt.check != nil && err == nil {
				tt.check(t, result)
			}
		})
	}
}

func TestExtractStreamFromBody(t *testing.T) {
	tests := []struct {
		name string
		body string
		want bool
	}{
		{"stream true", `{"stream":true}`, true},
		{"stream false", `{"stream":false}`, false},
		{"missing stream", `{"model":"gpt-4o"}`, false},
		{"malformed json", `{invalid`, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ExtractStreamFromBody([]byte(tt.body))
			if got != tt.want {
				t.Errorf("ExtractStreamFromBody() = %v, want %v", got, tt.want)
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

// jsonUnmarshal is a test helper using the standard library.
func jsonUnmarshal(data []byte, v interface{}) error {
	return json.Unmarshal(data, v)
}
