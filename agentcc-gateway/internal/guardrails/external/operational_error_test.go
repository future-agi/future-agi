package external

import "testing"

func TestAdapterParseFailuresAreOperational(t *testing.T) {
	for _, provider := range []string{"lakera", "azure_content_safety", "presidio", "llama_guard", "bedrock_guardrails", "hiddenlayer", "dynamoai", "enkrypt", "ibm_ai", "pangea", "zscaler", "crowdstrike", "aporia", "lasso", "grayswan"} {
		t.Run(provider, func(t *testing.T) {
			g := New("parse-fixture", map[string]interface{}{"provider": provider})
			if g.adapter == nil {
				t.Fatal("missing test adapter")
			}
			r := g.adapter.parseResponse([]byte(`{`))
			if r == nil || r.Pass || !r.OperationalError {
				t.Fatalf("parse error is repairable content: %+v", r)
			}
		})
	}
	for _, tc := range []struct{ provider, body string }{{"dynamoai", `{"error":"service unavailable"}`}, {"ibm_ai", `{"choices":[]}`}} {
		r := New("error-fixture", map[string]interface{}{"provider": tc.provider}).adapter.parseResponse([]byte(tc.body))
		if r.Pass || !r.OperationalError {
			t.Fatalf("provider=%s result=%+v", tc.provider, r)
		}
	}
}

func TestAdapterExplicitPassFallbacksRemainPass(t *testing.T) {
	for _, tc := range []struct{ provider, body string }{{"bedrock_guardrails", `{"action":"unknown"}`}, {"llama_guard", `{"choices":[]}`}, {"aporia", `{}`}} {
		r := New("pass-fixture", map[string]interface{}{"provider": tc.provider}).adapter.parseResponse([]byte(tc.body))
		if !r.Pass || r.OperationalError {
			t.Fatalf("provider=%s result=%+v", tc.provider, r)
		}
	}
}
