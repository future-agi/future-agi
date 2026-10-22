package gemini

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func asAPIError(err error, target **models.APIError) bool { return errors.As(err, target) }

// requestSentFor returns the body the provider put on the wire for a caller's
// request, decoded from JSON so the whole path is exercised: the tools entry
// is parsed the way a real client writes it, not built as a struct.
func requestSentFor(t *testing.T, body string) map[string]json.RawMessage {
	t.Helper()

	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(body), &req); err != nil {
		t.Fatalf("reading the caller's request: %v", err)
	}

	var sent map[string]json.RawMessage
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&sent); err != nil {
			t.Errorf("reading the sent body: %v", err)
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	if _, err := p.ChatCompletion(context.Background(), &req); err != nil {
		t.Fatalf("ChatCompletion: %v", err)
	}
	return sent
}

// toolsEntries decodes the tools array the provider sent.
func toolsEntries(t *testing.T, sent map[string]json.RawMessage) []map[string]json.RawMessage {
	t.Helper()
	raw, ok := sent["tools"]
	if !ok {
		t.Fatalf("no tools were sent at all; body was %v", keysOf(sent))
	}
	var entries []map[string]json.RawMessage
	if err := json.Unmarshal(raw, &entries); err != nil {
		t.Fatalf("reading tools: %v", err)
	}
	return entries
}

func keysOf(m map[string]json.RawMessage) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

// The tool Gemini runs itself is written as a bare {"google_search": {}}, with
// no type to declare. It used to be skipped for not being a function, leaving
// a request with no tools at all: the model answered without being able to
// search, and the caller was billed for a reply that could not do the job.
func TestChatCompletion_GoogleSearchReachesTheProvider(t *testing.T) {
	sent := requestSentFor(t, `{
		"model": "gemini-3.6-flash",
		"messages": [{"role":"user","content":"search the web"}],
		"tools": [{"google_search": {}}]
	}`)

	entries := toolsEntries(t, sent)
	if len(entries) != 1 {
		t.Fatalf("tools entries = %d, want 1: %s", len(entries), sent["tools"])
	}
	if _, ok := entries[0]["googleSearch"]; !ok {
		t.Errorf("googleSearch is missing: %s", sent["tools"])
	}
}

func TestChatCompletion_ServerToolSpellingsAndConfig(t *testing.T) {
	tests := []struct {
		name  string
		tools string
		want  string // the Gemini field that must be set
		body  string // and the config it must carry
	}{
		{"snake_case", `[{"google_search": {}}]`, "googleSearch", `{}`},
		{"camelCase", `[{"googleSearch": {}}]`, "googleSearch", `{}`},
		{"url_context", `[{"url_context": {}}]`, "urlContext", `{}`},
		{"code_execution", `[{"code_execution": {}}]`, "codeExecution", `{}`},
		{
			"a retrieval config is forwarded as written",
			`[{"google_search_retrieval": {"dynamicRetrievalConfig": {"mode": "MODE_DYNAMIC"}}}]`,
			"googleSearchRetrieval",
			`{"dynamicRetrievalConfig": {"mode": "MODE_DYNAMIC"}}`,
		},
		{"named in type", `[{"type": "google_search"}]`, "googleSearch", `{}`},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			sent := requestSentFor(t, `{
				"model": "gemini-3.6-flash",
				"messages": [{"role":"user","content":"hi"}],
				"tools": `+tc.tools+`
			}`)

			entries := toolsEntries(t, sent)
			if len(entries) != 1 {
				t.Fatalf("tools entries = %d, want 1: %s", len(entries), sent["tools"])
			}
			got, ok := entries[0][tc.want]
			if !ok {
				t.Fatalf("%s is missing: %s", tc.want, sent["tools"])
			}
			var wantAny, gotAny any
			if err := json.Unmarshal([]byte(tc.body), &wantAny); err != nil {
				t.Fatalf("test config: %v", err)
			}
			if err := json.Unmarshal(got, &gotAny); err != nil {
				t.Fatalf("sent config: %v", err)
			}
			if !jsonEqual(wantAny, gotAny) {
				t.Errorf("%s = %s, want %s", tc.want, got, tc.body)
			}
		})
	}
}

