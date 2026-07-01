package twelvelabs

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func newTestProvider(t *testing.T, serverURL string) *Provider {
	t.Helper()
	p, err := New("test-twelvelabs", config.ProviderConfig{
		BaseURL: serverURL,
		APIKey:  "test-key",
	})
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	return p
}

func TestNew_DefaultBaseURL(t *testing.T) {
	p, err := New("tl", config.ProviderConfig{APIKey: "k"})
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	if p.baseURL != defaultBaseURL {
		t.Errorf("baseURL = %q, want %q", p.baseURL, defaultBaseURL)
	}
}

// ---------------------------------------------------------------------------
// extractVideoAndPrompt
// ---------------------------------------------------------------------------

func userMessage(t *testing.T, content any) models.Message {
	t.Helper()
	raw, err := json.Marshal(content)
	if err != nil {
		t.Fatalf("marshal content: %v", err)
	}
	return models.Message{Role: "user", Content: raw}
}

func TestExtractVideoAndPrompt_MultimodalURL(t *testing.T) {
	msg := userMessage(t, []map[string]any{
		{"type": "text", "text": "What happens in this video?"},
		{"type": "video_url", "video_url": map[string]string{"url": "https://example.com/clip.mp4"}},
	})
	video, prompt, err := extractVideoAndPrompt([]models.Message{msg})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if video.Type != "url" || video.URL != "https://example.com/clip.mp4" {
		t.Errorf("video = %+v", video)
	}
	if prompt != "What happens in this video?" {
		t.Errorf("prompt = %q", prompt)
	}
}

func TestExtractVideoAndPrompt_ImageURLPart(t *testing.T) {
	// OpenAI clients often only know the image_url part; accept it for video too.
	msg := userMessage(t, []map[string]any{
		{"type": "text", "text": "Summarize."},
		{"type": "image_url", "image_url": map[string]string{"url": "https://example.com/v.mp4"}},
	})
	video, _, err := extractVideoAndPrompt([]models.Message{msg})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if video.URL != "https://example.com/v.mp4" {
		t.Errorf("video.URL = %q", video.URL)
	}
}

func TestExtractVideoAndPrompt_AssetID(t *testing.T) {
	msg := userMessage(t, []map[string]any{
		{"type": "text", "text": "Describe it."},
		{"type": "input_asset", "asset_id": "asset_123"},
	})
	video, _, err := extractVideoAndPrompt([]models.Message{msg})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if video.Type != "asset" || video.AssetID != "asset_123" {
		t.Errorf("video = %+v", video)
	}
}

func TestExtractVideoAndPrompt_BareURLInString(t *testing.T) {
	msg := models.Message{Role: "user", Content: json.RawMessage(
		`"Describe https://example.com/clip.mp4 please"`)}
	video, prompt, err := extractVideoAndPrompt([]models.Message{msg})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if video.URL != "https://example.com/clip.mp4" {
		t.Errorf("video.URL = %q", video.URL)
	}
	if !strings.Contains(prompt, "Describe") || strings.Contains(prompt, "http") {
		t.Errorf("prompt should keep text and drop URL, got %q", prompt)
	}
}

