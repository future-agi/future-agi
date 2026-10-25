package reqopts

import (
	"reflect"
	"testing"
)

func TestExtraBodyHoist(t *testing.T) {
	cases := []struct {
		name      string
		fields    string
		wantCode  string
		wantParam string
		check     func(t *testing.T, res Result, extra map[string]string)
	}{
		{name: "merged into the top level", fields: `"extra_body":{"reasoning_effort":"low","temperature":0.2}`,
			check: func(t *testing.T, _ Result, extra map[string]string) {
				if extra["reasoning_effort"] != `"low"` {
					t.Errorf("reasoning_effort = %s", extra["reasoning_effort"])
				}
				if _, left := extra["extra_body"]; left {
					t.Error("extra_body still in the body")
				}
			}},
		{name: "routing fields inside are parsed", fields: `"extra_body":{"models":["b"],"plugins":[{"id":"web"}]}`,
			check: func(t *testing.T, res Result, extra map[string]string) {
				if !reflect.DeepEqual(res.Options.Models, []string{"b"}) || string(res.Options.Plugins) != `[{"id":"web"}]` {
					t.Errorf("options = %+v", res.Options)
				}
				if len(extra) != 0 {
					t.Errorf("body extras left: %v", extra)
				}
			}},
		{name: "same value at both levels", fields: `"temperature":0.5,"extra_body":{"temperature":0.50}`},
		{name: "pre-read key with its zero value", fields: `"extra_body":{"stream":false}`},
		{name: "conflicting value", fields: `"temperature":0.5,"extra_body":{"temperature":0.7}`,
			wantCode: CodeExtraBodyConflict, wantParam: "extra_body.temperature"},
		{name: "conflicting extra field", fields: `"reasoning_effort":"high","extra_body":{"reasoning_effort":"low"}`,
			wantCode: CodeExtraBodyConflict, wantParam: "extra_body.reasoning_effort"},
		{name: "pre-read key set only in extra_body", fields: `"extra_body":{"stream":true}`,
			wantCode: CodeExtraBodyConflict, wantParam: "extra_body.stream"},
		{name: "session_id only in extra_body", fields: `"extra_body":{"session_id":"s"}`,
			wantCode: CodeExtraBodyConflict, wantParam: "extra_body.session_id"},
		{name: "model differs", fields: `"extra_body":{"model":"other"}`, wantCode: CodeExtraBodyConflict, wantParam: "extra_body.model"},
		{name: "not an object", fields: `"extra_body":["x"]`, wantCode: CodeInvalidExtraBody, wantParam: "extra_body"},
		{name: "null", fields: `"extra_body":null`, wantCode: CodeInvalidExtraBody, wantParam: "extra_body"},
		{name: "nested", fields: `"extra_body":{"extra_body":{}}`, wantCode: CodeInvalidExtraBody, wantParam: "extra_body.extra_body"},
		{name: "wrong type for a typed field", fields: `"extra_body":{"temperature":"hot"}`, wantCode: CodeInvalidExtraBody, wantParam: "extra_body"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := request(t, withMessages(tc.fields))
			res, apiErr := Parse(req)
			if tc.wantCode != "" {
				if apiErr == nil || apiErr.Code != tc.wantCode || apiErr.Param == nil || *apiErr.Param != tc.wantParam {
					t.Fatalf("error = %+v, want %s param %s", apiErr, tc.wantCode, tc.wantParam)
				}
				return
			}
			if apiErr != nil {
				t.Fatalf("refused: %s %s", apiErr.Code, apiErr.Message)
			}
			if req.Model != "m" || len(req.Messages) != 1 {
				t.Fatalf("hoisting lost the request: %+v", req)
			}
			extra := map[string]string{}
			for k, v := range req.Extra {
				extra[k] = string(v)
			}
			if tc.check != nil {
				tc.check(t, res, extra)
			}
		})
	}
}

func TestExtraBodyTypedFieldBecomesTyped(t *testing.T) {
	req := request(t, withMessages(`"stream":true,"stream_options":{"include_usage":true},"extra_body":{"temperature":0.2,"max_tokens":64}`))
	if _, apiErr := Parse(req); apiErr != nil {
		t.Fatal(apiErr.Message)
	}
	if req.Temperature == nil || *req.Temperature != 0.2 || req.MaxTokens == nil || *req.MaxTokens != 64 {
		t.Fatalf("typed fields not set: temperature %v max_tokens %v", req.Temperature, req.MaxTokens)
	}
	if !req.Stream || req.StreamOptions == nil || !req.StreamOptions.IncludeUsage {
		t.Fatal("hoisting dropped the stream settings the handler had set")
	}
}