func jsonEqual(a, b any) bool {
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return string(x) == string(y)
}

// A caller's own functions and a tool Gemini runs itself travel together, each
// in its own entry of the tools array.
func TestChatCompletion_FunctionsAndAServerToolTogether(t *testing.T) {
	sent := requestSentFor(t, `{
		"model": "gemini-3.6-flash",
		"messages": [{"role":"user","content":"hi"}],
		"tools": [
			{"type":"function","function":{"name":"get_weather","description":"w"}},
			{"google_search": {}}
		]
	}`)

	entries := toolsEntries(t, sent)
	if len(entries) != 2 {
		t.Fatalf("tools entries = %d, want 2: %s", len(entries), sent["tools"])
	}
	if _, ok := entries[0]["functionDeclarations"]; !ok {
		t.Errorf("the first entry holds no functionDeclarations: %s", sent["tools"])
	}
	if _, ok := entries[1]["googleSearch"]; !ok {
		t.Errorf("the second entry holds no googleSearch: %s", sent["tools"])
	}
}

// A tool the provider cannot serve is refused rather than dropped. Dropping it
// is how a caller pays for an answer that could not use the capability it
// asked for.
func TestChatCompletion_UnsupportedToolIsRefused(t *testing.T) {
	tests := []struct {
		name  string
		tools string
	}{
		{"an unknown bare key", `[{"web_search": {}}]`},
		{"an anthropic server tool", `[{"type":"web_search_20250305","max_uses":5}]`},
		{"a legacy assistants type that also names a function", `[{"type":"retrieval","function":{"name":"r"}}]`},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var req models.ChatCompletionRequest
			body := `{"model":"gemini-3.6-flash","messages":[{"role":"user","content":"hi"}],"tools":` + tc.tools + `}`
			if err := json.Unmarshal([]byte(body), &req); err != nil {
				t.Fatalf("reading the request: %v", err)
			}

			reached := false
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				reached = true
				w.Write([]byte(`{"candidates":[]}`))
			}))
			defer server.Close()

			p := newTestProvider(t, server.URL)
			_, err := p.ChatCompletion(context.Background(), &req)

			if err == nil {
				t.Fatal("the request was accepted, want a refusal")
			}
			var apiErr *models.APIError
			if !asAPIError(err, &apiErr) {
				t.Fatalf("error %v is not an *models.APIError", err)
			}
			if apiErr.Status != http.StatusBadRequest {
				t.Errorf("status = %d, want %d", apiErr.Status, http.StatusBadRequest)
			}
			if apiErr.Code != "unsupported_tool" {
				t.Errorf("code = %q, want %q", apiErr.Code, "unsupported_tool")
			}
			if reached {
				t.Error("the upstream was called for a request the gateway cannot serve")
			}
		})
	}
}

// Grounding sources are what googleSearch returns beside the text. Without
// them a grounded answer arrives with no way to see where it came from, which
// is the reason for asking in the first place.
func TestTranslateResponse_GroundingSourcesBecomeAnnotations(t *testing.T) {
	const body = `{
		"candidates": [{
			"content": {"parts": [{"text": "Cities flooded."}]},
			"finishReason": "STOP",
			"groundingMetadata": {
				"groundingChunks": [
					{"web": {"uri": "https://news.example/a", "title": "News A"}},
					{"web": {"uri": "https://news.example/b", "title": "News B"}}
				]
			}
		}]
	}`

	var resp geminiResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}

	out := translateResponse(&resp, "gemini-3.6-flash")
	if len(out.Choices) != 1 {
		t.Fatalf("choices = %d, want 1", len(out.Choices))
	}
	annotations := out.Choices[0].Message.Annotations
	if len(annotations) == 0 {
		t.Fatal("the grounding sources were dropped")
	}

	var got []struct {
		Type        string `json:"type"`
		URLCitation struct {
			URL        string `json:"url"`
			Title      string `json:"title"`
			StartIndex int    `json:"start_index"`
			EndIndex   int    `json:"end_index"`
		} `json:"url_citation"`
	}
	if err := json.Unmarshal(annotations, &got); err != nil {
		t.Fatalf("reading annotations: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("annotations = %d, want 2: %s", len(got), annotations)
	}
	if got[0].Type != "url_citation" {
		t.Errorf("type = %q, want %q", got[0].Type, "url_citation")
	}
	if got[0].URLCitation.URL != "https://news.example/a" || got[0].URLCitation.Title != "News A" {
		t.Errorf("first source = %+v", got[0].URLCitation)
	}
	if want := len("Cities flooded."); got[0].URLCitation.EndIndex != want {
		t.Errorf("end_index = %d, want %d (the text extent)", got[0].URLCitation.EndIndex, want)
	}
}

