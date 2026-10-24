package requestview

import (
	"context"
	"reflect"
	"strings"
	"testing"
)

// A generateContent body reads as the chat request the gateway's translator
// gives such a request, with every field under either name Google's parser
// takes.
func TestGemini_ReadsTheChatShape(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
		want []string
	}{
		{"a user turn",
			`{"contents":[{"role":"user","parts":[{"text":"hello"}]}]}`,
			[]string{"user: hello"}},
		{"a turn with no role is the user's",
			`{"contents":[{"parts":[{"text":"hello"}]}]}`,
			[]string{"user: hello"}},
		{"systemInstruction",
			`{"systemInstruction":{"parts":[{"text":"be brief"},{"text":"be kind"}]},"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`,
			[]string{"system: be brief\nbe kind", "user: hi"}},
		{"system_instruction",
			`{"system_instruction":{"parts":[{"text":"be brief"}]},"contents":[]}`,
			[]string{"system: be brief"}},
		{"a model turn is the assistant's; a thought is not read",
			`{"contents":[{"role":"model","parts":[{"text":"thinking hard","thought":true},{"text":"answer"}]}]}`,
			[]string{"assistant: answer"}},
		{"function calls and responses, camelCase",
			`{"contents":[{"role":"model","parts":[{"functionCall":{"name":"lookup","args":{"q":1}}}]},{"role":"user","parts":[{"functionResponse":{"name":"lookup","response":{"r":"v"}}},{"text":"next"}]}]}`,
			[]string{`assistant:  [call call_0_0 lookup {"q":1}]`, `tool: {"r":"v"}`, "user: next"}},
		{"function calls and responses, snake_case",
			`{"contents":[{"role":"model","parts":[{"function_call":{"id":"c9","name":"lookup","args":{}}}]},{"role":"user","parts":[{"function_response":{"id":"c9","name":"lookup","response":{"r":"v"}}}]}]}`,
			[]string{`assistant:  [call c9 lookup {}]`, `tool: {"r":"v"} (for c9)`}},
		{"inline image data, both names",
			`{"contents":[{"role":"user","parts":[{"text":"see"},{"inlineData":{"mimeType":"image/png","data":"AAAA"}},{"inline_data":{"mime_type":"image/jpeg","data":"BBBB"}}]}]}`,
			[]string{`user: [{"text":"see","type":"text"},{"image_url":{"url":"data:image/png;base64,AAAA"},"type":"image_url"},{"image_url":{"url":"data:image/jpeg;base64,BBBB"},"type":"image_url"}]`}},
		{"inline data that is not an image is not read",
			`{"contents":[{"role":"user","parts":[{"inlineData":{"mimeType":"application/pdf","data":"JVBER"}},{"text":"q"}]}]}`,
			[]string{"user: q"}},
		{"a code execution result",
			`{"contents":[{"role":"model","parts":[{"executableCode":{"language":"PYTHON","code":"print(1)"}},{"codeExecutionResult":{"outcome":"OUTCOME_OK","output":"1"}}]}]}`,
			[]string{"tool: 1"}},
		// Google decodes a function response before the model reads it, so
		// the checks read it decoded: escapes in strings and keys read as the
		// characters they stand for, with no white space, and the keys'
		// order and the numbers as sent.
		{"a function response is read as Google decodes it",
			`{"contents":[{"role":"user","parts":[{"functionResponse":{"name":"lookup","response":{ "r" : "Jos\u00e9 \u0031\u0032\u0033-45-6789", "n" : 1.50, "k\u0065y" : [true, null, {"z":"a\/b"}] }}}]}]}`,
			[]string{`tool: {"r":"José 123-45-6789","n":1.50,"key":[true,null,{"z":"a/b"}]}`}},
		// Google's parser takes one value for a repeated field, and its own
		// examples send contents and parts that way.
		{"contents and parts given as one object",
			`{"contents":{"role":"user","parts":{"text":"hello"}}}`,
			[]string{"user: hello"}},
		{"contents as one object, its parts a list",
			`{"contents":{"role":"user","parts":[{"text":"hello"},{"text":"again"}]}}`,
			[]string{"user: hello\nagain"}},
		{"a system instruction's parts as one object",
			`{"system_instruction":{"parts":{"text":"be brief"}},"contents":[{"parts":{"text":"hi"}}]}`,
			[]string{"system: be brief", "user: hi"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := shape(t, readOK(t, Gemini([]byte(tc.body), "gemini-x", false)))
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("chat shape\n got %q\nwant %q", got, tc.want)
			}
		})
	}
}

