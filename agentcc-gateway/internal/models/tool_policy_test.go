package models

import (
	"encoding/json"
	"testing"
)

func TestAllowedToolsChoiceFiltersDeclarations(t *testing.T) {
	choice, err := ParseAllowedToolsChoice(json.RawMessage(`{"type":"allowed_tools","allowed_tools":{"mode":"auto","tools":[{"type":"function","function":{"name":"tool_b"}}]}}`))
	if err != nil {
		t.Fatal(err)
	}
	tools := []Tool{
		{Type: "function", Function: ToolFunction{Name: "tool_a"}},
		{Type: "function", Function: ToolFunction{Name: "tool_b"}},
	}
	selected, err := choice.Filter(tools)
	if err != nil {
		t.Fatal(err)
	}
	if choice.Mode != "auto" || len(selected) != 1 || selected[0].Function.Name != "tool_b" {
		t.Fatalf("choice = %+v, selected = %+v", choice, selected)
	}
}

func TestAllowedToolsChoiceFiltersMultipleNames(t *testing.T) {
	choice, err := ParseAllowedToolsChoice(json.RawMessage(`{"type":"allowed_tools","allowed_tools":{"mode":"required","tools":[{"type":"function","name":"tool_b"},{"type":"function","name":"tool_c"}]}}`))
	if err != nil {
		t.Fatal(err)
	}
	tools := []Tool{
		{Type: "function", Function: ToolFunction{Name: "tool_a"}},
		{Type: "function", Function: ToolFunction{Name: "tool_b"}},
		{Type: "function", Function: ToolFunction{Name: "tool_c"}},
	}
	selected, err := choice.Filter(tools)
	if err != nil {
		t.Fatal(err)
	}
	if len(selected) != 2 || selected[0].Function.Name != "tool_b" || selected[1].Function.Name != "tool_c" {
		t.Fatalf("selected tools = %+v, want tool_b and tool_c", selected)
	}
}

func TestAllowedToolsChoiceRejectsUnknownAndMalformedRestrictions(t *testing.T) {
	for _, raw := range []string{
		`{"type":"allowed_tools","allowed_tools":{"mode":"auto","tools":[]}}`,
		`{"type":"allowed_tools","allowed_tools":{"mode":"sometimes","tools":[{"type":"function","name":"tool_a"}]}}`,
		`{"type":"allowed_tools","allowed_tools":{"mode":"auto","tools":[{"type":"function","name":"tool_a"},{"type":"function","name":"tool_a"}]}}`,
	} {
		if _, err := ParseAllowedToolsChoice(json.RawMessage(raw)); err == nil {
			t.Errorf("accepted malformed allowed_tools choice %s", raw)
		}
	}
	choice, err := ParseAllowedToolsChoice(json.RawMessage(`{"type":"allowed_tools","allowed_tools":{"mode":"required","tools":[{"type":"function","name":"missing"}]}}`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := choice.Filter([]Tool{{Type: "function", Function: ToolFunction{Name: "other"}}}); err == nil {
		t.Fatal("accepted a restriction naming a tool absent from tools")
	}
}

func TestParallelToolCallsFalseSurvivesCanonicalJSON(t *testing.T) {
	var req ChatCompletionRequest
	if err := json.Unmarshal([]byte(`{"model":"m","parallel_tool_calls":false}`), &req); err != nil {
		t.Fatal(err)
	}
	if req.ParallelToolCalls == nil || *req.ParallelToolCalls {
		t.Fatalf("parallel_tool_calls = %v, want explicit false", req.ParallelToolCalls)
	}
	encoded, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	var roundTrip map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &roundTrip); err != nil {
		t.Fatal(err)
	}
	if string(roundTrip["parallel_tool_calls"]) != "false" {
		t.Fatalf("parallel_tool_calls = %s after round trip", roundTrip["parallel_tool_calls"])
	}
}
