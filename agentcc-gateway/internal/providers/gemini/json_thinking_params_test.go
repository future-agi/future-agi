package gemini

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The release notes' Known limits say this adapter sends json_object,
// json_schema, thinking_budget and reasoning_effort: json_object as JSON
// output, json_schema with its schema, thinking_budget as the thinking
// budget and reasoning_effort (when no budget is set) as the thinking level.
func TestJSONFormatsAndThinkingThisAdapterSends(t *testing.T) {
	msgs := []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}
	cases := []struct {
		name string
		req  *models.ChatCompletionRequest
		want []string
		not  []string
	}{
		{
			name: "json_object",
			req:  &models.ChatCompletionRequest{Model: "gemini-2.5-flash", Messages: msgs, ResponseFormat: &models.ResponseFormat{Type: "json_object"}},
			want: []string{`"responseMimeType":"application/json"`},
			not:  []string{"responseSchema", "thinkingConfig"},
		},
		{
			name: "json_schema",
			req: &models.ChatCompletionRequest{Model: "gemini-2.5-flash", Messages: msgs, ResponseFormat: &models.ResponseFormat{
				Type:       "json_schema",
				JSONSchema: json.RawMessage(`{"name":"fmt","schema":{"type":"object","properties":{"fmtmarker":{"type":"string"}}}}`),
			}},
			want: []string{`"responseMimeType":"application/json"`, `"responseSchema":{"type":"object","properties":{"fmtmarker":`},
		},
		{
			name: "thinking_budget",
			req: &models.ChatCompletionRequest{Model: "gemini-2.5-flash", Messages: msgs, Extra: map[string]json.RawMessage{
				"thinking_budget": json.RawMessage(`1777`),
			}},
			want: []string{`"thinkingConfig":{"includeThoughts":true,`, `"thinkingBudget":1777`},
		},
		{
			name: "reasoning_effort",
			req: &models.ChatCompletionRequest{Model: "gemini-2.5-flash", Messages: msgs, Extra: map[string]json.RawMessage{
				"reasoning_effort": json.RawMessage(`"HIGH"`),
			}},
			want: []string{`"thinkingConfig":{"includeThoughts":true,`, `"thinkingLevel":"high"`},
			not:  []string{"thinkingBudget"},
		},
		{
			name: "neither",
			req:  &models.ChatCompletionRequest{Model: "gemini-2.5-flash", Messages: msgs},
			not:  []string{"thinkingConfig", "responseMimeType"},
		},
	}
	for _, c := range cases {
		out := translatedJSON(t, c.req)
		for _, w := range c.want {
			if !strings.Contains(out, w) {
				t.Errorf("%s: translated request %s lacks %s", c.name, out, w)
			}
		}
		for _, n := range c.not {
			if strings.Contains(out, n) {
				t.Errorf("%s: translated request %s carries %s", c.name, out, n)
			}
		}
	}
}