// The function declarations are the tools, under either name; the model and
// the stream flag are the handler's, and the generation config's settings
// are kept.
func TestGemini_ReadsToolsAndSettings(t *testing.T) {
	req := readOK(t, Gemini([]byte(`{"contents":[],
		"generation_config":{"max_output_tokens":7,"temperature":0.2},
		"tools":[{"functionDeclarations":[{"name":"a","description":"A"}]},{"function_declarations":[{"name":"b"}]},{"googleSearch":{}}]}`),
		"gemini-x", true))
	if req.Model != "gemini-x" || !req.Stream || req.MaxTokens == nil || *req.MaxTokens != 7 || req.Temperature == nil {
		t.Errorf("settings model %q stream %v max_tokens %v temperature %v", req.Model, req.Stream, req.MaxTokens, req.Temperature)
	}
	var names []string
	for _, tool := range req.Tools {
		names = append(names, tool.Function.Name)
	}
	if !reflect.DeepEqual(names, []string{"a", "b", "google_search"}) {
		t.Errorf("tools %v", names)
	}
}

// A built-in tool (Google Search, code execution, URL context, ...) is a
// tool the request offers, named by its field in snake_case, under either
// spelling Google's parser takes, so a check on the tools (tool
// permissions) reads it. A tool of a kind the reader does not know is named
// the same way.
func TestGemini_NamesBuiltInTools(t *testing.T) {
	req := readOK(t, Gemini([]byte(`{"contents":[],"tools":[{"googleSearch":{}},{"google_search_retrieval":{"dynamicRetrievalConfig":{}}},
		{"codeExecution":{}},{"url_context":{}},{"computerUse":{"environment":"ENVIRONMENT_BROWSER"}},{"fileSearch":{"fileSearchStoreNames":["s"]}},
		{"googleMaps":{}},{"someFutureTool":{}},{"googleSearch":null}]}`), "gemini-x", false))
	var names []string
	for _, tool := range req.Tools {
		names = append(names, tool.Function.Name)
	}
	want := []string{"google_search", "google_search_retrieval", "code_execution", "url_context", "computer_use",
		"file_search", "google_maps", "some_future_tool"}
	if !reflect.DeepEqual(names, want) {
		t.Errorf("tools %v, want %v", names, want)
	}
}

// Function declarations, and the tools that hold them, given as one object
// are read as a list of one, as Google's parser takes them.
func TestGemini_ReadsToolsGivenAsOneObject(t *testing.T) {
	req := readOK(t, Gemini([]byte(`{"contents":[],"tools":{"functionDeclarations":{"name":"a","description":"A"}}}`), "gemini-x", false))
	if len(req.Tools) != 1 || req.Tools[0].Function.Name != "a" || req.Tools[0].Function.Description != "A" {
		t.Errorf("tools %+v, want the one declaration a", req.Tools)
	}
}

func TestGemini_UnreadableBodies(t *testing.T) {
	for _, tc := range []struct{ name, body, where string }{
		{"not an object", `"hi"`, "not a JSON object"},
		{"contents neither a list nor an object", `{"contents":"hi"}`, "contents is neither a list nor an object"},
		{"parts neither a list nor an object", `{"contents":[{"parts":"x"}]}`, "contents[0].parts is neither a list nor an object"},
		{"tools neither a list nor an object", `{"tools":"x"}`, "tools is neither a list nor an object"},
		{"a part not an object", `{"contents":[{"parts":["x"]}]}`, "contents[0].parts[0] is not an object"},
		{"a text that is not a string", `{"contents":[{"parts":[{"text":1}]}]}`, "contents[0].parts[0].text is not a string"},
		{"a system instruction of another type", `{"system_instruction":5}`, "system_instruction is not a content"},
		{"a function response that is not an object", `{"contents":[{"parts":[{"function_response":"x"}]}]}`, "contents[0].parts[0].function_response is not an object"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req, reason := Gemini([]byte(tc.body), "m", false).ChatRequest(context.Background())
			if req != nil || !strings.Contains(reason, tc.where) {
				t.Errorf("request %v, reason %q; want none, and a reason that says %q", req, reason, tc.where)
			}
		})
	}
}

// A countTokens body reads as the request whose tokens the provider counts:
// its contents, or its generateContentRequest under either name, read as a
// generateContent body; a generateContentRequest that is not an object
// cannot be read.
func TestGeminiCountTokens_ReadsTheCountedRequest(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		want       []string
	}{
		{"contents", `{"contents":[{"role":"user","parts":[{"text":"hello"}]}]}`, []string{"user: hello"}},
		{"generateContentRequest", `{"generateContentRequest":{"model":"models/g","systemInstruction":{"parts":[{"text":"be brief"}]},"contents":[{"parts":[{"text":"hello"}]}]}}`,
			[]string{"system: be brief", "user: hello"}},
		{"generate_content_request", `{"generate_content_request":{"contents":{"parts":{"text":"hello"}}}}`, []string{"user: hello"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := readOK(t, GeminiCountTokens([]byte(tc.body), "gemini-x").View)
			if got := shape(t, req); !reflect.DeepEqual(got, tc.want) || req.Model != "gemini-x" {
				t.Errorf("chat shape %q model %q, want %q gemini-x", got, req.Model, tc.want)
			}
		})
	}
	if _, reason := GeminiCountTokens([]byte(`{"generateContentRequest":"x"}`), "m").ChatRequest(context.Background()); !strings.Contains(reason, "generateContentRequest is not an object") {
		t.Errorf("reason %q, want generateContentRequest is not an object", reason)
	}
}
