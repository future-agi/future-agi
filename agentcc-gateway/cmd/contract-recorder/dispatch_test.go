package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// allCaps implements providers.Provider and every capability interface an
// endpoint drives, and logs each call as "Method arg arg ...".
type allCaps struct{ calls []string }

func (f *allCaps) log(format string, args ...any) {
	f.calls = append(f.calls, fmt.Sprintf(format, args...))
}

func stream() io.ReadCloser { return io.NopCloser(strings.NewReader("data: {}\n\n")) }

func chunks() (<-chan models.StreamChunk, <-chan error) {
	c, e := make(chan models.StreamChunk, 1), make(chan error, 1)
	c <- models.StreamChunk{}
	close(c)
	e <- nil
	return c, e
}

func (f *allCaps) ID() string   { return "fake" }
func (f *allCaps) Close() error { return nil }
func (f *allCaps) ChatCompletion(_ context.Context, r *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	f.log("ChatCompletion %s", r.Model)
	return nil, nil
}
func (f *allCaps) StreamChatCompletion(_ context.Context, r *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	f.log("StreamChatCompletion %s", r.Model)
	return chunks()
}
func (f *allCaps) ListModels(context.Context) ([]models.ModelObject, error) {
	f.log("ListModels")
	return nil, nil
}
func (f *allCaps) ChatNeedsResponses(*models.ChatCompletionRequest) bool { return true }
func (f *allCaps) ChatCompletionViaResponses(_ context.Context, r *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	f.log("ChatCompletionViaResponses %s", r.Model)
	return nil, nil
}
func (f *allCaps) StreamChatCompletionViaResponses(_ context.Context, r *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	f.log("StreamChatCompletionViaResponses %s", r.Model)
	return chunks()
}
func (f *allCaps) TextCompletion(_ context.Context, r *models.CompletionRequest) (*models.CompletionResponse, error) {
	f.log("TextCompletion %s", r.Model)
	return nil, nil
}
func (f *allCaps) CreateEmbedding(_ context.Context, r *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	f.log("CreateEmbedding %s", r.Model)
	return nil, nil
}
func (f *allCaps) CreateImage(_ context.Context, r *models.ImageRequest) (*models.ImageResponse, error) {
	f.log("CreateImage %s", r.Model)
	return nil, nil
}
func (f *allCaps) CreateSpeech(_ context.Context, r *models.SpeechRequest) (io.ReadCloser, string, error) {
	f.log("CreateSpeech %s", r.Model)
	return stream(), "audio/mpeg", nil
}
func (f *allCaps) CreateTranscription(_ context.Context, r *models.TranscriptionRequest) (*models.TranscriptionResponse, error) {
	f.log("CreateTranscription %s %s %s", r.Model, r.FileName, r.FileData)
	return nil, nil
}
func (f *allCaps) CreateTranslation(_ context.Context, r *models.TranslationRequest) (*models.TranslationResponse, error) {
	f.log("CreateTranslation %s %s %s", r.Model, r.FileName, r.FileData)
	return nil, nil
}
func (f *allCaps) Rerank(_ context.Context, r *models.RerankRequest) (*models.RerankResponse, error) {
	f.log("Rerank %s", r.Model)
	return nil, nil
}
func (f *allCaps) OCR(_ context.Context, r *models.OCRRequest) (*models.OCRResponse, error) {
	f.log("OCR %s", r.Model)
	return nil, nil
}
func (f *allCaps) CreateResponse(_ context.Context, b []byte) ([]byte, int, error) {
	f.log("CreateResponse %s", b)
	return nil, 200, nil
}
func (f *allCaps) StreamResponse(_ context.Context, b []byte) (io.ReadCloser, int, error) {
	f.log("StreamResponse %s", b)
	return stream(), 200, nil
}
func (f *allCaps) ProxyAssistantsRequest(_ context.Context, method, path string, body []byte, q url.Values) ([]byte, int, http.Header, error) {
	f.log("ProxyAssistantsRequest %s %s %q %s", method, path, body, q.Encode())
	return nil, 200, nil, nil
}
func (f *allCaps) StreamAssistantsRequest(_ context.Context, method, path string, body []byte, q url.Values) (io.ReadCloser, int, http.Header, error) {
	f.log("StreamAssistantsRequest %s %s %s %s", method, path, body, q.Encode())
	return stream(), 200, nil, nil
}
func (f *allCaps) ProxyVectorStoresRequest(_ context.Context, method, path string, body []byte, q url.Values) ([]byte, int, http.Header, error) {
	f.log("ProxyVectorStoresRequest %s %s %q %s", method, path, body, q.Encode())
	return nil, 200, nil, nil
}
func (f *allCaps) CreateAnthropicMessage(_ context.Context, b []byte, h map[string]string) ([]byte, int, error) {
	f.log("CreateAnthropicMessage %s %v", b, h)
	return nil, 200, nil
}
func (f *allCaps) CountAnthropicTokens(_ context.Context, b []byte, h map[string]string) ([]byte, int, error) {
	f.log("CountAnthropicTokens %s %v", b, h)
	return nil, 200, nil
}
func (f *allCaps) StreamAnthropicMessage(_ context.Context, b []byte, h map[string]string) (io.ReadCloser, int, error) {
	f.log("StreamAnthropicMessage %s %v", b, h)
	return stream(), 200, nil
}
func (f *allCaps) GenerateContent(_ context.Context, model string, b []byte, h map[string]string) ([]byte, int, error) {
	f.log("GenerateContent %s %s %v", model, b, h)
	return nil, 200, nil
}
func (f *allCaps) StreamGenerateContent(_ context.Context, model string, b []byte, h map[string]string) (io.ReadCloser, int, error) {
	f.log("StreamGenerateContent %s %s %v", model, b, h)
	return stream(), 200, nil
}
func (f *allCaps) GenAICountTokens(_ context.Context, model string, b []byte, h map[string]string) ([]byte, int, error) {
	f.log("GenAICountTokens %s %s %v", model, b, h)
	return nil, 200, nil
}
func (f *allCaps) EmbedContent(_ context.Context, model string, b []byte, h map[string]string) ([]byte, int, error) {
	f.log("EmbedContent %s %s %v", model, b, h)
	return nil, 200, nil
}

