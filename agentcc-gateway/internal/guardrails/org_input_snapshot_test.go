package guardrails

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The snapshot keeps message content byte for byte (no HTML re-escaping),
// owns its bytes, and still copies the other fields.
func TestCloneCheckInputKeepsContentBytes(t *testing.T) {
	content := json.RawMessage(`"a < b & c > d"`)
	in := &CheckInput{
		Request: &models.ChatCompletionRequest{Model: "m", Messages: []models.Message{
			{Role: "user", Content: content, Name: "n"},
			{Role: "assistant"},
		}},
		Metadata: map[string]string{"k": "v"},
	}
	out, err := cloneCheckInput(in)
	if err != nil {
		t.Fatal(err)
	}
	if string(out.Request.Messages[0].Content) != string(content) {
		t.Fatalf("content = %s, want %s", out.Request.Messages[0].Content, content)
	}
	if out.Request.Messages[1].Content != nil {
		t.Errorf("an absent content became %q", out.Request.Messages[1].Content)
	}
	if out.Request.Model != "m" || out.Request.Messages[0].Name != "n" || out.Metadata["k"] != "v" {
		t.Errorf("other fields not copied: %+v %+v", out.Request, out.Metadata)
	}
	out.Request.Messages[0].Content[1] = 'X'
	if string(in.Request.Messages[0].Content) != string(content) {
		t.Error("the snapshot shares the original's content bytes")
	}
	if string(in.Request.Messages[0].Content) != `"a < b & c > d"` || in.Request.Messages[0].Name != "n" {
		t.Error("cloning changed the input")
	}
}
