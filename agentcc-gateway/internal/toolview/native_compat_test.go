package toolview

import (
	"bytes"
	"context"
	"fmt"
	"testing"
)

func compatList(object string, array bool) string {
	if array {
		return "[" + object + "]"
	}
	return object
}

func compatKeep(t *testing.T, r *NativeRequest, names ...string) Snapshot {
	t.Helper()
	s, err := r.ToolSnapshot(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	var keep []ToolID
	for i := 0; i < s.Len(); i++ {
		d, _ := s.Tool(i)
		for _, name := range names {
			if d.PolicyName() == name {
				keep = append(keep, d.ID())
			}
		}
	}
	if err := r.ApplyToolFilter(context.Background(), s, keep); err != nil {
		t.Fatal(err)
	}
	return s
}

func TestGeminiNativeSingletonShapes(t *testing.T) {
	ctx := context.Background()
	for _, alias := range []string{"functionDeclarations", "function_declarations"} {
		for _, toolsArray := range []bool{false, true} {
			for _, declarationsArray := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/toolsArray=%t/declarationsArray=%t", alias, toolsArray, declarationsArray), func(t *testing.T) {
					tool := `{ "` + alias + `" : ` + compatList(`{ "name" : "lookup", "parameters" : {"type":"object"} }`, declarationsArray) + ` }`
					body := " \n" + `{ "contents":[], "tools" : ` + compatList(tool, toolsArray) + `, "opaque" : { "n":1e+03, "s":"a" } }` + "\n "
					r := Gemini([]byte(body), "model", false)
					s := compatKeep(t, r, "lookup")
					d, ok := s.Tool(0)
					if s.Len() != 1 || !ok || d.Kind() != Function || d.PolicyName() != "lookup" || string(r.Body()) != body {
						t.Fatal("allow changed shape, bytes or semantic count")
					}
					chat, why := r.ChatRequest(ctx)
					if why != "" || chat == nil || len(chat.Tools) != 1 {
						t.Fatal("inherited Gemini view no longer agrees")
					}
					generation := r.Generation()
					old := compatKeep(t, r)
					want := " \n" + `{ "contents":[], "opaque" : { "n":1e+03, "s":"a" } }` + "\n "
					if toolsArray {
						want = " \n" + `{ "contents":[], "tools" : [], "opaque" : { "n":1e+03, "s":"a" } }` + "\n "
					}
					after, err := r.ToolSnapshot(ctx)
					if err != nil || after.Len() != 0 || string(r.Body()) != want || r.Generation() == generation {
						t.Fatal("last declaration removal lost fidelity or left an invalid empty singleton")
					}
					if r.ApplyToolFilter(ctx, old, nil) == nil || Gemini([]byte(body), "model", false).ApplyToolFilter(ctx, old, nil) == nil {
						t.Fatal("stale or foreign snapshot accepted")
					}
				})
			}
		}
	}
}

func TestGeminiNativeSingletonGroupedStrips(t *testing.T) {
	for _, alias := range []string{"functionDeclarations", "function_declarations"} {
		for _, array := range []bool{false, true} {
			for _, declArray := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/toolsArray=%t/declarationsArray=%t", alias, array, declArray), func(t *testing.T) {
					decls := `{"name":"drop"}`
					wantGroup := `{ "googleSearch" : {} }`
					count := 2
					if declArray {
						decls = `[{ "name" : "keep", "description":"unchanged" },{"name":"drop"}]`
						wantGroup = `{ "` + alias + `" : [{ "name" : "keep", "description":"unchanged" }], "googleSearch" : {} }`
						count = 3
					}
					group := `{ "` + alias + `" : ` + decls + `, "googleSearch" : {} }`
					body := `{ "tools" : ` + compatList(group, array) + `, "opaque": "unchanged" }`
					r := Gemini([]byte(body), "model", false)
					s := compatKeep(t, r, "keep", "google_search")
					want := `{ "tools" : ` + compatList(wantGroup, array) + `, "opaque": "unchanged" }`
					if s.Len() != count || string(r.Body()) != want {
						t.Fatal("grouped sibling or kept declaration bytes changed")
					}
				})
			}
		}
	}
	r := Gemini([]byte(`{"tools":[{"functionDeclarations":{"name":"drop"}},{"function_declarations":{"name":"keep"}}]}`), "model", false)
	compatKeep(t, r, "keep")
	if string(r.Body()) != `{"tools":[{"function_declarations":{"name":"keep"}}]}` {
		t.Fatal("sibling tool group lost")
	}
}

