package toolview

import (
	"context"
	"strings"
	"testing"
)

var identityNames = []struct {
	label, value string
	refuse       bool
}{
	{"kept", `"keep"`, false},
	{"undeclared", `"missing"`, true},
	{"case-mismatch", `"Keep"`, true},
	{"removed", `"drop"`, true},
	{"empty", `""`, true},
	{"null", `null`, true},
	{"number", `7`, true},
	{"array", `["keep"]`, true},
}

func TestNativeStripExactFunctionSelectors(t *testing.T) {
	for _, tc := range identityNames {
		for _, messages := range []bool{false, true} {
			family, typ := "responses", "function"
			if messages {
				family, typ = "messages", "tool"
			}
			t.Run(family+"/"+tc.label, func(t *testing.T) {
				assertNonToolStrip(t, messages, `"tool_choice":{"type":"`+typ+`","name":`+tc.value+`}`, tc.refuse)
			})
		}
		for _, config := range []string{"toolConfig", "tool_config"} {
			for _, calling := range []string{"functionCallingConfig", "function_calling_config"} {
				for _, names := range []string{"allowedFunctionNames", "allowed_function_names"} {
					t.Run(config+"/"+calling+"/"+names+"/"+tc.label, func(t *testing.T) {
						body := `{"tools":[{"functionDeclarations":[{"name":"keep"},{"name":"drop"}]}],"` + config + `":{"` + calling + `":{"mode":"ANY","` + names + `":[` + tc.value + `]}}}`
						r := Gemini([]byte(body), "model", false)
						compatReference(t, r, tc.refuse)
						if !tc.refuse && string(r.Body()) != strings.Replace(body, `,{"name":"drop"}`, "", 1) {
							t.Fatal("kept selector changed")
						}
					})
				}
			}
		}
	}
}

func TestNativeStripExactFunctionHistory(t *testing.T) {
	for _, tc := range identityNames {
		t.Run("responses/"+tc.label, func(t *testing.T) {
			assertNonToolStrip(t, false, `"input":[{"type":"function_call","name":`+tc.value+`,"call_id":"c1","arguments":"{}"}]`, tc.refuse)
		})
		t.Run("messages/"+tc.label, func(t *testing.T) {
			assertNonToolStrip(t, true, `"messages":[{"role":"assistant","content":[{"type":"tool_use","name":`+tc.value+`,"id":"c1","input":{}}]}]`, tc.refuse)
		})
		for _, field := range []string{"functionCall", "function_call", "functionResponse", "function_response"} {
			t.Run(field+"/"+tc.label, func(t *testing.T) {
				body := `{"tools":[{"functionDeclarations":[{"name":"keep"},{"name":"drop"}]}],"contents":{"parts":{"` + field + `":{"name":` + tc.value + `,"args":{"text":"drop"},"response":{"text":"drop"}}}}}`
				r := Gemini([]byte(body), "model", false)
				compatReference(t, r, tc.refuse)
				if !tc.refuse && string(r.Body()) != strings.Replace(body, `,{"name":"drop"}`, "", 1) {
					t.Fatal("kept history bytes changed")
				}
			})
		}
	}
}

func TestNativeStripBuiltinAndFunctionIdentity(t *testing.T) {
	fields := []struct{ name, json string }{
		{"selector", `"toolConfig":{"functionCallingConfig":{"mode":"ANY","allowedFunctionNames":["googleSearch"]}}`},
		{"snake-selector", `"tool_config":{"function_calling_config":{"mode":"ANY","allowed_function_names":["googleSearch"]}}`},
		{"call", `"contents":{"parts":{"functionCall":{"name":"googleSearch","args":{"q":"drop"}}}}`},
		{"snake-call", `"contents":{"parts":{"function_call":{"name":"googleSearch","args":{"q":"drop"}}}}`},
		{"response", `"contents":{"parts":{"functionResponse":{"name":"googleSearch","response":{"text":"drop"}}}}`},
		{"snake-response", `"contents":{"parts":{"function_response":{"name":"googleSearch","response":{"text":"drop"}}}}`},
	}
	for _, field := range fields {
		for _, builtin := range []string{"googleSearch", "google_search"} {
			t.Run(field.name+"/"+builtin, func(t *testing.T) {
				body := `{"tools":[{"functionDeclarations":[{"name":"googleSearch"}],"` + builtin + `":{}}],` + field.json + `,"opaque":{"name":"googleSearch","n":1e+03}}`
				r := Gemini([]byte(body), "model", false)
				s, err := r.ToolSnapshot(context.Background())
				if err != nil || s.Len() != 2 {
					t.Fatal("separate function/builtin identities not described")
				}
				fn, _ := s.Tool(0)
				bi, _ := s.Tool(1)
				if fn.Kind() != Function || fn.PolicyName() != "googleSearch" || bi.Kind() != Builtin || bi.PolicyName() != "google_search" {
					t.Fatal("declaration identity conflated")
				}
				generation := r.Generation()
				if err := r.ApplyToolFilter(context.Background(), s, []ToolID{fn.ID()}); err != nil {
					t.Fatal("removed builtin alias invalidated a retained Function")
				}
				want := `{"tools":[{"functionDeclarations":[{"name":"googleSearch"}]}],` + field.json + `,"opaque":{"name":"googleSearch","n":1e+03}}`
				if string(r.Body()) != want || r.Generation() != generation+1 {
					t.Fatal("builtin removal changed function reference bytes")
				}
				// Keeping only the builtin must not resolve a Function reference.
				reverse := Gemini([]byte(body), "model", false)
				rs, _ := reverse.ToolSnapshot(context.Background())
				rg := reverse.Generation()
				if reverse.ApplyToolFilter(context.Background(), rs, []ToolID{bi.ID()}) == nil || string(reverse.Body()) != body || reverse.Generation() != rg {
					t.Fatal("builtin substituted for removed Function identity")
				}
			})
		}
		t.Run(field.name+"/builtin-only", func(t *testing.T) {
			body := `{"tools":[{"googleSearch":{},"functionDeclarations":[{"name":"drop"}]}],` + field.json + `}`
			compatReference(t, Gemini([]byte(body), "model", false), true)
		})
	}
}
