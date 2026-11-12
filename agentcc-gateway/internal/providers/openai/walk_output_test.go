package openai

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type walkOutputTransport func(*http.Request) (*http.Response, error)

func (f walkOutputTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestWalkOpenAIOutputRoundTrip(t *testing.T) {
	for _, message := range []string{
		`{"role":"assistant","content":null,"audio":{"id":"audio_fixture","expires_at":1900000000,"data":"SGVsbG8=","transcript":"Hello"}}`,
		`{"role":"assistant","content":null,"refusal":"I cannot help with that."}`,
	} {
		t.Run(message, func(t *testing.T) {
			p := newTestProvider(t, "https://fixture.invalid")
			p.httpClient.Transport = walkOutputTransport(func(r *http.Request) (*http.Response, error) {
				var request map[string]any
				if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
					t.Fatal(err)
				}
				msgs := request["messages"].([]any)
				if got := msgs[0].(map[string]any)["audio"]; !reflect.DeepEqual(got, map[string]any{"id": "audio_previous"}) {
					t.Errorf("follow-up audio ID lost: %#v", got)
				}
				if !reflect.DeepEqual(request["modalities"], []any{"text", "audio"}) {
					t.Errorf("modalities lost: %#v", request)
				}
				body := `{"id":"chat_fixture","object":"chat.completion","choices":[{"index":0,"message":` + message + `,"finish_reason":"stop"}]}`
				return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body))}, nil
			})
			var req models.ChatCompletionRequest
			if err := json.Unmarshal([]byte(`{"model":"audio-model","modalities":["text","audio"],"audio":{"voice":"alloy","format":"wav"},"messages":[{"role":"assistant","audio":{"id":"audio_previous"}}]}`), &req); err != nil {
				t.Fatal(err)
			}
			resp, err := p.ChatCompletion(context.Background(), &req)
			if err != nil {
				t.Fatal(err)
			}
			got, err := json.Marshal(resp.Choices[0].Message)
			if err != nil {
				t.Fatal(err)
			}
			walkAssertJSON(t, got, []byte(message))
		})
	}
}

func TestWalkOpenAIStreamOutputRoundTrip(t *testing.T) {
	for _, delta := range []string{
		`{"audio":{"id":"audio_fixture","expires_at":1900000000,"data":"SGVsbG8=","transcript":"Hello"}}`,
		`{"refusal":"I cannot"}`,
	} {
		t.Run(delta, func(t *testing.T) {
			p := newTestProvider(t, "https://fixture.invalid")
			p.httpClient.Transport = walkOutputTransport(func(r *http.Request) (*http.Response, error) {
				body := `data: {"id":"chat_fixture","choices":[{"index":0,"delta":` + delta + `,"finish_reason":null}]}` + "\n\ndata: [DONE]\n\n"
				return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body))}, nil
			})
			chunks, errs := p.StreamChatCompletion(context.Background(), &models.ChatCompletionRequest{Model: "audio-model"})
			n := 0
			for chunk := range chunks {
				n++
				got, err := json.Marshal(chunk.Choices[0].Delta)
				if err != nil {
					t.Fatal(err)
				}
				walkAssertJSON(t, got, []byte(delta))
			}
			for err := range errs {
				if err != nil {
					t.Fatal(err)
				}
			}
			if n != 1 {
				t.Fatalf("got %d chunks, want 1", n)
			}
		})
	}
}

func walkAssertJSON(t *testing.T, got, want []byte) {
	t.Helper()
	var g, w any
	if err := json.Unmarshal(got, &g); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(want, &w); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(g, w) {
		t.Errorf("output changed: got %s, want %s", got, want)
	}
}
