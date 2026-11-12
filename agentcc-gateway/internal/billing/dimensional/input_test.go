package dimensional

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/png"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestDimensionalInputCountsOccurrencesWithoutFetching(t *testing.T) {
	var b bytes.Buffer
	if err := png.Encode(&b, image.NewRGBA(image.Rect(0, 0, 2, 3))); err != nil {
		t.Fatal(err)
	}
	url := "data:image/png;base64," + base64.StdEncoding.EncodeToString(b.Bytes())
	content, _ := json.Marshal([]any{map[string]any{"type": "image_url", "image_url": map[string]string{"url": url}}})
	req := &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: content}, {Role: "tool", Content: content}}}
	if n, err := InputImageOccurrences(req); n != 2 || err != nil {
		t.Fatalf("n=%d err=%v", n, err)
	}
	for _, content := range []string{
		`[{"type":"image_url","image_url":{"url":"https://example.invalid/opaque.png"}}]`,
		`[{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,YWJj"}}]`,
		`[{"type":"input_audio","input_audio":{"data":"YWJj"}}]`,
		`[{"type":"text","text":"hi","nativePart":{"fileUri":"opaque"}}]`,
	} {
		req.Messages[0].Content = json.RawMessage(content)
		if _, err := InputImageOccurrences(req); err == nil {
			t.Fatalf("accepted unbounded content %s", content)
		}
	}
	req.Messages = []models.Message{{Role: "user", Content: json.RawMessage(`"text only"`)}}
	if n, err := InputImageOccurrences(req); n != 0 || err != nil {
		t.Fatalf("explicit zero: %d %v", n, err)
	}
}
