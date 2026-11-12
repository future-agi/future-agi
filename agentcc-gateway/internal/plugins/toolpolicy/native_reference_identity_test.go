package toolpolicy

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
)

func assertReferencePolicy(t *testing.T, view *toolview.NativeRequest, deny, want string, refuse bool) {
	t.Helper()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = nil
	rc.PassThrough = view
	before, generation := string(view.Body()), view.Generation()
	p := New(config.ToolPolicyConfig{Enabled: true, Deny: []string{deny}, DefaultAction: "strip"}, nil)
	err := p.PrepareNative(context.Background(), rc)
	if refuse {
		if err == nil || err.Code != "native_tool_policy_unavailable" || string(view.Body()) != before || view.Generation() != generation {
			t.Fatal("unresolved reference not refused transactionally")
		}
		if _, sealed := p.native.Load(rc); sealed {
			t.Fatal("failed reference validation sealed a policy state")
		}
		return
	}
	if err != nil || string(view.Body()) != want || view.Generation() != generation+1 || rc.Metadata["tools_requested"] != "2" || rc.Metadata["tools_filtered"] != "1" {
		t.Fatal("reference policy lost byte fidelity, count or single-commit semantics")
	}
	if rc.Request != nil || rc.Routing.ComputerUse.Required {
		t.Fatal("ordinary fixture gained canonical or CUA authority")
	}
	if err := p.PrepareNative(context.Background(), rc); err != nil || string(view.Body()) != want || view.Generation() != generation+1 {
		t.Fatal("repeated preparation changed the sealed body")
	}
}

func TestNativePolicyNonToolContentStrip(t *testing.T) {
	for _, tc := range []struct {
		name, fields, tools, kept string
		make                      func([]byte) *toolview.NativeRequest
	}{
		{"reasoning", `"input":[{"type":"reasoning","id":"r1","summary":[{"type":"summary_text","text":"drop"}]},{"role":"user","content":"hello"}]`,
			`[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `[{"type":"function","name":"keep"}]`, toolview.Responses},
		{"search-result", `"messages":[{"role":"user","content":[{"type":"search_result","source":"s","title":"drop","content":[{"type":"text","text":"drop"}]}]}]`,
			`[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}]`, `[{"name":"keep","input_schema":{}}]`, toolview.Anthropic},
		{"container-upload", `"messages":[{"role":"user","content":[{"type":"container_upload","file_id":"f1"}]}]`,
			`[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}]`, `[{"name":"keep","input_schema":{}}]`, toolview.Anthropic},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := `{` + tc.fields + `,"tools":` + tc.tools + `,"opaque":{"name":"drop","n":1e+03}}`
			want := `{` + tc.fields + `,"tools":` + tc.kept + `,"opaque":{"name":"drop","n":1e+03}}`
			assertReferencePolicy(t, tc.make([]byte(body)), "drop", want, false)
		})
	}
}

func TestNativePolicyTypedFunctionReferences(t *testing.T) {
	for _, tc := range []struct {
		label, name string
		refuse      bool
	}{
		{"kept", `"keep"`, false}, {"undeclared", `"missing"`, true}, {"removed", `"drop"`, true}, {"malformed", `7`, true},
	} {
		t.Run("responses/"+tc.label, func(t *testing.T) {
			body := `{"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}],"tool_choice":{"type":"function","name":` + tc.name + `}}`
			assertReferencePolicy(t, toolview.Responses([]byte(body)), "drop", strings.Replace(body, `,{"type":"function","name":"drop"}`, "", 1), tc.refuse)
		})
		t.Run("messages/"+tc.label, func(t *testing.T) {
			body := `{"tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}],"tool_choice":{"type":"tool","name":` + tc.name + `}}`
			assertReferencePolicy(t, toolview.Anthropic([]byte(body)), "drop", strings.Replace(body, `,{"name":"drop","input_schema":{}}`, "", 1), tc.refuse)
		})
	}
	for _, builtin := range []string{"googleSearch", "google_search"} {
		for _, field := range []struct{ name, json string }{
			{"selector", `"toolConfig":{"functionCallingConfig":{"mode":"ANY","allowedFunctionNames":["googleSearch"]}}`},
			{"snake-selector", `"tool_config":{"function_calling_config":{"mode":"ANY","allowed_function_names":["googleSearch"]}}`},
			{"call", `"contents":{"parts":{"functionCall":{"name":"googleSearch","args":{}}}}`},
			{"snake-call", `"contents":{"parts":{"function_call":{"name":"googleSearch","args":{}}}}`},
			{"response", `"contents":{"parts":{"functionResponse":{"name":"googleSearch","response":{}}}}`},
			{"snake-response", `"contents":{"parts":{"function_response":{"name":"googleSearch","response":{}}}}`},
		} {
			t.Run(builtin+"/"+field.name, func(t *testing.T) {
				body := `{"tools":[{"functionDeclarations":[{"name":"googleSearch"}],"` + builtin + `":{}}],` + field.json + `}`
				want := `{"tools":[{"functionDeclarations":[{"name":"googleSearch"}]}],` + field.json + `}`
				assertReferencePolicy(t, toolview.Gemini([]byte(body), "model", false), "google_search", want, false)
			})
		}
	}
}
