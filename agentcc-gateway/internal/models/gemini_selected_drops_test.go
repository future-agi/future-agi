package models

import (
	"encoding/json"
	"slices"
	"testing"
)

func TestGeminiOmittedParamsUsesSelectedTools(t *testing.T) {
	tools := []Tool{
		{Type: "function", Function: ToolFunction{Name: "lookup"}},
		{Type: "function", Function: ToolFunction{Name: "calculate"}, AllowedCallers: []string{"code_execution"}},
	}
	for _, tc := range []struct {
		name string
		want []string
	}{
		{"lookup", nil},
		{"calculate", []string{"allowed_callers"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := &ChatCompletionRequest{
				Tools:      tools,
				ToolChoice: json.RawMessage(`{"type":"allowed_tools","allowed_tools":{"mode":"auto","tools":[{"type":"function","name":"` + tc.name + `"}]}}`),
			}
			if got := GeminiOmittedParams(req); !slices.Equal(got, tc.want) {
				t.Fatalf("GeminiOmittedParams() = %v, want %v", got, tc.want)
			}
		})
	}
}
