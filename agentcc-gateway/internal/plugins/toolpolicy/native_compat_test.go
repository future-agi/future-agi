package toolpolicy

import (
	"bytes"
	"context"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
)

func TestNativePolicyGeminiSingletonCompatibility(t *testing.T) {
	for _, alias := range []string{"functionDeclarations", "function_declarations"} {
		for _, toolsArray := range []bool{false, true} {
			for _, declarationsArray := range []bool{false, true} {
				for _, strip := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/toolsArray=%t/declarationsArray=%t/strip=%t", alias, toolsArray, declarationsArray, strip), func(t *testing.T) {
						decl := `{"name":"lookup"}`
						if declarationsArray {
							decl = "[" + decl + "]"
						}
						tools := `{"` + alias + `":` + decl + `}`
						if toolsArray {
							tools = "[" + tools + "]"
						}
						body := `{"contents":[],"tools":` + tools + `,"opaque":{"n":1e+03}}`
						cfg := config.ToolPolicyConfig{Enabled: true, Allow: []string{"lookup"}, MaxToolsPerRequest: 1, DefaultAction: "strip"}
						if strip {
							cfg.Deny = []string{"lookup"}
						}
						p := New(cfg, nil)
						rc := models.AcquireRequestContext()
						defer rc.Release()
						rc.Request = nil
						view := toolview.Gemini([]byte(body), "model", false)
						rc.PassThrough = view
						if result := p.ProcessRequest(context.Background(), rc); result.Error != nil {
							t.Fatal(result.Error)
						}
						want := body
						if strip {
							want = `{"contents":[],"opaque":{"n":1e+03}}`
							if toolsArray {
								want = `{"contents":[],"tools":[],"opaque":{"n":1e+03}}`
							}
							if rc.Metadata["tools_filtered"] != "1" {
								t.Fatal("last declaration not counted as removed")
							}
						}
						if string(view.Body()) != want || rc.Metadata["tools_requested"] != "1" || rc.Request != nil || rc.Routing.ComputerUse.Required {
							t.Fatal("ordinary native policy changed bytes, count or carrier")
						}
						generation := view.Generation()
						if err := p.PrepareNative(context.Background(), rc); err != nil || string(view.Body()) != want || generation != view.Generation() {
							t.Fatal("prepared singleton not sealed/idempotent")
						}
					})
				}
			}
		}
	}
}

func TestNativePolicyGeminiSingletonCountAndCustomNames(t *testing.T) {
	for _, tools := range []string{
		`{"functionDeclarations":[{"name":"keep"},{"name":"drop"}]}`,
		`[{"functionDeclarations":{"name":"keep"}},{"function_declarations":{"name":"drop"}}]`,
		`{"function_declarations":{"name":"keep"},"googleSearch":{}}`,
	} {
		rc := models.AcquireRequestContext()
		rc.Request = nil
		view := toolview.Gemini([]byte(`{"tools":`+tools+`}`), "model", false)
		rc.PassThrough = view
		before := view.Body()
		p := New(config.ToolPolicyConfig{Enabled: true, MaxToolsPerRequest: 1}, nil)
		err := p.PrepareNative(context.Background(), rc)
		if err == nil || err.Code != "too_many_tools" || !bytes.Equal(before, view.Body()) {
			t.Fatal("singleton semantic count not enforced transactionally")
		}
		rc.Release()
	}
	for _, name := range []string{"codeExecution", "googleSearch", "computer_use"} {
		rc := models.AcquireRequestContext()
		rc.Request = nil
		body := `{"tools":{"function_declarations":{"name":"` + name + `"}}}`
		rc.PassThrough = toolview.Gemini([]byte(body), "model", false)
		p := New(config.ToolPolicyConfig{Enabled: true, Deny: []string{"code_execution", "google_search", "computerUse"}}, nil)
		if err := p.PrepareNative(context.Background(), rc); err != nil || string(rc.PassThrough.(models.NativeBody).Body()) != body {
			t.Fatal("custom function spelling treated as builtin authority")
		}
		rc.Release()
	}
}

func TestNativePolicyStripIncidentalTextAndRefuseSelection(t *testing.T) {
	for _, selection := range []string{"", `,"tool_choice":{"type":"function","name":"drop"}`, `,"tool_choice":{"type":"future","name":"keep"}`} {
		rc := models.AcquireRequestContext()
		rc.Request = nil
		body := `{"input":"drop","tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}],"opaque":{"text":"drop"}` + selection + `}`
		view := toolview.Responses([]byte(body))
		rc.PassThrough = view
		p := New(config.ToolPolicyConfig{Enabled: true, Deny: []string{"drop"}, DefaultAction: "strip"}, nil)
		err := p.PrepareNative(context.Background(), rc)
		if selection != "" {
			if err == nil || err.Code != "native_tool_policy_unavailable" || string(view.Body()) != body {
				t.Fatal("invalidated or unknown selection not refused transactionally")
			}
		} else {
			want := bytes.Replace([]byte(body), []byte(`,{"type":"function","name":"drop"}`), nil, 1)
			if err != nil || !bytes.Equal(view.Body(), want) || rc.Metadata["tools_requested"] != "2" || rc.Metadata["tools_filtered"] != "1" {
				t.Fatal("incidental text mistaken for reference or semantic counts changed")
			}
		}
		rc.Release()
	}
}
