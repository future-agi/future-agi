package mock

import (
	"errors"
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestSplitDirectives(t *testing.T) {
	got := splitDirectives(" fail:429:n=2 ,, latency:5,tools ,")
	want := []string{"fail:429:n=2", "latency:5", "tools"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("splitDirectives = %q, want %q", got, want)
	}
	if got := splitDirectives(""); got != nil {
		t.Fatalf("empty header gave %q", got)
	}
}

func TestModelShortcuts(t *testing.T) {
	cases := map[string][]string{
		"mock/fail-503":            {"fail:503"},
		"openrouter/mock/fail-429": {"fail:429"},
		"mock/slow-250":            {"latency:250"},
		"mock/tools":               {"tools"},
		"mock/empty":               {"empty"},
		"mock/filter":              {"empty", "finish:content_filter"},
		"mock/ok-1":                nil,
		"anthropic/claude":         nil,
		"gpt-4o":                   nil,
		"notmock/fail-503":         nil,
	}
	for model, want := range cases {
		if got := modelShortcut(model); !reflect.DeepEqual(got, want) {
			t.Errorf("modelShortcut(%q) = %q, want %q", model, got, want)
		}
	}
}

func TestDirectivesEditTheResponse(t *testing.T) {
	m := newTestMock(t, "")
	req := &Request{Route: RouteChat, Model: "test/model-a", JSON: map[string]any{
		"tools": []any{map[string]any{"type": "function", "function": map[string]any{"name": "lookup"}}},
	}}
	resp := m.srv.baseResponse(req)
	toks := splitDirectives("latency:7,chunk_delay:8,stream_abort_after:3,no_usage,cached_tokens:40,cache_write_tokens:30,reasoning_tokens:500,finish:length,tools,served:acme")
	if err := m.srv.applyDirectives(toks, req, resp); err != nil {
		t.Fatal(err)
	}
	if resp.LatencyMs != 7 || resp.ChunkDelayMs != 8 || resp.AbortAfter != 3 || !resp.NoUsage {
		t.Errorf("timing/usage flags = %+v", resp)
	}
	if resp.Usage.CachedTokens != 40 || resp.Usage.CacheWriteTokens != 30 || resp.Usage.ReasoningTokens != 500 {
		t.Errorf("token details = %+v", resp.Usage)
	}
	if resp.Usage.Completion != 500 {
		t.Errorf("reasoning tokens are part of the completion: completion = %d, want 500", resp.Usage.Completion)
	}
	if resp.FinishReason != "length" || resp.ServedProvider != "acme" || resp.Content != "" ||
		len(resp.ToolCalls) != 1 || resp.ToolCalls[0].Name != "lookup" {
		t.Errorf("response = %+v", resp)
	}
}

func TestInvalidDirectivesAreRefused(t *testing.T) {
	m := newTestMock(t, "")
	for _, tok := range []string{"fail:200", "fail:abc", "fail:500:n=0", "fail:500:k=1", "latency:-1", "latency:x", "usage_cost:-1", "finish", "served:"} {
		req := &Request{Route: RouteChat, Model: "test/model-a"}
		err := m.srv.applyDirectives([]string{tok}, req, m.srv.baseResponse(req))
		var de *DirectiveError
		if !errors.As(err, &de) || de.Code != "invalid_mock_directive" {
			t.Errorf("%q: err = %v, want invalid_mock_directive", tok, err)
		}
	}
}

func TestUnknownDirectiveWithoutAHookIs400(t *testing.T) {
	m := newTestMock(t, "")
	resp, b := m.chat(t, "test/model-a", "latency:1,stream_tools:2", false)
	if resp.StatusCode != http.StatusBadRequest || !strings.Contains(string(b), `"code":"unknown_mock_directive"`) {
		t.Fatalf("status %d body %s", resp.StatusCode, b)
	}
	if _, n := charges(t, m); n != 0 {
		t.Fatalf("a refused call was charged (%d charges)", n)
	}
}

func TestDirectiveHookHandlesUnknownNames(t *testing.T) {
	m := newTestMock(t, "")
	var gotName, gotArg string
	m.srv.SetDirectiveHook(func(name, arg string, req *Request, resp *Response) error {
		gotName, gotArg = name, arg
		resp.Content = "from the hook"
		return nil
	})
	_, b := m.chat(t, "test/model-a", "custom:a:b", false)
	if gotName != "custom" || gotArg != "a:b" || !strings.Contains(string(b), "from the hook") {
		t.Fatalf("hook got %q %q; body %s", gotName, gotArg, b)
	}
}

func TestFailCountsPerToken(t *testing.T) {
	m := newTestMock(t, "")
	var got []int
	for i := 0; i < 4; i++ {
		resp, _ := m.chat(t, "test/model-a", "fail:429:n=2", false)
		got = append(got, resp.StatusCode)
	}
	if want := []int{429, 429, 200, 200}; !reflect.DeepEqual(got, want) {
		t.Fatalf("fail:429:n=2 statuses = %v, want %v", got, want)
	}
	if resp, _ := m.chat(t, "test/model-a", "fail:500:n=1", false); resp.StatusCode != 500 {
		t.Fatalf("a different token has its own count: status %d", resp.StatusCode)
	}
	m.do(t, "POST", "/__reset", "")
	if resp, _ := m.chat(t, "test/model-a", "fail:429:n=2", false); resp.StatusCode != 429 {
		t.Fatalf("after /__reset the count restarts: status %d", resp.StatusCode)
	}
	for i := 0; i < 3; i++ {
		if resp, _ := m.chat(t, "test/model-a", "fail:503", false); resp.StatusCode != 503 {
			t.Fatalf("fail without n fails every call: status %d", resp.StatusCode)
		}
	}
}

func TestFailBodyIsAnOpenAIError(t *testing.T) {
	m := newTestMock(t, "")
	resp, b := m.chat(t, "mock/fail-503", "", false)
	e, _ := decode(t, b)["error"].(map[string]any)
	if resp.StatusCode != 503 || e["type"] != "server_error" || e["code"] != "503" {
		t.Fatalf("status %d body %s", resp.StatusCode, b)
	}
	if _, n := charges(t, m); n != 0 {
		t.Fatal("a failed call was charged")
	}
}

func TestLatencyDelaysTheAnswer(t *testing.T) {
	m := newTestMock(t, "")
	start := time.Now()
	m.chat(t, "mock/slow-120", "", false)
	if d := time.Since(start); d < 120*time.Millisecond {
		t.Fatalf("mock/slow-120 answered in %v", d)
	}
}