// Compile-time proof that allCaps covers every capability drive uses.
var (
	_ providers.Provider                 = (*allCaps)(nil)
	_ providers.ChatViaResponsesProvider = (*allCaps)(nil)
	_ textCompleter                      = (*allCaps)(nil)
	_ providers.EmbeddingProvider        = (*allCaps)(nil)
	_ providers.ImageProvider            = (*allCaps)(nil)
	_ providers.SpeechProvider           = (*allCaps)(nil)
	_ providers.TranscriptionProvider    = (*allCaps)(nil)
	_ providers.TranslationProvider      = (*allCaps)(nil)
	_ providers.RerankProvider           = (*allCaps)(nil)
	_ providers.OCRProvider              = (*allCaps)(nil)
	_ providers.ResponsesProvider        = (*allCaps)(nil)
	_ providers.AssistantsProvider       = (*allCaps)(nil)
	_ providers.VectorStoresProvider     = (*allCaps)(nil)
	_ providers.AnthropicNativeProvider  = (*allCaps)(nil)
	_ providers.GenAINativeProvider      = (*allCaps)(nil)
)

func TestDriveEveryEndpoint(t *testing.T) {
	audio := &contracttest.InputFile{Name: "audio.wav", Data: []byte("RIFF")}
	tests := []struct {
		name string
		call call
		want string
	}{
		{"chat", call{Endpoint: "chat", Body: []byte(`{"model":"m"}`)}, "ChatCompletion m"},
		{"chat stream", call{Endpoint: "chat", Body: []byte(`{"model":"m","stream":true}`), Stream: true}, "StreamChatCompletion m"},
		{"chat_via_responses", call{Endpoint: "chat_via_responses", Body: []byte(`{"model":"m"}`)}, "ChatCompletionViaResponses m"},
		{"chat_via_responses stream", call{Endpoint: "chat_via_responses", Body: []byte(`{"model":"m","stream":true}`), Stream: true}, "StreamChatCompletionViaResponses m"},
		{"completions", call{Endpoint: "completions", Body: []byte(`{"model":"m","prompt":"x"}`)}, "TextCompletion m"},
		{"embeddings", call{Endpoint: "embeddings", Body: []byte(`{"model":"m","input":"x"}`)}, "CreateEmbedding m"},
		{"images", call{Endpoint: "images", Body: []byte(`{"model":"m","prompt":"x"}`)}, "CreateImage m"},
		{"speech", call{Endpoint: "speech", Body: []byte(`{"model":"m","input":"x"}`)}, "CreateSpeech m"},
		{"transcription", call{Endpoint: "transcription", Body: []byte(`{"model":"m","file":"audio.wav"}`), Input: audio}, "CreateTranscription m audio.wav RIFF"},
		{"translation", call{Endpoint: "translation", Body: []byte(`{"model":"m","file":"audio.wav"}`), Input: audio}, "CreateTranslation m audio.wav RIFF"},
		{"rerank", call{Endpoint: "rerank", Body: []byte(`{"model":"m","query":"q"}`)}, "Rerank m"},
		{"ocr", call{Endpoint: "ocr", Body: []byte(`{"model":"m"}`)}, "OCR m"},
		{"responses", call{Endpoint: "responses", Body: []byte(`{"model":"m"}`)}, `CreateResponse {"model":"m"}`},
		{"responses stream", call{Endpoint: "responses", Body: []byte(`{"stream":true}`), Stream: true}, `StreamResponse {"stream":true}`},
		{"assistants", call{Endpoint: "assistants", Body: []byte(`{"method":"POST","path":"/v1/assistants","body":{"model":"m"}}`)},
			`ProxyAssistantsRequest POST /v1/assistants "{\"model\":\"m\"}" `},
		{"assistants GET", call{Endpoint: "assistants", Body: []byte(`{"method":"GET","path":"/v1/assistants","query":{"limit":["2"]}}`)},
			`ProxyAssistantsRequest GET /v1/assistants "" limit=2`},
		{"assistants stream", call{Endpoint: "assistants", Body: []byte(`{"method":"POST","path":"/v1/threads/runs","body":{"stream":true}}`), Stream: true},
			`StreamAssistantsRequest POST /v1/threads/runs {"stream":true} `},
		{"vector_stores", call{Endpoint: "vector_stores", Body: []byte(`{"method":"POST","path":"/v1/vector_stores","body":{"name":"v"}}`)},
			`ProxyVectorStoresRequest POST /v1/vector_stores "{\"name\":\"v\"}" `},
		{"anthropic_messages", call{Endpoint: "anthropic_messages", Body: []byte(`{"model":"m"}`)}, `CreateAnthropicMessage {"model":"m"} map[]`},
		{"anthropic_messages stream", call{Endpoint: "anthropic_messages", Body: []byte(`{"stream":true}`), Stream: true}, `StreamAnthropicMessage {"stream":true} map[]`},
		{"anthropic_count_tokens", call{Endpoint: "anthropic_count_tokens", Body: []byte(`{"model":"m"}`)}, `CountAnthropicTokens {"model":"m"} map[]`},
		{"genai_generate", call{Endpoint: "genai_generate", Body: []byte(`{"contents":[]}`), Model: "g-1"}, `GenerateContent g-1 {"contents":[]} map[]`},
		{"genai_generate stream", call{Endpoint: "genai_generate", Body: []byte(`{"contents":[]}`), Model: "g-1", Stream: true}, `StreamGenerateContent g-1 {"contents":[]} map[]`},
		{"genai_count_tokens", call{Endpoint: "genai_count_tokens", Body: []byte(`{}`), Model: "g-1"}, `GenAICountTokens g-1 {} map[]`},
		{"genai_embed", call{Endpoint: "genai_embed", Body: []byte(`{}`), Model: "g-1"}, `EmbedContent g-1 {} map[]`},
		{"list_models", call{Endpoint: "list_models", Body: []byte(`{}`)}, "ListModels"},
	}
	covered := map[string]bool{}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			f := &allCaps{}
			if err := drive(t.Context(), f, tc.call); err != nil {
				t.Fatalf("drive: %v", err)
			}
			if len(f.calls) != 1 || f.calls[0] != tc.want {
				t.Errorf("calls = %q, want [%q]", f.calls, tc.want)
			}
		})
		covered[tc.call.Endpoint] = true
	}
	for _, ep := range contracttest.Endpoints {
		if !covered[ep] {
			t.Errorf("endpoint %q is not driven by this test: add it to drive and here", ep)
		}
	}
}

