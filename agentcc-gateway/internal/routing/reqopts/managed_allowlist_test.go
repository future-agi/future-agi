package reqopts

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCheckManaged(t *testing.T) {
	cases := []struct {
		name      string
		fields    string
		wantCode  string
		wantParam string
	}{
		{name: "allowlisted parameters", fields: `"tools":[{"type":"function","function":{"name":"f"}}],"tool_choice":"auto",
			"parallel_tool_calls":true,"response_format":{"type":"json_object"},"temperature":0.1,"top_p":0.9,"top_k":40,
			"max_tokens":10,"max_completion_tokens":10,"stop":["x"],"seed":1,"presence_penalty":0.1,"frequency_penalty":0.1,
			"repetition_penalty":1.1,"logit_bias":{"1":1},"logprobs":true,"top_logprobs":2,"user":"u","stream":true,
			"stream_options":{"include_usage":true},"reasoning":{"effort":"low"},"reasoning_effort":"low","metadata":{"k":"v"},"n":1`},
		{name: "plugins", fields: `"plugins":[{"id":"web"}]`, wantCode: CodeUnsupportedRoutingField, wantParam: "plugins"},
		{name: "empty plugins", fields: `"plugins":[]`},
		{name: "route", fields: `"route":"fallback"`, wantCode: CodeUnsupportedRoutingField, wantParam: "route"},
		{name: "transforms", fields: `"transforms":["middle-out"]`, wantCode: CodeUnsupportedRoutingField, wantParam: "transforms"},
		{name: "web_search_options", fields: `"web_search_options":{}`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "web_search_options"},
		{name: "prediction", fields: `"prediction":{"type":"content","content":"x"}`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "prediction"},
		{name: "typed modalities", fields: `"modalities":["text","audio"]`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "modalities"},
		{name: "typed audio", fields: `"audio":{"voice":"alloy","format":"mp3"}`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "audio"},
		{name: "typed service_tier", fields: `"service_tier":"flex"`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "service_tier"},
		{name: "n above 1", fields: `"n":2`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "n"},
		{name: "usage include true", fields: `"usage":{"include":true}`},
		{name: "usage include false", fields: `"usage":{"include":false}`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "usage"},
		{name: "usage with another key", fields: `"usage":{"include":true,"x":1}`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "usage"},
		{name: "usage not an object", fields: `"usage":true`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "usage"},
		{name: "hoisted from extra_body", fields: `"extra_body":{"web_search_options":{}}`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "web_search_options"},
		{name: "provider-run server tool", fields: `"tools":[{"type":"function","function":{"name":"f"}},
			{"type":"web_search_20250305","name":"web_search","max_uses":50}]`, wantCode: CodeUnsupportedFieldForManaged, wantParam: "tools[1].type"},
		{name: "server tool hoisted from extra_body", fields: `"extra_body":{"tools":[{"type":"code_execution_20250522","name":"code_execution"}]}`,
			wantCode: CodeUnsupportedFieldForManaged, wantParam: "tools[0].type"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := request(t, withMessages(tc.fields))
			res, apiErr := Parse(req)
			if apiErr != nil {
				t.Fatalf("Parse: %s", apiErr.Message)
			}
			apiErr = CheckManaged(req, &res.Options)
			if tc.wantCode == "" {
				if apiErr != nil {
					t.Fatalf("refused: %s %s", apiErr.Code, apiErr.Message)
				}
				return
			}
			if apiErr == nil || apiErr.Status != 400 || apiErr.Code != tc.wantCode || *apiErr.Param != tc.wantParam {
				t.Fatalf("error = %+v, want 400 %s param %s", apiErr, tc.wantCode, tc.wantParam)
			}
		})
	}
}

