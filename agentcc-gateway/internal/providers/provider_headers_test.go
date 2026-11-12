package providers

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

type headerRoute struct {
	name, path                    string
	stream, multipart, openaiOnly bool
	call                          func(context.Context, Provider) error
}

func headerRoutes() []headerRoute {
	return []headerRoute{
		{name: "chat", path: "/chat/completions", call: func(ctx context.Context, p Provider) error {
			_, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture"})
			return err
		}},
		{name: "stream-chat", path: "/chat/completions", stream: true, call: func(ctx context.Context, p Provider) error {
			chunks, errs := p.StreamChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture"})
			for range chunks {
			}
			for err := range errs {
				if err != nil {
					return err
				}
			}
			return nil
		}},
		{name: "embeddings", path: "/embeddings", call: func(ctx context.Context, p Provider) error {
			_, err := p.(EmbeddingProvider).CreateEmbedding(ctx, &models.EmbeddingRequest{Model: "fixture"})
			return err
		}},
		{name: "models", path: "/models", call: func(ctx context.Context, p Provider) error {
			_, err := p.ListModels(ctx)
			return err
		}},
		{name: "images", path: "/images/generations", call: func(ctx context.Context, p Provider) error {
			_, err := p.(ImageProvider).CreateImage(ctx, &models.ImageRequest{Model: "fixture"})
			return err
		}},
		{name: "speech", path: "/audio/speech", call: func(ctx context.Context, p Provider) error {
			body, _, err := p.(SpeechProvider).CreateSpeech(ctx, &models.SpeechRequest{Model: "fixture"})
			return closeHeaderBody(body, err)
		}},
		{name: "transcription", path: "/audio/transcriptions", multipart: true, call: func(ctx context.Context, p Provider) error {
			_, err := p.(TranscriptionProvider).CreateTranscription(ctx, &models.TranscriptionRequest{Model: "fixture", FileData: []byte("fixture")})
			return err
		}},
		{name: "translation", path: "/audio/translations", multipart: true, openaiOnly: true, call: func(ctx context.Context, p Provider) error {
			_, err := p.(TranslationProvider).CreateTranslation(ctx, &models.TranslationRequest{Model: "fixture", FileData: []byte("fixture")})
			return err
		}},
		{name: "completions", path: "/completions", openaiOnly: true, call: func(ctx context.Context, p Provider) error {
			_, err := p.(interface {
				TextCompletion(context.Context, *models.CompletionRequest) (*models.CompletionResponse, error)
			}).TextCompletion(ctx, &models.CompletionRequest{Model: "fixture"})
			return err
		}},
		{name: "ocr", path: "/ocr", openaiOnly: true, call: func(ctx context.Context, p Provider) error {
			_, err := p.(OCRProvider).OCR(ctx, &models.OCRRequest{})
			return err
		}},
		{name: "responses", path: "/responses", openaiOnly: true, call: func(ctx context.Context, p Provider) error {
			_, _, err := p.(ResponsesProvider).CreateResponse(ctx, []byte(`{}`))
			return err
		}},
		{name: "stream-responses", path: "/responses", stream: true, openaiOnly: true, call: func(ctx context.Context, p Provider) error {
			body, _, err := p.(ResponsesProvider).StreamResponse(ctx, []byte(`{}`))
			return closeHeaderBody(body, err)
		}},
		{name: "assistants", path: "/assistants", openaiOnly: true, call: func(ctx context.Context, p Provider) error {
			_, _, _, err := p.(AssistantsProvider).ProxyAssistantsRequest(ctx, "POST", "/v1/assistants", []byte(`{}`), nil)
			return err
		}},
		{name: "stream-assistants", path: "/threads/fixture/runs", stream: true, openaiOnly: true, call: func(ctx context.Context, p Provider) error {
			body, _, _, err := p.(AssistantsProvider).StreamAssistantsRequest(ctx, "POST", "/v1/threads/fixture/runs", []byte(`{}`), nil)
			return closeHeaderBody(body, err)
		}},
		{name: "vector-stores", path: "/vector_stores", openaiOnly: true, call: func(ctx context.Context, p Provider) error {
			_, _, _, err := p.(VectorStoresProvider).ProxyVectorStoresRequest(ctx, "POST", "/v1/vector_stores", []byte(`{}`), nil)
			return err
		}},
	}
}

func closeHeaderBody(body io.ReadCloser, err error) error {
	if err != nil {
		return err
	}
	defer body.Close()
	_, err = io.Copy(io.Discard, body)
	return err
}

