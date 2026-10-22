package expression

import (
	"context"
	"testing"
)

// An expression never reads a credential from its input's metadata (the
// guardrail plugin's copy never holds one); it reads everything else.
func TestExpr_NeverReadsACredential(t *testing.T) {
	meta := map[string]string{
		"authorization": "Bearer sk-agentcc-expression-0123",
		"session_token": "planted-session-token",
		"auth_key_id":   "key_1",
	}
	for _, tt := range []struct {
		expr      string
		triggered bool
	}{
		{`metadata.authorization contains "sk-agentcc-"`, false},
		{`metadata.session_token == "planted-session-token"`, false},
		{`metadata.auth_key_id == "key_1"`, true},
	} {
		g := New("expr", map[string]interface{}{"expression": tt.expr})
		res := g.Check(context.Background(), makeInput("gpt-4o", nil, meta))
		if triggered := !res.Pass; triggered != tt.triggered {
			t.Errorf("%s: triggered = %v, want %v", tt.expr, triggered, tt.triggered)
		}
	}
}
