package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestNativePolicyNonToolContentOutgoing(t *testing.T) {
	g, calls, spy := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: true, Deny: []string{"drop"}, DefaultAction: "strip"})
	for _, tc := range []struct {
		name, path, prefix, tools, kept string
	}{
		{"reasoning", "/v1/responses", `"model":"gpt-4o","input":[{"type":"reasoning","id":"r1","summary":[{"type":"summary_text","text":"drop"}]},{"role":"user","content":"hello"}]`,
			`[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `[{"type":"function","name":"keep"}]`},
		{"search-result", "/v1/messages", `"model":"` + postBlockClaude + `","max_tokens":64,"messages":[{"role":"user","content":[{"type":"search_result","source":"https://example.invalid","title":"drop","content":[{"type":"text","text":"drop"}]}]}]`,
			`[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}]`, `[{"name":"keep","input_schema":{}}]`},
		{"container-upload", "/v1/messages", `"model":"` + postBlockClaude + `","max_tokens":64,"messages":[{"role":"user","content":[{"type":"container_upload","file_id":"f1"},{"type":"text","text":"hello"}]}]`,
			`[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}]`, `[{"name":"keep","input_schema":{}}]`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := `{` + tc.prefix + `,"tools":` + tc.tools + `,"opaque":{"name":"drop","tool_choice":"drop","n":1e+03}}`
			want := `{` + tc.prefix + `,"tools":` + tc.kept + `,"opaque":{"name":"drop","tool_choice":"drop","n":1e+03}}`
			assertNativeCompatOutgoing(t, g, calls, spy, tc.path, body, want, false)
		})
	}
}

func TestNativePolicyExactFunctionSelectorsOutgoing(t *testing.T) {
	g, calls, spy := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: true, Deny: []string{"drop"}, DefaultAction: "strip"})
	for _, tc := range []struct {
		label, name string
		refuse      bool
	}{
		{"kept", `"keep"`, false}, {"undeclared", `"missing"`, true}, {"case-mismatch", `"Keep"`, true},
		{"removed", `"drop"`, true}, {"empty", `""`, true}, {"null", `null`, true}, {"number", `7`, true}, {"array", `["keep"]`, true},
	} {
		t.Run("responses/"+tc.label, func(t *testing.T) {
			body := `{"model":"gpt-4o","input":"hello","tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}],"tool_choice":{"type":"function","name":` + tc.name + `}}`
			want := strings.Replace(body, `,{"type":"function","name":"drop"}`, "", 1)
			assertNativeCompatOutgoing(t, g, calls, spy, "/v1/responses", body, want, tc.refuse)
		})
		t.Run("messages/"+tc.label, func(t *testing.T) {
			body := `{"model":"` + postBlockClaude + `","max_tokens":64,"messages":[{"role":"user","content":"hello"}],"tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}],"tool_choice":{"type":"tool","name":` + tc.name + `}}`
			want := strings.Replace(body, `,{"name":"drop","input_schema":{}}`, "", 1)
			assertNativeCompatOutgoing(t, g, calls, spy, "/v1/messages", body, want, tc.refuse)
		})
		for _, config := range []string{"toolConfig", "tool_config"} {
			for _, calling := range []string{"functionCallingConfig", "function_calling_config"} {
				for _, names := range []string{"allowedFunctionNames", "allowed_function_names"} {
					t.Run(config+"/"+calling+"/"+names+"/"+tc.label, func(t *testing.T) {
						body := `{"contents":[{"role":"user","parts":[{"text":"hello"}]}],"tools":[{"functionDeclarations":[{"name":"keep"},{"name":"drop"}]}],"` + config + `":{"` + calling + `":{"mode":"ANY","` + names + `":[` + tc.name + `]}}}`
						want := strings.Replace(body, `,{"name":"drop"}`, "", 1)
						assertNativeCompatOutgoing(t, g, calls, spy, "/v1beta/models/"+postBlockGemini+":generateContent", body, want, tc.refuse)
					})
				}
			}
		}
	}
}

func TestNativePolicyBuiltinFunctionIdentityOutgoing(t *testing.T) {
	g, calls, spy := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: true, Deny: []string{"google_search"}, DefaultAction: "strip"})
	for _, builtin := range []string{"googleSearch", "google_search"} {
		for _, tc := range []struct{ name, fields string }{
			{"selector", `"contents":[{"role":"user","parts":[{"text":"hello"}]}],"toolConfig":{"functionCallingConfig":{"mode":"ANY","allowedFunctionNames":["googleSearch"]}}`},
			{"snake-selector", `"contents":[{"role":"user","parts":[{"text":"hello"}]}],"tool_config":{"function_calling_config":{"mode":"ANY","allowed_function_names":["googleSearch"]}}`},
			{"call", `"contents":[{"role":"model","parts":[{"functionCall":{"name":"googleSearch","args":{"q":"drop"}}}]}]`},
			{"snake-call", `"contents":[{"role":"model","parts":[{"function_call":{"name":"googleSearch","args":{"q":"drop"}}}]}]`},
			{"response", `"contents":[{"role":"user","parts":[{"functionResponse":{"name":"googleSearch","response":{"text":"drop"}}}]}]`},
			{"snake-response", `"contents":[{"role":"user","parts":[{"function_response":{"name":"googleSearch","response":{"text":"drop"}}}]}]`},
		} {
			t.Run(builtin+"/"+tc.name, func(t *testing.T) {
				body := `{"tools":[{"functionDeclarations":[{"name":"googleSearch"}],"` + builtin + `":{}}],` + tc.fields + `,"opaque":{"name":"googleSearch","n":1e+03}}`
				want := `{"tools":[{"functionDeclarations":[{"name":"googleSearch"}]}],` + tc.fields + `,"opaque":{"name":"googleSearch","n":1e+03}}`
				assertNativeCompatOutgoing(t, g, calls, spy, "/v1beta/models/"+postBlockGemini+":generateContent", body, want, false)
			})
		}
	}
}