func TestNativeStripIncidentalValues(t *testing.T) {
	for _, tc := range []struct {
		name string
		make func([]byte) *NativeRequest
		body string
		drop string
	}{
		{"responses-input", Responses, `{"input":"drop","tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}],"opaque":{"name":"drop","tool_choice":"drop","type":"tool_use"}}`, `,{"type":"function","name":"drop"}`},
		{"responses-text", Responses, `{"input":[{"role":"user","content":[{"type":"input_text","text":"drop"}]}],"instructions":"drop","tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]}`, `,{"type":"function","name":"drop"}`},
		{"messages", Anthropic, `{"messages":[{"role":"user","content":[{"type":"text","text":"drop"}]}],"system":"drop","tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}],"opaque":["drop"]}`, `,{"name":"drop","input_schema":{}}`},
		{"gemini", func(b []byte) *NativeRequest { return Gemini(b, "model", false) }, `{"contents":{"role":"user","parts":{"text":"drop"}},"tools":[{"functionDeclarations":[{"name":"keep"},{"name":"drop"}]}],"opaque":{"functionCall":{"name":"drop"}}}`, `,{"name":"drop"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := tc.make([]byte(tc.body))
			compatKeep(t, r, "keep")
			want := bytes.Replace([]byte(tc.body), []byte(tc.drop), nil, 1)
			if !bytes.Equal(r.Body(), want) {
				t.Fatal("incidental value rejected or unrelated bytes changed")
			}
		})
	}
}

func TestNativeStripReferenceLocations(t *testing.T) {
	for _, tc := range []struct {
		name, prefix, fields string
		make                 func([]byte) *NativeRequest
		refuse               bool
	}{
		{"responses-removed-selection", `"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `"tool_choice":{"type":"function","name":"drop"}`, Responses, true},
		{"responses-kept-selection", `"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `"tool_choice":{"type":"function","name":"keep"}`, Responses, false},
		{"responses-auto", `"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `"tool_choice":"auto"`, Responses, false},
		{"responses-unknown-selection", `"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `"tool_choice":{"type":"future","name":"keep"}`, Responses, true},
		{"responses-malformed-selection", `"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `"tool_choice":{"type":"function","name":["keep"]}`, Responses, true},
		{"responses-removed-call", `"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `"input":[{"type":"function_call","name":"drop","call_id":"c","arguments":"{}"}]`, Responses, true},
		{"responses-output-id", `"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `"input":[{"type":"function_call_output","call_id":"c","output":"keep"}]`, Responses, true},
		{"responses-unknown-item", `"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`, `"input":[{"type":"future_call","name":"keep"}]`, Responses, true},
		{"messages-removed-selection", `"tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}]`, `"tool_choice":{"type":"tool","name":"drop"}`, Anthropic, true},
		{"messages-kept-selection", `"tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}]`, `"tool_choice":{"type":"tool","name":"keep"}`, Anthropic, false},
		{"messages-removed-call", `"tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}]`, `"messages":[{"role":"assistant","content":[{"type":"tool_use","id":"c","name":"drop","input":{}}]}]`, Anthropic, true},
		{"messages-result-id", `"tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}]`, `"messages":[{"role":"user","content":[{"type":"tool_result","tool_use_id":"c","content":"keep"}]}]`, Anthropic, true},
	} {
		t.Run(tc.name, func(t *testing.T) { compatReference(t, tc.make([]byte("{"+tc.prefix+","+tc.fields+"}")), tc.refuse) })
	}
	for _, alias := range []string{"toolConfig", "tool_config"} {
		for _, spelling := range []struct{ config, names, call, response string }{
			{"functionCallingConfig", "allowedFunctionNames", "functionCall", "functionResponse"},
			{"function_calling_config", "allowed_function_names", "function_call", "function_response"},
		} {
			for _, name := range []string{"keep", "drop"} {
				t.Run(alias+"/"+spelling.config+"/"+name, func(t *testing.T) {
					body := `{"tools":{"functionDeclarations":[{"name":"keep"},{"name":"drop"}]},"` + alias + `":{"` + spelling.config + `":{"mode":"ANY","` + spelling.names + `":["` + name + `"]}}}`
					compatReference(t, Gemini([]byte(body), "model", false), name == "drop")
				})
				for _, field := range []string{spelling.call, spelling.response} {
					t.Run(field+"/"+name, func(t *testing.T) {
						body := `{"tools":[{"functionDeclarations":[{"name":"keep"},{"name":"drop"}]}],"contents":{"parts":{"` + field + `":{"name":"` + name + `","args":{"text":"drop"},"response":{"text":"drop"}}}}}`
						compatReference(t, Gemini([]byte(body), "model", false), name == "drop")
					})
				}
			}
		}
	}
	for _, fields := range []string{
		`"toolConfig":{"functionCallingConfig":{"allowedFunctionNames":true}}`,
		`"toolConfig":{"futureReference":{"name":"keep"}}`,
		`"toolConfig":{},"tool_config":{}`,
		`"contents":{"parts":{"functionCall":{"args":{}}}}`,
		`"contents":{"parts":{"function_call":"keep"}}`,
	} {
		compatReference(t, Gemini([]byte(`{"tools":[{"functionDeclarations":[{"name":"keep"},{"name":"drop"}]}],`+fields+`}`), "model", false), true)
	}
}

func compatReference(t *testing.T, r *NativeRequest, refuse bool) {
	t.Helper()
	ctx := context.Background()
	s, err := r.ToolSnapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	before, generation := r.Body(), r.Generation()
	d, _ := s.Tool(0)
	err = r.ApplyToolFilter(ctx, s, []ToolID{d.ID()})
	if (err != nil) != refuse {
		t.Fatalf("reference refusal = %v, want %v", err != nil, refuse)
	}
	if refuse {
		if !bytes.Equal(before, r.Body()) || r.Generation() != generation {
			t.Fatal("refused edit was not transactional")
		}
	} else {
		after, err := r.ToolSnapshot(ctx)
		if err != nil || after.Len() != 1 || r.Generation() == generation {
			t.Fatal("successful strip did not commit exactly one tool")
		}
	}
}

func TestGeminiNativeSingletonClosedShapes(t *testing.T) {
	for _, body := range []string{
		`{"tools":"lookup"}`, `{"tools":true}`, `{"tools":[[]]}`, `{"tools":{}}`,
		`{"tools":{"functionDeclarations":"lookup"}}`, `{"tools":{"functionDeclarations":true}}`,
		`{"tools":{"functionDeclarations":[{"name":"lookup"},false]}}`,
		`{"tools":{"functionDeclarations":{"name":"lookup"},"function_declarations":{"name":"other"}}}`,
		`{"tools":[{"functionDeclarations":{"name":"lookup"}},{"function_declarations":{"name":"lookup"}}]}`,
		`{"tools":{"unrecognizedBuiltin":{}}}`,
	} {
		if _, err := Gemini([]byte(body), "model", false).ToolSnapshot(context.Background()); err == nil {
			t.Fatal("unsupported or ambiguous Gemini shape accepted")
		}
	}
	if _, err := Responses([]byte(`{"tools":{"type":"function","name":"lookup"}}`)).ToolSnapshot(context.Background()); err == nil {
		t.Fatal("Gemini singleton acceptance broadened Responses")
	}
}
