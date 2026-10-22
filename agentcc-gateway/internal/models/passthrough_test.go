package models

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func reflectTypeOfResponse() reflect.Type { return reflect.TypeFor[ChatCompletionResponse]() }

// A provider answers with more than the OpenAI schema names. Perplexity sends
// citations and search_results beside the choices, and its own counters inside
// usage. The gateway rebuilds every response on its canonical struct, so
// anything it does not name used to be dropped before the caller saw it.
func TestChatCompletionResponse_KeepsProviderFields(t *testing.T) {
	const body = `{
		"id": "chatcmpl-1",
		"object": "chat.completion",
		"created": 1,
		"model": "sonar",
		"choices": [{"index":0,"message":{"role":"assistant","content":"answer"},"finish_reason":"stop"}],
		"usage": {"prompt_tokens":10,"completion_tokens":5,"total_tokens":15,"citation_tokens":200,"num_search_queries":2},
		"citations": ["https://a.example/one","https://b.example/two"],
		"search_results": [{"title":"One","url":"https://a.example/one"}]
	}`

	var resp ChatCompletionResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}

	// The known fields still parse.
	if resp.Model != "sonar" || len(resp.Choices) != 1 {
		t.Fatalf("model = %q, choices = %d", resp.Model, len(resp.Choices))
	}
	if resp.Usage == nil || resp.Usage.TotalTokens != 15 {
		t.Fatalf("usage = %+v", resp.Usage)
	}

	out, err := json.Marshal(resp)
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}

	var got map[string]json.RawMessage
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("re-reading the response: %v", err)
	}
	for _, key := range []string{"citations", "search_results"} {
		if _, ok := got[key]; !ok {
			t.Errorf("%q was dropped: %s", key, out)
		}
	}
	if string(got["citations"]) != `["https://a.example/one","https://b.example/two"]` {
		t.Errorf("citations = %s, want them unchanged", got["citations"])
	}

	var usage map[string]json.RawMessage
	if err := json.Unmarshal(got["usage"], &usage); err != nil {
		t.Fatalf("re-reading usage: %v", err)
	}
	for _, key := range []string{"citation_tokens", "num_search_queries"} {
		if _, ok := usage[key]; !ok {
			t.Errorf("usage.%s was dropped: %s", key, got["usage"])
		}
	}
	if string(usage["prompt_tokens"]) != "10" {
		t.Errorf("usage.prompt_tokens = %s, want 10", usage["prompt_tokens"])
	}
}

// A provider that reports its citations on the last chunk rather than in a
// whole response has to reach a streaming caller too.
func TestStreamChunk_KeepsProviderFields(t *testing.T) {
	const body = `{
		"id": "chatcmpl-1",
		"object": "chat.completion.chunk",
		"created": 1,
		"model": "sonar",
		"choices": [{"index":0,"delta":{"content":"hi"},"finish_reason":null}],
		"citations": ["https://a.example/one"]
	}`

	var chunk StreamChunk
	if err := json.Unmarshal([]byte(body), &chunk); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if len(chunk.Choices) != 1 {
		t.Fatalf("choices = %d, want 1", len(chunk.Choices))
	}

	out, err := json.Marshal(chunk)
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	if !strings.Contains(string(out), `"citations"`) {
		t.Errorf("citations were dropped from the chunk: %s", out)
	}
}

// The struct is the gateway's own answer. A captured copy of a field the
// struct names would undo whatever the gateway normalized, so a known key is
// never captured and never merged back.
func TestChatCompletionResponse_StructWinsOverTheCapturedCopy(t *testing.T) {
	const body = `{"id":"a","object":"chat.completion","created":1,"model":"upstream-name","choices":[]}`

	var resp ChatCompletionResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if _, captured := resp.Extra["model"]; captured {
		t.Error("model was captured as an unknown field")
	}

	resp.Model = "resolved-name" // what the gateway routed to
	out, err := json.Marshal(resp)
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}

	var got struct {
		Model string `json:"model"`
	}
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("re-reading: %v", err)
	}
	if got.Model != "resolved-name" {
		t.Errorf("model = %q, want the value the gateway set", got.Model)
	}
}

// A response carrying nothing extra must serialize byte-for-byte as it did
// before, so the capture cannot change an ordinary reply.
func TestChatCompletionResponse_NoExtraFieldsIsUnchanged(t *testing.T) {
	resp := ChatCompletionResponse{
		ID: "a", Object: "chat.completion", Created: 1, Model: "gpt-4o",
		Usage: &Usage{PromptTokens: 1, CompletionTokens: 2, TotalTokens: 3},
	}
	out, err := json.Marshal(resp)
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	const want = `{"id":"a","object":"chat.completion","created":1,"model":"gpt-4o","choices":null,"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}`
	if string(out) != want {
		t.Errorf("marshalled to\n  %s\nwant\n  %s", out, want)
	}
}

// The known-key set is read from the struct tags, so a field added to a struct
// cannot drift out of its own capture.
func TestKnownJSONFields_ReadsTheTags(t *testing.T) {
	known := knownJSONFields(reflectTypeOfResponse())
	for _, key := range []string{"id", "object", "created", "model", "choices", "usage", "system_fingerprint", "service_tier"} {
		if !known[key] {
			t.Errorf("%q is not in the known set", key)
		}
	}
	if known["-"] || known["Extra"] {
		t.Error("the Extra field named itself as a known key")
	}
}