// fill sets every field of v to a non-zero value.
func fill(v reflect.Value) {
	switch v.Kind() {
	case reflect.String:
		v.SetString("x")
	case reflect.Bool:
		v.SetBool(true)
	case reflect.Int, reflect.Int64:
		v.SetInt(2)
	case reflect.Float64:
		v.SetFloat(0.5)
	case reflect.Pointer:
		v.Set(reflect.New(v.Type().Elem()))
		fill(v.Elem())
	case reflect.Slice:
		if v.Type() == reflect.TypeOf(json.RawMessage(nil)) {
			v.SetBytes([]byte(`"x"`))
			return
		}
		v.Set(reflect.MakeSlice(v.Type(), 1, 1))
	case reflect.Map:
		v.Set(reflect.MakeMap(v.Type()))
		v.SetMapIndex(reflect.New(v.Type().Key()).Elem(), reflect.New(v.Type().Elem()).Elem())
	case reflect.Struct:
		for i := 0; i < v.NumField(); i++ {
			if v.Type().Field(i).IsExported() {
				fill(v.Field(i))
			}
		}
	}
}

// TestStripForManagedCoversEveryTypedField sets every typed parameter of a
// chat request, so a typed field added to models.ChatCompletionRequest later
// fails here until the allowlist or StripForManaged covers it.
func TestStripForManagedCoversEveryTypedField(t *testing.T) {
	var req models.ChatCompletionRequest
	fill(reflect.ValueOf(&req).Elem())
	req.Extra = map[string]json.RawMessage{"web_search_options": json.RawMessage(`{}`), "reasoning_effort": json.RawMessage(`"low"`)}
	var none models.RoutingOptions
	if CheckManaged(&req, &none) == nil {
		t.Fatal("a request with every typed field set passed the managed check")
	}
	removed := StripForManaged(&req)
	if apiErr := CheckManaged(&req, &none); apiErr != nil {
		t.Fatalf("after StripForManaged removed %v, %s is still refused", removed, *apiErr.Param)
	}
	want := []string{"audio", "modalities", "n", "service_tier", "web_search_options"}
	if !reflect.DeepEqual(removed, want) {
		t.Errorf("removed %v, want %v", removed, want)
	}
	if string(req.Extra["reasoning_effort"]) != `"low"` {
		t.Error("an allowlisted extra was removed")
	}
}

// TestStripForManagedDropsProviderRunTools: on the fail-safe path a
// provider-run tool never reaches the managed upstream; function tools stay,
// and tool_choice and parallel_tool_calls go only when no tool is left.
func TestStripForManagedDropsProviderRunTools(t *testing.T) {
	server := `{"type":"web_search_20250305","name":"web_search","max_uses":50}`
	cases := []struct {
		name, tools string
		wantRemoved []string
		wantTools   int
	}{
		{name: "mixed", tools: `[` + server + `,{"type":"function","function":{"name":"f"}}]`, wantRemoved: []string{"tools[0]"}, wantTools: 1},
		{name: "server only", tools: `[` + server + `]`, wantRemoved: []string{"parallel_tool_calls", "tool_choice", "tools[0]"}},
		{name: "functions only", tools: `[{"type":"function","function":{"name":"f"}}]`, wantTools: 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := request(t, withMessages(`"tools":`+tc.tools+`,"tool_choice":"auto","parallel_tool_calls":true`))
			removed := StripForManaged(req)
			if !reflect.DeepEqual(removed, tc.wantRemoved) {
				t.Errorf("removed %v, want %v", removed, tc.wantRemoved)
			}
			if len(req.Tools) != tc.wantTools {
				t.Errorf("%d tools left, want %d", len(req.Tools), tc.wantTools)
			}
			var none models.RoutingOptions
			if apiErr := CheckManaged(req, &none); apiErr != nil {
				t.Fatalf("after StripForManaged, %s is still refused", *apiErr.Param)
			}
			b, err := json.Marshal(req)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(b), "web_search") {
				t.Errorf("outbound body still carries the server tool: %s", b)
			}
		})
	}
}
