package inclusive

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestBoundRefusesResponseImagesAsInput(t *testing.T) {
	for _, raw := range []string{`[{"image_url":{"url":"data:image/png;base64,b25l"}}]`, `[]`, `null`, `{"opaque":true}`} {
		var req models.ChatCompletionRequest
		if err := json.Unmarshal([]byte(`{"model":"image","max_tokens":64,"messages":[{"role":"assistant","content":"hi","images":`+raw+`}]}`), &req); err != nil {
			t.Fatal(err)
		}
		q := &models.InclusivePrice{Model: "image", ContextLimit: 128, Prompt: "0.000002", Completion: "0.000012", Image: "0.000002"}
		if _, err := Bound(&req, q); err == nil {
			t.Error("response images accepted as unbounded request media")
		}
	}
}
