package reflexion

import "testing"

func TestPolicyExecutionSemantics(t *testing.T) {
	for _, tc := range []struct {
		name, stage, action, mode string
		cfg                       map[string]interface{}
		valid                     bool
	}{
		{"output", "post", "block", "sync", map[string]interface{}{"on_block": "reflect"}, true},
		{"output", "pre", "block", "sync", map[string]interface{}{"on_block": "reflect"}, false},
		{"output", "post", "warn", "sync", map[string]interface{}{"on_block": "reflect"}, false},
		{"output", "post", "block", "async", map[string]interface{}{"on_block": "reflect"}, false},
		{"output", "post", "block", "sync", map[string]interface{}{"on_block": "reflect", "mode": "async"}, true},
		{"prompt-injection", "post", "block", "sync", map[string]interface{}{"stage": "post"}, false},
		{"topic-restriction", "post", "block", "sync", map[string]interface{}{"on_block": "reflect"}, false},
		{"language-detection", "post", "block", "sync", map[string]interface{}{"stage": "post"}, false},
		{"system-prompt-protection", "post", "block", "sync", map[string]interface{}{"stage": "post"}, false},
		{"hallucination-detection", "pre", "block", "sync", map[string]interface{}{"stage": "pre"}, false},
		{"data-leakage-prevention", "post", "block", "sync", map[string]interface{}{"on_block": "reflect"}, true},
		{"output", "post", "block", "sync", map[string]interface{}{"on_block": "reflect", "stage": "pre"}, true},
		{"tool-permissions", "post", "block", "sync", map[string]interface{}{"on_block": "reflect", "apply_to": "request"}, false},
		{"custom", "post", "block", "sync", map[string]interface{}{"on_block": "reflect", "provider": "tool_permissions", "apply_to": "request"}, false},
		{"custom", "post", "block", "sync", map[string]interface{}{"on_block": "reflect", "provider": "tool_permission", "apply_to": "request"}, false},
		{"custom", "post", "block", "sync", map[string]interface{}{"on_block": "reflect", "provider": "tool_permissions", "apply_to": "both"}, true},
	} {
		_, err := Validate(tc.cfg, tc.name, tc.stage, tc.action, tc.mode)
		if (err == nil) != tc.valid {
			t.Errorf("name=%s stage=%s action=%s mode=%s cfg=%v error=%v", tc.name, tc.stage, tc.action, tc.mode, tc.cfg, err)
		}
	}
}