// A response with no grounding must carry no annotations key at all, so an
// ordinary reply is unchanged.
func TestTranslateResponse_NoGroundingLeavesNoAnnotations(t *testing.T) {
	const body = `{"candidates":[{"content":{"parts":[{"text":"plain"}]},"finishReason":"STOP"}]}`

	var resp geminiResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}

	out := translateResponse(&resp, "gemini-3.6-flash")
	if got := out.Choices[0].Message.Annotations; len(got) != 0 {
		t.Errorf("annotations = %s, want none", got)
	}
	encoded, err := json.Marshal(out)
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	if strings.Contains(string(encoded), "annotations") {
		t.Errorf("an ordinary reply carries an annotations key: %s", encoded)
	}
}

// A tool meant for another provider must not fail a request that still has
// tools Gemini can serve. A model alias or a failover group can span
// providers, and 400 is not a failover status, so refusing here would end the
// request outright rather than letting the next provider try.
func TestChatCompletion_AnUnservableToolBesideAFunctionIsKept(t *testing.T) {
	sent := requestSentFor(t, `{
		"model": "gemini-3.6-flash",
		"messages": [{"role":"user","content":"hi"}],
		"tools": [
			{"type":"web_search_20250305","max_uses":5},
			{"type":"function","function":{"name":"get_weather","description":"w"}}
		]
	}`)

	entries := toolsEntries(t, sent)
	if len(entries) != 1 {
		t.Fatalf("tools entries = %d, want 1: %s", len(entries), sent["tools"])
	}
	var decls []map[string]json.RawMessage
	if err := json.Unmarshal(entries[0]["functionDeclarations"], &decls); err != nil {
		t.Fatalf("reading functionDeclarations: %v", err)
	}
	if len(decls) != 1 || string(decls[0]["name"]) != `"get_weather"` {
		t.Errorf("function declarations = %s, want only get_weather", entries[0]["functionDeclarations"])
	}
}

// The same holds when the surviving tool is one Gemini runs itself.
func TestChatCompletion_AnUnservableToolBesideAServerToolIsKept(t *testing.T) {
	sent := requestSentFor(t, `{
		"model": "gemini-3.6-flash",
		"messages": [{"role":"user","content":"hi"}],
		"tools": [{"web_search": {}}, {"google_search": {}}]
	}`)

	entries := toolsEntries(t, sent)
	if len(entries) != 1 {
		t.Fatalf("tools entries = %d, want 1: %s", len(entries), sent["tools"])
	}
	if _, ok := entries[0]["googleSearch"]; !ok {
		t.Errorf("googleSearch is missing: %s", sent["tools"])
	}
}

// A function entry that names no function is malformed. It used to reach the
// provider as an empty function declaration; it now carries the caller's own
// bytes, so the upstream sees what was actually written and answers for it.
func TestTool_AFunctionWithNoNameKeepsTheCallersBytes(t *testing.T) {
	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(`{"model":"m","tools":[{"type":"function"}]}`), &req); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if len(req.Tools) != 1 {
		t.Fatalf("tools = %d, want 1", len(req.Tools))
	}

	out, err := json.Marshal(req.Tools[0])
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	if string(out) != `{"type":"function"}` {
		t.Errorf("tool marshalled to %s, want the caller's own bytes", out)
	}
}
