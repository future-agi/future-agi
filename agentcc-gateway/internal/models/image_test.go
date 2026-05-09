package models

import (
	"encoding/json"
	"testing"
)

func TestImageRequestMarshalRoundTrip(t *testing.T) {
	n := 2
	req := ImageRequest{
		Model:          "dall-e-3",
		Prompt:         "A sunset over mountains",
		N:              &n,
		Size:           "1024x1024",
		Quality:        "hd",
		ResponseFormat: "url",
		Style:          "vivid",
		User:           "user-42",
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	for _, key := range []string{"model", "prompt", "n", "size", "quality", "response_format", "style", "user"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("expected key %q in marshaled JSON", key)
		}
	}
}

func TestImageRequestUnmarshal(t *testing.T) {
	input := `{
		"model": "dall-e-3",
		"prompt": "A cat wearing a hat",
		"n": 1,
		"size": "1792x1024",
		"quality": "standard",
		"response_format": "b64_json",
		"style": "natural",
		"user": "u-99"
	}`

	var req ImageRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if req.Model != "dall-e-3" {
		t.Errorf("Model = %q, want %q", req.Model, "dall-e-3")
	}
	if req.Prompt != "A cat wearing a hat" {
		t.Errorf("Prompt = %q, want %q", req.Prompt, "A cat wearing a hat")
	}
	if req.N == nil || *req.N != 1 {
		t.Errorf("N = %v, want 1", req.N)
	}
	if req.Size != "1792x1024" {
		t.Errorf("Size = %q, want %q", req.Size, "1792x1024")
	}
	if req.Quality != "standard" {
		t.Errorf("Quality = %q, want %q", req.Quality, "standard")
	}
	if req.ResponseFormat != "b64_json" {
		t.Errorf("ResponseFormat = %q, want %q", req.ResponseFormat, "b64_json")
	}
	if req.Style != "natural" {
		t.Errorf("Style = %q, want %q", req.Style, "natural")
	}
	if req.User != "u-99" {
		t.Errorf("User = %q, want %q", req.User, "u-99")
	}
}

func TestImageRequestOmitEmpty(t *testing.T) {
	req := ImageRequest{
		Model:  "dall-e-3",
		Prompt: "A dog",
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	for _, key := range []string{"n", "size", "quality", "response_format", "style", "user"} {
		if _, ok := raw[key]; ok {
			t.Errorf("key %q should be omitted when zero/nil", key)
		}
	}

	// Required fields must be present.
	for _, key := range []string{"model", "prompt"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("required key %q should be present", key)
		}
	}
}

func TestImageResponseMarshalRoundTrip(t *testing.T) {
	resp := ImageResponse{
		Created: 1700000000,
		Data: []ImageData{
			{
				URL:           "https://example.com/image.png",
				RevisedPrompt: "A beautiful sunset over the mountains",
			},
		},
	}

	data, err := json.Marshal(&resp)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var got ImageResponse
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if got.Created != 1700000000 {
		t.Errorf("Created = %d, want 1700000000", got.Created)
	}
	if len(got.Data) != 1 {
		t.Fatalf("Data length = %d, want 1", len(got.Data))
	}
	if got.Data[0].URL != "https://example.com/image.png" {
		t.Errorf("Data[0].URL = %q, want %q", got.Data[0].URL, "https://example.com/image.png")
	}
	if got.Data[0].RevisedPrompt != "A beautiful sunset over the mountains" {
		t.Errorf("Data[0].RevisedPrompt = %q, want %q", got.Data[0].RevisedPrompt, "A beautiful sunset over the mountains")
	}
}

func TestImageResponseUnmarshal(t *testing.T) {
	input := `{
		"created": 1700000001,
		"data": [
			{"url": "https://img.example.com/1.png", "revised_prompt": "revised one"},
			{"b64_json": "aGVsbG8=", "revised_prompt": "revised two"}
		]
	}`

	var resp ImageResponse
	if err := json.Unmarshal([]byte(input), &resp); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if resp.Created != 1700000001 {
		t.Errorf("Created = %d, want 1700000001", resp.Created)
	}
	if len(resp.Data) != 2 {
		t.Fatalf("Data length = %d, want 2", len(resp.Data))
	}
	if resp.Data[0].URL != "https://img.example.com/1.png" {
		t.Errorf("Data[0].URL = %q, want %q", resp.Data[0].URL, "https://img.example.com/1.png")
	}
	if resp.Data[1].B64JSON != "aGVsbG8=" {
		t.Errorf("Data[1].B64JSON = %q, want %q", resp.Data[1].B64JSON, "aGVsbG8=")
	}
}

func TestImageDataOmitEmpty(t *testing.T) {
	imgData := ImageData{
		URL: "https://example.com/img.png",
	}

	data, err := json.Marshal(&imgData)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	if _, ok := raw["b64_json"]; ok {
		t.Error("b64_json should be omitted when empty")
	}
	if _, ok := raw["revised_prompt"]; ok {
		t.Error("revised_prompt should be omitted when empty")
	}
	if _, ok := raw["url"]; !ok {
		t.Error("url should be present")
	}
}
