package anthropic

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// encryptedContent is an opaque web search result payload, in the base64
// alphabet Anthropic uses ('+', '/', '='), which the next turn must send
// back exactly as received.
const encryptedContent = "EqgfCioIARgBIiQ3YTAwMjY1Mi1mZjM5LTQ1NGUtODgxNC1kNjNjNTk1ZWI3Y2QSDFhy+/ZpZ8m9qQp=="

// searchTurn is a Messages request continuing a turn whose web search ran:
// the assistant turn carries the server_tool_use block and its
// web_search_tool_result, with the encrypted content, as Anthropic sent
// them. It is pretty-printed, so any decode and re-encode would show.
const searchTurn = `{
  "model": "claude-sonnet-5.5",
  "max_tokens": 1024,
  "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3, "allowed_domains": ["uefa.com"]}],
  "messages": [
    {"role": "user", "content": "Who won Euro 2024?"},
    {"role": "assistant", "content": [
      {"type": "server_tool_use", "id": "srvtoolu_01", "name": "web_search", "input": {"query": "euro 2024 winner"}},
      {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_01", "content": [
        {"type": "web_search_result", "url": "https://www.uefa.com/euro2024", "title": "EURO 2024", "encrypted_content": "` + encryptedContent + `", "page_age": "2024-07-15"}
      ]},
      {"type": "text", "text": "Spain won.", "citations": [{"type": "web_search_result_location", "url": "https://www.uefa.com/euro2024", "title": "EURO 2024", "encrypted_index": "Eo8BCioIAhgBIiQ=", "cited_text": "Spain beat England"}]}
    ]},
    {"role": "user", "content": "By what score?"}
  ]
}`

// searchReply is the upstream's answer, with a new encrypted result.
const searchReply = `{
  "id": "msg_02", "type": "message", "role": "assistant", "model": "claude-sonnet-5.5",
  "content": [
    {"type": "server_tool_use", "id": "srvtoolu_02", "name": "web_search", "input": {"query": "euro 2024 final score"}},
    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_02", "content": [
      {"type": "web_search_result", "url": "https://www.uefa.com/final", "title": "Final", "encrypted_content": "` + encryptedContent + `"}
    ]},
    {"type": "text", "text": "2-1."}
  ],
  "stop_reason": "end_turn",
  "usage": {"input_tokens": 2100, "output_tokens": 12, "server_tool_use": {"web_search_requests": 1}}
}`

// Anthropic's web search results are opaque: encrypted_content must go back
// on the next turn exactly as it came. The
// native Messages pass-through sends the caller's body upstream byte for
// byte and returns the upstream's body byte for byte, so the encrypted
// content, its citations' encrypted_index and the server tool's own fields
// survive both ways. On the chat path the server tool's definition reaches
// the upstream with every field it was written with.
func TestEncryptedContentRoundTrip(t *testing.T) {
	var received []byte
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		received, _ = io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(searchReply))
	}))
	defer upstream.Close()
	p, err := New("anthropic", config.ProviderConfig{BaseURL: upstream.URL, APIKey: "test-key"})
	if err != nil {
		t.Fatal(err)
	}

	body, status, err := p.CreateAnthropicMessage(context.Background(), []byte(searchTurn), nil)
	if err != nil || status != http.StatusOK {
		t.Fatalf("CreateAnthropicMessage = %d, %v", status, err)
	}
	if !bytes.Equal(received, []byte(searchTurn)) {
		t.Fatalf("the upstream received a different body:\n got %s\nwant %s", received, searchTurn)
	}
	if !bytes.Equal(body, []byte(searchReply)) {
		t.Fatalf("the caller received a different body:\n got %s\nwant %s", body, searchReply)
	}
	if !bytes.Contains(body, []byte(`"encrypted_content": "`+encryptedContent+`"`)) {
		t.Fatal("the reply's encrypted_content is not the upstream's")
	}

	// Chat path: the server tool definition is forwarded with all its fields.
	const toolDef = `{"type":"web_search_20260209","name":"web_search","max_uses":3,"allowed_domains":["uefa.com"],"user_location":{"type":"approximate","city":"Madrid"}}`
	var tool models.Tool
	if err := json.Unmarshal([]byte(toolDef), &tool); err != nil {
		t.Fatal(err)
	}
	req := &models.ChatCompletionRequest{Model: "claude-sonnet-5.5", Tools: []models.Tool{tool},
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Who won Euro 2024?"`)}}}
	out, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatal(err)
	}
	var sent struct {
		Tools []json.RawMessage `json:"tools"`
	}
	if err := json.Unmarshal(received, &sent); err != nil || len(sent.Tools) != 1 {
		t.Fatalf("chat request tools = %s (%v)", received, err)
	}
	if !bytes.Equal(sent.Tools[0], []byte(toolDef)) {
		t.Fatalf("server tool sent as %s, want %s", sent.Tools[0], toolDef)
	}
	if out.Usage == nil || out.Usage.ServerToolUse == nil || out.Usage.ServerToolUse.WebSearchRequests != 1 {
		t.Fatalf("usage = %+v, want the one search counted", out.Usage)
	}
}
