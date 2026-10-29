package modeldb

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func toolRequest() *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
		Tools:    []models.Tool{{Type: "function", Function: models.ToolFunction{Name: "f"}}},
	}
}

// The price table often leaves a flag out for a model that has the
// capability, and a refresh adds many such entries. Validation refuses only
// on a flag the table states as false.
func TestParsedFlags_RefuseOnlyWhatTheTableStates(t *testing.T) {
	parsed, err := parseLiteLLM([]byte(`{
		"states-no-tools": {"mode": "chat", "input_cost_per_token": 1e-6, "supports_function_calling": false},
		"states-tools":    {"mode": "chat", "input_cost_per_token": 1e-6, "supports_function_calling": true},
		"states-nothing":  {"mode": "chat", "input_cost_per_token": 1e-6}
	}`))
	if err != nil {
		t.Fatal(err)
	}
	for model, wantOK := range map[string]bool{"states-no-tools": false, "states-tools": true, "states-nothing": true} {
		if _, ok := parsed[model].Capabilities.ValidateRequest(toolRequest()); ok != wantOK {
			t.Errorf("%s: tools allowed = %v, want %v", model, ok, wantOK)
		}
	}
}

// An override states the flag, so validation holds the model to it.
func TestOverrideStatesTheFlag(t *testing.T) {
	no := false
	m := &ModelInfo{ID: "m", Capabilities: CapabilityFlags{FunctionCallingUnstated: true}}
	mergeOverride(m, ModelOverride{Capabilities: &CapOverride{FunctionCalling: &no}})
	if _, ok := m.Capabilities.ValidateRequest(toolRequest()); ok {
		t.Error("tools allowed on a model an override says has none")
	}
}

// Upstream drops capability flags from entries it keeps; the refresh carries
// a flag forward when the old table stated it, so these models keep working
// with tools and images.
func TestBundledModels_KeepTheirStatedFlags(t *testing.T) {
	db := New(BundledModels, nil)
	checks := []struct {
		model, flag string
		stated      func(CapabilityFlags) bool
	}{
		{"azure/gpt-5-chat", "function calling", func(c CapabilityFlags) bool { return c.FunctionCalling }},
		{"azure_ai/deepseek-v3.1", "function calling", func(c CapabilityFlags) bool { return c.FunctionCalling }},
		{"azure/gpt-5.1-chat", "vision", func(c CapabilityFlags) bool { return c.Vision }},
		{"azure/gpt-5-chat", "response schema", func(c CapabilityFlags) bool { return c.ResponseSchema }},
	}
	for _, c := range checks {
		info, ok := db.Get(c.model)
		if !ok {
			t.Errorf("%s missing from the bundled table", c.model)
			continue
		}
		if !c.stated(info.Capabilities) {
			t.Errorf("%s lost its %s flag, which the previous table stated", c.model, c.flag)
		}
	}
}
