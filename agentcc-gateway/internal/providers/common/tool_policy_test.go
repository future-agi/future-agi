package common

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestPrepareToolPolicyKeepsSourceRequestIntact(t *testing.T) {
	var req models.ChatCompletionRequest
	raw := []byte(`{"tools":[{"type":"function","function":{"name":"allowed"},"allowed_callers":["direct"]},{"type":"function","function":{"name":"restricted"}}],"tool_choice":{"type":"allowed_tools","allowed_tools":{"mode":"auto","tools":[{"type":"function","name":"allowed"}]}}}`)
	if err := json.Unmarshal(raw, &req); err != nil {
		t.Fatal(err)
	}
	before, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	prepared, err := PrepareDirectToolPolicy(&req, ToolPolicySupport{Provider: "synthetic"})
	if err != nil {
		t.Fatal(err)
	}
	after, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Fatal("adapter mutated shared request")
	}
	if len(prepared.Tools) != 1 || prepared.Tools[0].Function.Name != "allowed" || prepared.Tools[0].AllowedCallers != nil {
		t.Fatalf("prepared tools=%+v", prepared.Tools)
	}
}
