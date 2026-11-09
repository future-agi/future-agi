package models

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestChatImagesRoundTrip(t *testing.T) {
	for _, stream := range []bool{false, true} {
		name, field := "message", "message"
		if stream {
			name, field = "delta", "delta"
		}
		t.Run(name, func(t *testing.T) {
			images := `[{"type":"image_url","image_url":{"url":"data:image/png;base64,b25l"},"index":0},{"type":"image_url","image_url":{"url":"data:image/png;base64,dHdv"},"index":1}]`
			wire := []byte(`{"choices":[{"index":0,"` + field + `":{"role":"assistant","images":` + images + `}}]}`)
			var value any = &ChatCompletionResponse{}
			if stream {
				value = &StreamChunk{}
			}
			if err := json.Unmarshal(wire, value); err != nil {
				t.Fatal(err)
			}
			encoded, err := json.Marshal(value)
			if err != nil {
				t.Fatal(err)
			}
			var out struct {
				Choices []map[string]json.RawMessage `json:"choices"`
			}
			if err := json.Unmarshal(encoded, &out); err != nil {
				t.Fatal(err)
			}
			var body map[string]json.RawMessage
			if err := json.Unmarshal(out.Choices[0][field], &body); err != nil {
				t.Fatal(err)
			}
			var got, want any
			if err := json.Unmarshal(body["images"], &got); err != nil {
				t.Fatalf("images lost: %v", err)
			}
			if err := json.Unmarshal([]byte(images), &want); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatal("image payload changed")
			}
		})
	}
}

func TestChatImagesAbsentStayAbsent(t *testing.T) {
	for _, value := range []any{Message{Role: "assistant"}, Delta{Role: "assistant"}} {
		data, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(data, &fields); err != nil {
			t.Fatal(err)
		}
		if _, ok := fields["images"]; ok {
			t.Fatal("text-only response gained images")
		}
	}
}