// These are every outbound route implemented by OpenAI and Azure. Files are
// gateway-local, batches dispatch chat internally, and neither adapter implements
// a moderation endpoint. OpenRouter and Azure's v1 API use the OpenAI adapter.
func TestConfiguredProviderHeadersEveryRoute(t *testing.T) {
	for _, adapter := range []struct{ name, format, prefix, authType string }{
		{"openai", "openai", "/v1", ""},
		{"openrouter", "openai", "/api/v1", ""},
		{"azure-compatible", "openai", "/openai/v1", ""},
		{"azure", "azure", "", "api-key"},
		{"azure-bearer", "azure", "", "bearer"},
	} {
		t.Run(adapter.name, func(t *testing.T) {
			for _, route := range headerRoutes() {
				if adapter.format == "azure" && route.openaiOnly {
					continue
				}
				t.Run(route.name, func(t *testing.T) {
					t.Parallel()
					captured := make(chan *http.Request, 1)
					upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						_, _ = io.Copy(io.Discard, r.Body)
						captured <- r.Clone(context.Background())
						if route.stream {
							w.Header().Set("Content-Type", "text/event-stream")
							_, _ = io.WriteString(w, "data: [DONE]\n\n")
						} else {
							w.Header().Set("Content-Type", "application/json")
							_, _ = io.WriteString(w, `{}`)
						}
					}))
					defer upstream.Close()
					headers := map[string]string{
						"x-e2e-run": "fixture-run", "OpenAI-Organization": "fixture-org",
						"aUtHoRiZaTiOn": "config-must-not-win", "API-KEY": "config-must-not-win",
						"X-Api-Key": "config-must-not-win", "Proxy-Authorization": "config-must-not-win",
						"X-AgEnTcC-Org-ID": "spoofed", "x-agentcc-run-id": "spoofed",
						"Content-Type": "config-must-not-win", "Accept": "config-default",
						"OpenAI-Beta": "config-must-not-win",
					}
					if adapter.format == "azure" {
						headers["auth-type"] = adapter.authType
						headers["api-version"] = "2024-10-21"
					}
					p, err := createProvider(adapter.name, config.ProviderConfig{
						APIFormat: adapter.format, BaseURL: upstream.URL, APIKey: "fixture-credential",
						APIPathPrefix: &adapter.prefix, Headers: headers,
					})
					if err != nil {
						t.Fatal(err)
					}
					defer p.Close()
					// The adapter must own its snapshot, not retain a caller-mutable map.
					headers["x-e2e-run"] = "changed-after-New"
					ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
					defer cancel()
					if err := route.call(ctx, p); err != nil {
						t.Fatal(err)
					}
					var r *http.Request
					select {
					case r = <-captured:
					case <-ctx.Done():
						t.Fatal("upstream did not receive request")
					}
					if r.Header.Get("X-E2e-Run") != "fixture-run" {
						t.Error("configured provider header missing or mutated")
					}
					if r.Header.Get("OpenAI-Organization") != "fixture-org" {
						t.Error("configured organization header missing")
					}
					wantAuth, wantAPIKey := "Bearer fixture-credential", ""
					if adapter.authType == "api-key" {
						wantAuth, wantAPIKey = "", "fixture-credential"
					}
					if r.Header.Get("Authorization") != wantAuth || r.Header.Get("Api-Key") != wantAPIKey {
						t.Error("provider authentication changed by config headers")
					}
					for _, name := range []string{"X-Api-Key", "Proxy-Authorization", "X-Agentcc-Org-Id", "X-Agentcc-Run-Id", "auth-type", "api-version"} {
						if r.Header.Get(name) != "" {
							t.Errorf("reserved header %s sent from config", name)
						}
					}
					wantPath := adapter.prefix + route.path
					if adapter.format == "azure" {
						wantPath = "/openai/deployments/fixture" + route.path
						if route.name == "models" {
							wantPath = "/openai/models"
						}
						if r.URL.Query().Get("api-version") != "2024-10-21" {
							t.Error("Azure version changed")
						}
					}
					if r.URL.Path != wantPath {
						t.Errorf("path = %s, want %s", r.URL.Path, wantPath)
					}
					if route.name != "models" {
						if route.multipart {
							if !strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/form-data; boundary=") {
								t.Error("multipart boundary overridden")
							}
						} else if r.Header.Get("Content-Type") != "application/json" {
							t.Error("content type overridden")
						}
					}
					if route.stream && r.Header.Get("Accept") != "text/event-stream" {
						t.Error("stream accept overridden")
					}
					if strings.Contains(route.name, "assistants") && r.Header.Get("OpenAI-Beta") != "assistants=v2" {
						t.Error("per-call beta overridden")
					}
				})
			}
		})
	}
}

func TestConfiguredProviderHeadersValidation(t *testing.T) {
	for _, format := range []string{"openai", "azure"} {
		for _, headers := range []map[string]string{
			{"": "fixture"}, {"Bad Header": "fixture"}, {"Bad:Header": "fixture"},
			{"Bad\r\nHeader": "fixture"}, {"X-Ünicode": "fixture"},
			{"X-Fixture": "value-not-to-log\r\nInjected: true"}, {"X-Fixture": "value-not-to-log\x00"},
			{"X-Fixture": "one", "x-fixture": "two"},
		} {
			p, err := createProvider("fixture", config.ProviderConfig{APIFormat: format, BaseURL: "http://127.0.0.1:9", Headers: headers})
			if err == nil {
				p.Close()
				t.Errorf("%s accepted invalid or ambiguous headers", format)
				continue
			}
			if strings.Contains(err.Error(), "value-not-to-log") {
				t.Error("header value leaked in constructor error")
			}
		}
	}
}

func TestConfiguredProviderHeadersCannotSupplyAuth(t *testing.T) {
	for _, format := range []string{"openai", "azure"} {
		t.Run(format, func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				for _, name := range []string{"Authorization", "Api-Key", "X-Api-Key", "X-Agentcc-Org-Id"} {
					if r.Header.Get(name) != "" {
						t.Errorf("config supplied reserved header %s without provider credential", name)
					}
				}
				_, _ = io.WriteString(w, `{}`)
			}))
			defer upstream.Close()
			p, err := createProvider("fixture", config.ProviderConfig{APIFormat: format, BaseURL: upstream.URL, Headers: map[string]string{
				"Authorization": "fixture", "api-key": "fixture", "x-api-key": "fixture", "x-agentcc-org-id": "fixture",
			}})
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close()
			if _, err := p.ListModels(context.Background()); err != nil {
				t.Fatal(err)
			}
		})
	}
}
