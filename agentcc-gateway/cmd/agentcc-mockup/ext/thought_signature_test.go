package ext

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

// toolHistory is a chat request whose history has one assistant turn with
// the given tool calls and reasoning_details.
func toolHistory(model string, calls []any, details []any) string {
	assistant := map[string]any{"role": "assistant", "content": nil, "tool_calls": calls}
	if details != nil {
		assistant["reasoning_details"] = details
	}
	b, _ := json.Marshal(map[string]any{"model": model, "messages": []any{
		map[string]any{"role": "user", "content": "weather?"},
		assistant,
		map[string]any{"role": "tool", "tool_call_id": "tool_a", "content": "sunny"},
	}})
	return string(b)
}

func call(id string, extra map[string]any) map[string]any {
	c := map[string]any{"id": id, "type": "function", "function": map[string]any{"name": "get_weather", "arguments": "{}"}}
	if extra != nil {
		c["extra_content"] = extra
	}
	return c
}

func encrypted(id, data string) map[string]any {
	return map[string]any{"type": "reasoning.encrypted", "id": id, "data": data, "format": "google-gemini-v1", "index": 0}
}

func TestRequireThoughtSignature(t *testing.T) {
	const gemini = "google/gemini-9-pro"
	googleSig := map[string]any{"google": map[string]any{"thought_signature": "c2lnCg=="}}
	for _, c := range []struct {
		name   string
		body   string
		status int
	}{
		{"unsigned", toolHistory(gemini, []any{call("tool_a", nil)}, nil), 400},
		{"signed by reasoning_details", toolHistory(gemini, []any{call("tool_a", nil)}, []any{encrypted("tool_a", "EoEDCv4C")}), 200},
		{"reasoning for another call", toolHistory(gemini, []any{call("tool_a", nil)}, []any{encrypted("tool_b", "EoEDCv4C")}), 400},
		{"encrypted reasoning without data", toolHistory(gemini, []any{call("tool_a", nil)}, []any{encrypted("tool_a", "")}), 400},
		{"reasoning text is no signature", toolHistory(gemini, []any{call("tool_a", nil)},
			[]any{map[string]any{"type": "reasoning.text", "id": "tool_a", "text": "thinking"}}), 400},
		{"signed by extra_content", toolHistory(gemini, []any{call("tool_a", googleSig)}, nil), 200},
		{"one of two unsigned", toolHistory(gemini, []any{call("tool_a", googleSig), call("tool_b", nil)}, nil), 400},
		{"another vendor's model", toolHistory("test/model-a", []any{call("tool_a", nil)}, nil), 200},
		{"no tool history", chatBody(gemini, false), 200},
	} {
		m := newExtMock(t)
		status, b := m.chat(t, "key-1", c.body, mock.BehaviorHeader, "require_thought_signature")
		if status != c.status {
			t.Errorf("%s: status %d, want %d (%s)", c.name, status, c.status, b)
			continue
		}
		_, charged := m.state.TotalChargeMicros()
		if c.status == http.StatusBadRequest && (errorCode(t, b) != "invalid_request" || charged != 0) {
			t.Errorf("%s: refused as %v with %d charges, want invalid_request and none (%s)", c.name, errorCode(t, b), charged, b)
		}
		if c.status == http.StatusOK && charged != 1 {
			t.Errorf("%s: %d charges, want 1", c.name, charged)
		}
	}
}

func TestRequireThoughtSignatureTakesNoArgument(t *testing.T) {
	m := newExtMock(t)
	status, b := m.chat(t, "key-1", chatBody("google/gemini-9-pro", false), mock.BehaviorHeader, "require_thought_signature:yes")
	if status != http.StatusBadRequest || errorCode(t, b) != "invalid_mock_directive" {
		t.Fatalf("status %d body %s", status, b)
	}
}