func TestDriveRefusals(t *testing.T) {
	// An adapter without the capability is reported, not called.
	err := drive(t.Context(), &chatOnlyProvider{}, call{Endpoint: "embeddings", Body: []byte(`{"model":"m"}`)})
	if err == nil || !strings.Contains(err.Error(), "does not implement EmbeddingProvider") {
		t.Errorf("drive without the capability = %v", err)
	}
	if err := drive(t.Context(), &allCaps{}, call{Endpoint: "transcription", Body: []byte(`{"model":"m"}`)}); err == nil {
		t.Error("a transcription without an input file: want an error")
	}
	if err := drive(t.Context(), &allCaps{}, call{Endpoint: "vector_stores", Body: []byte(`{"name":"v"}`)}); err == nil {
		t.Error("a vector_stores request.json that is not a passthrough request: want an error")
	}
	if err := drive(t.Context(), &allCaps{}, call{Endpoint: "nope", Body: []byte(`{}`)}); err == nil {
		t.Error("an unknown endpoint: want an error")
	}
	if err := drive(t.Context(), &allCaps{}, call{Endpoint: "embeddings", Body: []byte(`{"model":1}`)}); err == nil ||
		!strings.Contains(err.Error(), "request.json") {
		t.Errorf("a request.json the type cannot hold = %v", err)
	}
}

// chatOnlyProvider implements providers.Provider and nothing else.
type chatOnlyProvider struct{}

func (chatOnlyProvider) ID() string   { return "chatonly" }
func (chatOnlyProvider) Close() error { return nil }
func (chatOnlyProvider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, nil
}
func (chatOnlyProvider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	return chunks()
}
func (chatOnlyProvider) ListModels(context.Context) ([]models.ModelObject, error) { return nil, nil }
