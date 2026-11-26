package gemini_test

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestNativeToolPolicySubsets(t *testing.T) {
	for _, mode := range []string{"ANY", "AUTO", "MODE_UNSPECIFIED", ""} {
		for _, names := range [][]string{{"first", "second"}, {"first"}} {
			t.Run(mode+"/"+names[len(names)-1], func(t *testing.T) {
				input := map[string]any{
					"contents":   []any{map[string]any{"role": "user", "parts": []any{map[string]string{"text": "synthetic"}}}},
					"tools":      []any{map[string]any{"functionDeclarations": []any{map[string]string{"name": "first"}, map[string]string{"name": "second"}, map[string]string{"name": "excluded"}}}},
					"toolConfig": map[string]any{"functionCallingConfig": map[string]any{"mode": mode, "allowedFunctionNames": names}},
				}
				req, drops, err := translator().RequestToCanonical(mustMarshal(t, input))
				if err != nil {
					t.Fatal(err)
				}
				if len(drops) != 0 {
					t.Fatalf("subset must not be dropped: %v", drops)
				}
				var choice struct {
					Type    string `json:"type"`
					Allowed struct {
						Mode  string `json:"mode"`
						Tools []struct {
							Type string `json:"type"`
							Name string `json:"name"`
						} `json:"tools"`
					} `json:"allowed_tools"`
				}
				if err := json.Unmarshal(req.ToolChoice, &choice); err != nil {
					t.Fatalf("restricted subset became unrestricted choice %s: %v", req.ToolChoice, err)
				}
				wantMode := "auto"
				if mode == "ANY" {
					wantMode = "required"
				}
				if choice.Type != "allowed_tools" || choice.Allowed.Mode != wantMode {
					t.Errorf("choice=%s; want restricted %s", req.ToolChoice, wantMode)
				}
				var got []string
				for _, tool := range choice.Allowed.Tools {
					if tool.Type != "function" {
						t.Errorf("unexpected tool type %q", tool.Type)
					}
					got = append(got, tool.Name)
				}
				if !reflect.DeepEqual(got, names) {
					t.Errorf("allowed names=%v want %v", got, names)
				}
				if len(req.Tools) != 3 {
					t.Errorf("declarations lost during canonicalization: %v", req.Tools)
				}
			})
		}
	}
}

func TestNativeToolPolicyInvalidSubset(t *testing.T) {
	for _, tc := range []struct{ name, config string }{
		{"unknown", `{"mode":"ANY","allowedFunctionNames":["first","missing"]}`},
		{"duplicate", `{"mode":"ANY","allowedFunctionNames":["first","first"]}`},
		{"empty-name", `{"mode":"AUTO","allowedFunctionNames":[""]}`},
		{"unknown-mode", `{"mode":"INVALID","allowedFunctionNames":["first"]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := `{"contents":[{"parts":[{"text":"synthetic"}]}],"tools":[{"functionDeclarations":[{"name":"first"}]}],"toolConfig":{"functionCallingConfig":` + tc.config + `}}`
			if req, _, err := translator().RequestToCanonical([]byte(body)); err == nil {
				t.Fatalf("invalid restriction accepted: %+v", req)
			}
		})
	}
}
