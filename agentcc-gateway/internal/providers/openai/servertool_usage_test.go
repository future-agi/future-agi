package openai

import "testing"

func TestResponsesServerToolUsageCounts(t *testing.T) {
	u := ResponsesServerToolUsage([]byte(`{
	  "model": "gpt-5",
	  "tools": [{"type":"web_search"}, {"type":"file_search","vector_store_ids":["vs_1"]},
	            {"type":"code_interpreter","container":{"type":"auto","memory_limit":"16g"}}],
	  "output": [
	    {"type":"web_search_call","id":"ws_1","status":"completed"},
	    {"type":"file_search_call","id":"fs_1","queries":["q"]},
	    {"type":"file_search_call","id":"fs_2","queries":["r"]},
	    {"type":"code_interpreter_call","id":"ci_1","container_id":"cntr_a"},
	    {"type":"code_interpreter_call","id":"ci_2","container_id":"cntr_b"},
	    {"type":"message","id":"m","content":[]}
	  ],
	  "usage": {"input_tokens": 10, "output_tokens": 5}
	}`))
	if u == nil || u.WebSearchRequests != 1 || u.WebSearchPreviewRequests != 0 || u.FileSearchRequests != 2 {
		t.Fatalf("usage = %+v, want 1 search and 2 file searches", u)
	}
	if len(u.Containers) != 2 || u.Containers[0].MemoryGB != 16 || u.Containers[1].ID != "cntr_b" || u.Containers[0].Minutes != 0 {
		t.Fatalf("containers = %+v, want two 16 GB containers of unreported duration", u.Containers)
	}
}

// web_search_preview is priced apart, so its calls are counted apart; a
// response that declares both tools counts its calls as the generally
// available one.
func TestResponsesServerToolUsagePreview(t *testing.T) {
	preview := ResponsesServerToolUsage([]byte(`{"tools":[{"type":"web_search_preview_2025_03_11"}],
	  "output":[{"type":"web_search_call","id":"ws_1"},{"type":"web_search_call","id":"ws_2"}]}`))
	if preview == nil || preview.WebSearchPreviewRequests != 2 || preview.WebSearchRequests != 0 {
		t.Fatalf("preview = %+v, want 2 preview calls", preview)
	}
	both := ResponsesServerToolUsage([]byte(`{"tools":[{"type":"web_search_preview"},{"type":"web_search"}],
	  "output":[{"type":"web_search_call","id":"ws_1"}]}`))
	if both == nil || both.WebSearchRequests != 1 || both.WebSearchPreviewRequests != 0 {
		t.Fatalf("both = %+v, want 1 generally available call", both)
	}
}

// Only a body naming an OpenAI-run tool item or a container is decoded a
// second time: a function-calling turn, whose items are function_call, is
// not.
func TestServerToolMarkersSkipFunctionCalls(t *testing.T) {
	for body, want := range map[string]bool{
		`{"output":[{"type":"function_call","name":"f","call_id":"c"}]}`:     false,
		`{"output":[{"type":"custom_tool_call","name":"g"}]}`:                false,
		`{"output":[{"type":"message","content":[]}]}`:                       false,
		`{"output":[{"type":"web_search_call","id":"ws"}]}`:                  true,
		`{"output":[{"type": "file_search_call","id":"fs"}]}`:                true,
		`{"output":[{"type":"code_interpreter_call","container_id":"c_1"}]}`: true,
	} {
		if got := mayUseServerTools([]byte(body)); got != want {
			t.Errorf("mayUseServerTools(%s) = %v, want %v", body, got, want)
		}
	}
}

func TestResponsesServerToolUsageNone(t *testing.T) {
	for _, body := range []string{
		``, `not json`, `{"output":[{"type":"message"}]}`, `{"output":[{"type":"function_call","name":"f"}]}`,
	} {
		if u := ResponsesServerToolUsage([]byte(body)); u != nil {
			t.Errorf("%q = %+v, want nil", body, u)
		}
	}
	// A container named by id has no declared size: it is left to the
	// pricer to estimate.
	u := ResponsesServerToolUsage([]byte(`{"tools":[{"type":"code_interpreter","container":"cntr_x"}],
	  "output":[{"type":"code_interpreter_call","container_id":"cntr_x"}]}`))
	if u == nil || len(u.Containers) != 1 || u.Containers[0].MemoryGB != 0 {
		t.Fatalf("named container = %+v, want one of unknown size", u)
	}
}