func TestExtractVideoAndPrompt_NoVideo(t *testing.T) {
	msg := userMessage(t, []map[string]any{{"type": "text", "text": "hello"}})
	_, _, err := extractVideoAndPrompt([]models.Message{msg})
	if err == nil {
		t.Fatal("expected error when no video present")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok || apiErr.Status != http.StatusBadRequest {
		t.Errorf("expected 400 APIError, got %v", err)
	}
}

func TestTranslateAnalyzeRequest_ClampsMaxTokens(t *testing.T) {
	small := 10
	req := &models.ChatCompletionRequest{
		Model:     "pegasus1.5",
		MaxTokens: &small,
		Messages: []models.Message{userMessage(t, []map[string]any{
			{"type": "text", "text": "go"},
			{"type": "video_url", "video_url": map[string]string{"url": "https://e.com/v.mp4"}},
		})},
	}
	ar, err := translateAnalyzeRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.MaxTokens != 512 {
		t.Errorf("max_tokens = %d, want clamped to 512", ar.MaxTokens)
	}
	if ar.ModelName != "pegasus1.5" {
		t.Errorf("model = %q", ar.ModelName)
	}
}

func TestParseEmbeddingInput(t *testing.T) {
	one, err := parseEmbeddingInput(json.RawMessage(`"hello"`))
	if err != nil || len(one) != 1 || one[0] != "hello" {
		t.Fatalf("single = %v, err = %v", one, err)
	}
	many, err := parseEmbeddingInput(json.RawMessage(`["a","b"]`))
	if err != nil || len(many) != 2 {
		t.Fatalf("many = %v, err = %v", many, err)
	}
}

func TestEmbedResponse_FirstFloatVector(t *testing.T) {
	body := `{"model_name":"marengo3.0","text_embedding":{"segments":[{"float":[0.1,0.2,0.3]}]}}`
	var er embedResponse
	if err := json.Unmarshal([]byte(body), &er); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	v := er.firstFloatVector()
	if len(v) != 3 || v[0] != 0.1 {
		t.Errorf("vector = %v", v)
	}
}

func TestParseError(t *testing.T) {
	apiErr := parseError(http.StatusUnauthorized, []byte(`{"code":"api_key_invalid","message":"bad key"}`))
	if apiErr.Status != http.StatusUnauthorized {
		t.Errorf("status = %d", apiErr.Status)
	}
	apiErr = parseError(http.StatusBadRequest, []byte(`{"code":"parameter_invalid","message":"bad param"}`))
	if apiErr.Status != http.StatusBadRequest || apiErr.Code != "parameter_invalid" {
		t.Errorf("got %+v", apiErr)
	}
}

// ---------------------------------------------------------------------------
// httptest round-trips (no network)
// ---------------------------------------------------------------------------

func TestChatCompletion_RoundTrip(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/analyze" {
			t.Errorf("path = %q", r.URL.Path)
		}
		if r.Header.Get("x-api-key") != "test-key" {
			t.Errorf("missing x-api-key header")
		}
		var req analyzeRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		if req.Video.URL != "https://e.com/v.mp4" {
			t.Errorf("video.url = %q", req.Video.URL)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"abc","data":"A cat plays.","usage":{"output_tokens":4}}`))
	}))
	defer srv.Close()

	p := newTestProvider(t, srv.URL)
	resp, err := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model: "pegasus1.5",
		Messages: []models.Message{userMessage(t, []map[string]any{
			{"type": "text", "text": "Describe."},
			{"type": "video_url", "video_url": map[string]string{"url": "https://e.com/v.mp4"}},
		})},
	})
	if err != nil {
		t.Fatalf("ChatCompletion error: %v", err)
	}
	var content string
	_ = json.Unmarshal(resp.Choices[0].Message.Content, &content)
	if content != "A cat plays." {
		t.Errorf("content = %q", content)
	}
	if resp.Usage == nil || resp.Usage.CompletionTokens != 4 {
		t.Errorf("usage = %+v", resp.Usage)
	}
}

func TestCreateEmbedding_RoundTrip(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/embed" {
			t.Errorf("path = %q", r.URL.Path)
		}
		if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "multipart/form-data") {
			t.Errorf("Content-Type = %q, want multipart/form-data", ct)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"model_name":"marengo3.0","text_embedding":{"segments":[{"float":[0.5,0.6]}]}}`))
	}))
	defer srv.Close()

	p := newTestProvider(t, srv.URL)
	resp, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "marengo3.0",
		Input: json.RawMessage(`"a person riding a bicycle"`),
	})
	if err != nil {
		t.Fatalf("CreateEmbedding error: %v", err)
	}
	if len(resp.Data) != 1 {
		t.Fatalf("data len = %d", len(resp.Data))
	}
	var vec []float64
	_ = json.Unmarshal(resp.Data[0].Embedding, &vec)
	if len(vec) != 2 || vec[0] != 0.5 {
		t.Errorf("vector = %v", vec)
	}
}

// ---------------------------------------------------------------------------
// Live test (opt-in via TWELVELABS_API_KEY). Skipped in CI without a key.
// ---------------------------------------------------------------------------

func TestLive_MarengoEmbedding(t *testing.T) {
	key := os.Getenv("TWELVELABS_API_KEY")
	if key == "" {
		t.Skip("TWELVELABS_API_KEY not set; skipping live test")
	}
	p, err := New("tl-live", config.ProviderConfig{APIKey: key})
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	resp, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "marengo3.0",
		Input: json.RawMessage(`"a person riding a bicycle"`),
	})
	if err != nil {
		t.Fatalf("live CreateEmbedding error: %v", err)
	}
	var vec []float64
	_ = json.Unmarshal(resp.Data[0].Embedding, &vec)
	if len(vec) != 512 {
		t.Errorf("Marengo embedding dim = %d, want 512", len(vec))
	}
}
