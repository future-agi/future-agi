package gemini

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestR5VertexCredentialBoundary(t *testing.T) {
	key := `vertex-opaque/"\+Credential-abcdefghijklmnopqrstuvwxyz`
	for spelling, echo := range r5Spellings(key) {
		for _, mode := range []string{"http", "stream", "native_http", "native_stream"} {
			t.Run(mode+"/"+spelling, func(t *testing.T) {
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Header.Get("Authorization") != "Bearer "+key {
						t.Error("missing actual Vertex bearer witness")
					}
					b, _ := json.Marshal(map[string]any{"error": map[string]any{"message": "before " + echo + " after", "status": echo, "code": 400}})
					if mode == "stream" {
						w.Header().Set("Content-Type", "text/event-stream")
						w.Write([]byte("data: " + string(b) + "\n\n"))
						return
					}
					w.WriteHeader(400)
					w.Write(b)
				}))
				defer upstream.Close()
				p, err := New("vertex", config.ProviderConfig{BaseURL: upstream.URL, APIKey: key})
				if err != nil {
					t.Fatal(err)
				}
				defer p.Close()
				p.vertexAI = true
				ctx := context.Background()
				var text string
				switch mode {
				case "http":
					_, err = p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "unit"})
					text = r5Text(err)
				case "stream":
					ch, er := p.StreamChatCompletion(ctx, &models.ChatCompletionRequest{Model: "unit"})
					for range ch {
					}
					err = <-er
					text = r5Text(err)
				case "native_http":
					var body []byte
					var status int
					body, status, err = p.GenerateContent(ctx, "unit", []byte(`{}`), nil)
					if status != 400 || err != nil {
						t.Fatal("native status contract changed")
					}
					text = string(body)
				case "native_stream":
					_, _, err = p.StreamGenerateContent(ctx, "unit", []byte(`{}`), nil)
					text = r5Text(err)
				}
				if text == "" {
					t.Fatal("lost refusal")
				}
				if strings.Contains(text, echo) || strings.Contains(text, key) {
					t.Fatal("Vertex error credential survived")
				}
				if !strings.Contains(text, "before") || !strings.Contains(text, "after") {
					t.Fatal("lost ordinary diagnostics")
				}
			})
		}
	}
}
