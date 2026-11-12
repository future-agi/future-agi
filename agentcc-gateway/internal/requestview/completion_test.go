package requestview

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// completion is a /v1/completions request with prompt, and its prompt view.
func completion(prompt string) (*models.CompletionRequest, *CompletionPrompt) {
	req := &models.CompletionRequest{Model: "m", Prompt: json.RawMessage(prompt)}
	return req, Completion(req, req.ToChatRequest())
}

// A check's rewrite of the chat request built from the prompt is written
// into the prompt the provider gets: a string, or each prompt of a list.
func TestCompletion_WritesTheRewriteIntoThePrompt(t *testing.T) {
	for _, tc := range []struct{ prompt, rewritten, want string }{
		{`"my ssn 1"`, "my ssn ***", `"my ssn ***"`},
		{`["hi","my ssn 1"]`, "hi\nmy ssn ***", `["hi","my ssn ***"]`},
		{`["a\nb 1","c"]`, "a\nb ***\nc", `["a\nb ***","c"]`},
	} {
		req, p := completion(tc.prompt)
		chat, _ := p.ChatRequest(context.Background())
		chat.Messages[0].Content, _ = json.Marshal(tc.rewritten)
		if err := p.ApplyChecks(); err != nil {
			t.Fatalf("%s: %v", tc.prompt, err)
		}
		if string(req.Prompt) != tc.want {
			t.Errorf("prompt %s became %s, want %s", tc.prompt, req.Prompt, tc.want)
		}
	}
}

// No rewrite leaves the prompt as sent; a rewrite that cannot be written is
// an error, and the prompt stays as it was.
func TestCompletion_RefusesWhatItCannotWrite(t *testing.T) {
	req, p := completion(`["a","b"]`)
	if err := p.ApplyChecks(); err != nil || string(req.Prompt) != `["a","b"]` {
		t.Errorf("with no rewrite: %v, prompt %s", err, req.Prompt)
	}
	for _, tc := range []struct{ prompt, rewritten, why string }{
		{`["call 555-123","4567 now"]`, "call *** now", "across two prompts"},
		{`[1,2,3]`, "***", "token ids"},
	} {
		req, p := completion(tc.prompt)
		// The chat request the checks read (rc.Request); ChatRequest gives
		// none for a prompt of token ids, which cannot be read.
		chat := p.chat
		chat.Messages[0].Content, _ = json.Marshal(tc.rewritten)
		err := p.ApplyChecks()
		if err == nil || !strings.Contains(err.Error(), tc.why) || string(req.Prompt) != tc.prompt {
			t.Errorf("%s: error %v, prompt %s; want an error that says %q and the prompt as sent", tc.prompt, err, req.Prompt, tc.why)
		}
	}
}

// A prompt the provider decodes rather than reads as text (token ids, a
// list of them, or a list that is not all strings) cannot be read: the chat
// request built from it holds its JSON text, not what the model reads.
// ChatRequest gives no request and says why, so the guardrail plugin
// refuses it when a check would block or rewrite it. A string, a list of
// strings and no prompt are read.
func TestCompletion_ATokenIDPromptCannotBeRead(t *testing.T) {
	for _, prompt := range []string{`[5618,1052,856]`, `[[1,2],[3]]`, `["a",1]`, `{"x":1}`, `7`} {
		_, p := completion(prompt)
		if req, reason := p.ChatRequest(context.Background()); req != nil || !strings.Contains(reason, "cannot read") || !strings.Contains(reason, "prompt") {
			t.Errorf("%s: request %v, reason %q; want none, and a reason about the prompt", prompt, req, reason)
		}
	}
	for _, prompt := range []string{`"hello"`, `["a","b"]`, `null`, ``} {
		_, p := completion(prompt)
		if req, reason := p.ChatRequest(context.Background()); req == nil || reason != "" {
			t.Errorf("%s: request %v, reason %q; want it read", prompt, req, reason)
		}
	}
}
