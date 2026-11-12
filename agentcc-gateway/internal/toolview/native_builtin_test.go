package toolview

import (
	"bytes"
	"context"
	"testing"
)

// Native built-ins have protocol-specific
// identities, not function names or generic capability aliases.
func TestNativeBuiltinDescriptors(t *testing.T) {
	fixtures := []struct {
		make func([]byte) *NativeRequest
		tool string
		name string
	}{
		{Responses, `{"type":"web_search"}`, "web_search"},
		{Responses, `{"type":"web_search_2025_08_26","filters":{"allowed_domains":["example.com"]}}`, "web_search_2025_08_26"},
		{Responses, `{"type":"web_search_preview","search_context_size":"low"}`, "web_search_preview"},
		{Responses, `{"type":"web_search_preview_2025_03_11"}`, "web_search_preview_2025_03_11"},
		{Responses, `{"type":"file_search","vector_store_ids":["vs_fixture"],"max_num_results":2}`, "file_search"},
		{Responses, `{"type":"code_interpreter","container":{"type":"auto","file_ids":["file_fixture"]}}`, "code_interpreter"},
		{Responses, `{"type":"image_generation","quality":"low"}`, "image_generation"},
		{Anthropic, `{"type":"web_search_20250305","name":"web_search","max_uses":2}`, "web_search_20250305"},
		{Anthropic, `{"type":"web_fetch_20250910","name":"web_fetch","allowed_domains":["example.com"]}`, "web_fetch_20250910"},
		{Anthropic, `{"type":"code_execution_20250522","name":"code_execution"}`, "code_execution_20250522"},
		{Anthropic, `{"type":"code_execution_20250825","name":"code_execution","cache_control":{"type":"ephemeral"}}`, "code_execution_20250825"},
	}
	for _, tc := range fixtures {
		t.Run(tc.name, func(t *testing.T) {
			body := []byte(" \n{ \"tools\" : [ " + tc.tool + " ], \"metadata\" : {\"n\":1e+03} }\n")
			r := tc.make(body)
			generation := r.Generation()
			snapshot, err := r.ToolSnapshot(context.Background())
			if err != nil || snapshot.Len() != 1 {
				t.Fatalf("valid built-in declaration rejected: %v", err)
			}
			d, ok := snapshot.Tool(0)
			if !ok || d.Kind() != Builtin || d.PolicyName() != tc.name {
				t.Fatalf("descriptor = %#v, want Builtin %q", d, tc.name)
			}
			if err := r.ApplyToolFilter(context.Background(), snapshot, []ToolID{d.ID()}); err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(r.Body(), body) || r.Generation() != generation {
				t.Fatal("allowed built-in payload was rewritten")
			}
		})
	}
}

func TestNativeBuiltinUnknownAndConflictingSchemas(t *testing.T) {
	fixtures := []struct {
		make func([]byte) *NativeRequest
		tool string
	}{
		{Responses, `{"type":"web_search_future"}`},
		{Responses, `{"type":"web_search_20250305","name":"web_search"}`},
		{Responses, `{"type":"google_search"}`},
		{Responses, `{"type":"computer_use_preview","display_width":1024,"display_height":768,"environment":"browser"}`},
		{Responses, `{"type":"web_search","name":"allowed_function"}`},
		{Responses, `{"type":"web_search","function":{"name":"allowed_function"}}`},
		{Responses, `{"type":"web_search","parameters":{}}`},
		{Responses, `{"type":"web_search","input_schema":{}}`},
		{Anthropic, `{"type":"web_search_20990101","name":"web_search"}`},
		{Anthropic, `{"type":"web_search","name":"web_search"}`},
		{Anthropic, `{"type":"computer_20250124","name":"computer"}`},
		{Anthropic, `{"type":"web_search_20250305"}`},
		{Anthropic, `{"type":"web_search_20250305","name":null}`},
		{Anthropic, `{"type":"web_search_20250305","name":"allowed_function"}`},
		{Anthropic, `{"type":"web_search_20250305","name":"web_search","input_schema":{}}`},
		{Anthropic, `{"type":"web_search_20250305","name":"web_search","function":{"name":"allowed_function"}}`},
		{Anthropic, `{"type":"code_execution_20250825","name":"code_execution","parameters":{}}`},
	}
	for _, tc := range fixtures {
		t.Run(tc.tool, func(t *testing.T) {
			body := []byte(`{"tools":[` + tc.tool + `]}`)
			r := tc.make(body)
			if _, err := r.ToolSnapshot(context.Background()); err == nil {
				t.Fatal("unknown, computer-use or conflicting schema accepted")
			}
			if !bytes.Equal(body, r.Body()) {
				t.Fatal("failed classification changed the body")
			}
		})
	}
}

func TestNativeBuiltinAndFunctionRemainDistinct(t *testing.T) {
	r := Responses([]byte(`{"input":"hello","tools":[{"type":"web_search"},{"type":"function","name":"web_search"}]}`))
	s, err := r.ToolSnapshot(context.Background())
	if err != nil || s.Len() != 2 {
		t.Fatalf("built-in and same-named function were conflated: %v", err)
	}
	builtin, _ := s.Tool(0)
	function, _ := s.Tool(1)
	if builtin.Kind() != Builtin || function.Kind() != Function || builtin.ID() == function.ID() {
		t.Fatal("declarations did not retain distinct kinds/IDs")
	}
	if err := r.ApplyToolFilter(context.Background(), s, []ToolID{function.ID()}); err != nil {
		t.Fatal(err)
	}
	if string(r.Body()) != `{"input":"hello","tools":[{"type":"function","name":"web_search"}]}` {
		t.Fatal("removing built-in changed the custom function")
	}
}
