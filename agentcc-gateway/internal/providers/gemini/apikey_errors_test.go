package gemini

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// keyedCalls are the Gemini requests that carry the API key (chat, stream,
// embedding single and batch, speech). Each
// returns the error text the client and the log would get.
var keyedCalls = map[string]func(p *Provider) error{
	"chat": func(p *Provider) error {
		_, err := p.ChatCompletion(context.Background(), basicRequest())
		return err
	},
	"stream": func(p *Provider) error {
		chunks, errs := p.StreamChatCompletion(context.Background(), basicRequest())
		for range chunks {
		}
		return <-errs
	},
	"embedding": func(p *Provider) error {
		_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{Model: "gemini-embedding-001", Input: json.RawMessage(`"x"`)})
		return err
	},
	"batch embedding": func(p *Provider) error {
		_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{Model: "gemini-embedding-001", Input: json.RawMessage(`["x","y"]`)})
		return err
	},
	"speech": func(p *Provider) error {
		body, _, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{Model: "gemini-tts", Input: "x", Voice: "alloy"})
		if body != nil {
			body.Close()
		}
		return err
	},
}

// A transport failure (a dial failure, or a URL that does not parse) never
// puts the API key in the error text the client or the log gets. The
// upstream call is still named by its operation, so the error stays useful.
func TestGeminiTransportErrorsHideAPIKey(t *testing.T) {
	closed := httptest.NewServer(http.NotFoundHandler())
	closed.Close()
	for name, call := range keyedCalls {
		for mode, base := range map[string]string{"dial failure": closed.URL, "unparseable URL": "http://[::1"} {
			p := newTestProvider(t, closed.URL)
			p.baseURL = base
			err := call(p)
			if err == nil {
				t.Fatalf("%s, %s: no error", name, mode)
			}
			msg := err.Error()
			if strings.Contains(msg, "test-api-key") || strings.Contains(msg, "key=") {
				t.Errorf("%s, %s: error text carries the API key: %q", name, mode, msg)
			}
			if mode == "dial failure" && !strings.Contains(msg, "Post") {
				t.Errorf("%s, %s: error %q does not say which call failed", name, mode, msg)
			}
		}
	}
}
