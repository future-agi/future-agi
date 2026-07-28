package smallestai

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func newTestProvider(t *testing.T, baseURL string) *Provider {
	t.Helper()
	cfg := config.ProviderConfig{
		BaseURL:       baseURL,
		APIKey:        "test-api-key",
		MaxConcurrent: 10,
		ConnPoolSize:  5,
	}
	p, err := New("smallest-ai-test", cfg)
	if err != nil {
		t.Fatalf("failed to create provider: %v", err)
	}
	return p
}

// ---------------------------------------------------------------------------
// New() / ID()
// ---------------------------------------------------------------------------

func TestNew_DefaultValues(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL: "https://api.smallest.ai/waves/v1",
		APIKey:  "sk-123",
	}
	p, err := New("smallest_ai", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.ID() != "smallest_ai" {
		t.Errorf("ID() = %q, want %q", p.ID(), "smallest_ai")
	}
	if p.baseURL != "https://api.smallest.ai/waves/v1" {
		t.Errorf("baseURL = %q, want no trailing slash", p.baseURL)
	}
	if cap(p.semaphore) != 100 {
		t.Errorf("default semaphore cap = %d, want 100", cap(p.semaphore))
	}
}

func TestNew_TrimsTrailingSlash(t *testing.T) {
	p, err := New("t", config.ProviderConfig{BaseURL: "https://api.smallest.ai/waves/v1/"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()
	if p.baseURL != "https://api.smallest.ai/waves/v1" {
		t.Errorf("baseURL = %q, want trailing slash trimmed", p.baseURL)
	}
}

// ---------------------------------------------------------------------------
// Unsupported chat operations
// ---------------------------------------------------------------------------

func TestChatCompletion_Unsupported(t *testing.T) {
	p := newTestProvider(t, "https://api.smallest.ai/waves/v1")
	defer p.Close()

	_, err := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Status != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusBadRequest)
	}
}

func TestStreamChatCompletion_Unsupported(t *testing.T) {
	p := newTestProvider(t, "https://api.smallest.ai/waves/v1")
	defer p.Close()

	chunks, errs := p.StreamChatCompletion(context.Background(), &models.ChatCompletionRequest{})

	if _, ok := <-chunks; ok {
		t.Error("expected chunks channel to be closed with no values")
	}
	err, ok := <-errs
	if !ok {
		t.Fatal("expected an error on the errs channel")
	}
	if _, ok := err.(*models.APIError); !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
}

func TestListModels_ReturnsNil(t *testing.T) {
	p := newTestProvider(t, "https://api.smallest.ai/waves/v1")
	defer p.Close()

	models, err := p.ListModels(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if models != nil {
		t.Errorf("models = %v, want nil", models)
	}
}

// ---------------------------------------------------------------------------
// CreateSpeech
// ---------------------------------------------------------------------------

func TestCreateSpeech_Success(t *testing.T) {
	var gotAuth, gotSource, gotContentType string
	var gotBody map[string]any

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		gotSource = r.Header.Get("X-Source")
		gotContentType = r.Header.Get("Content-Type")

		if r.URL.Path != "/tts" {
			t.Errorf("path = %q, want /tts", r.URL.Path)
		}

		body := make(map[string]any)
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decoding request body: %v", err)
		}
		gotBody = body

		w.Header().Set("Content-Type", "audio/wav")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("RIFF-fake-wav-bytes"))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	speed := 1.5
	stream, contentType, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{
		Model: "smallest_ai/lightning_v3.1",
		Input: "Hello world",
		Voice: "avery",
		Speed: &speed,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer stream.Close()

	if contentType != "audio/wav" {
		t.Errorf("contentType = %q, want audio/wav", contentType)
	}
	if gotAuth != "Bearer test-api-key" {
		t.Errorf("Authorization = %q, want Bearer test-api-key", gotAuth)
	}
	if gotSource != "future-agi" {
		t.Errorf("X-Source = %q, want future-agi", gotSource)
	}
	if gotContentType != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", gotContentType)
	}
	if gotBody["model"] != "lightning_v3.1" {
		t.Errorf("body.model = %v, want lightning_v3.1 (provider prefix stripped)", gotBody["model"])
	}
	if gotBody["voice_id"] != "avery" {
		t.Errorf("body.voice_id = %v, want avery", gotBody["voice_id"])
	}
	if gotBody["speed"] != 1.5 {
		t.Errorf("body.speed = %v, want 1.5", gotBody["speed"])
	}

	data, err := io.ReadAll(stream)
	if err != nil {
		t.Fatalf("reading stream: %v", err)
	}
	if string(data) != "RIFF-fake-wav-bytes" {
		t.Errorf("stream data = %q, want RIFF-fake-wav-bytes", data)
	}
}

func TestCreateSpeech_DefaultsVoiceWhenEmpty(t *testing.T) {
	var gotVoice string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body := make(map[string]any)
		_ = json.NewDecoder(r.Body).Decode(&body)
		gotVoice, _ = body["voice_id"].(string)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	stream, _, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{
		Model: "lightning_v3.1",
		Input: "hi",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	stream.Close()

	if gotVoice != defaultVoice {
		t.Errorf("voice_id = %q, want default %q", gotVoice, defaultVoice)
	}
}

func TestCreateSpeech_ReleasesSemaphoreOnClose(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("audio"))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	stream, _, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{Model: "lightning_v3.1", Input: "hi"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(p.semaphore) != 1 {
		t.Fatalf("semaphore len = %d before close, want 1", len(p.semaphore))
	}
	if err := stream.Close(); err != nil {
		t.Fatalf("closing stream: %v", err)
	}
	if len(p.semaphore) != 0 {
		t.Errorf("semaphore len = %d after close, want 0 (leaked)", len(p.semaphore))
	}
}

func TestCreateSpeech_UpstreamError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"error":{"message":"invalid voice_id","type":"invalid_request_error","code":"bad_voice"}}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	_, _, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{Model: "lightning_v3.1", Input: "hi", Voice: "nonexistent"})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Message != "invalid voice_id" {
		t.Errorf("message = %q, want %q", apiErr.Message, "invalid voice_id")
	}

	// The semaphore must be released on the error path too (no stream to Close()).
	if len(p.semaphore) != 0 {
		t.Errorf("semaphore len = %d after error, want 0 (leaked)", len(p.semaphore))
	}
}

// ---------------------------------------------------------------------------
// CreateTranscription
// ---------------------------------------------------------------------------

func TestCreateTranscription_Success(t *testing.T) {
	var gotQuery, gotContentType, gotAuth string
	var gotBody []byte

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		gotContentType = r.Header.Get("Content-Type")
		gotAuth = r.Header.Get("Authorization")
		body, _ := io.ReadAll(r.Body)
		gotBody = body

		if r.URL.Path != "/stt/" {
			t.Errorf("path = %q, want /stt/", r.URL.Path)
		}

		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"success","transcription":"hello world"}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	resp, err := p.CreateTranscription(context.Background(), &models.TranscriptionRequest{
		Model:    "smallest_ai/pulse",
		FileData: []byte("fake-wav-bytes"),
		Language: "hi",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.Text != "hello world" {
		t.Errorf("Text = %q, want %q", resp.Text, "hello world")
	}
	if gotQuery != "model=pulse&language=hi" {
		t.Errorf("query = %q, want model=pulse&language=hi", gotQuery)
	}
	if gotContentType != "application/octet-stream" {
		t.Errorf("Content-Type = %q, want application/octet-stream", gotContentType)
	}
	if gotAuth != "Bearer test-api-key" {
		t.Errorf("Authorization = %q, want Bearer test-api-key", gotAuth)
	}
	if string(gotBody) != "fake-wav-bytes" {
		t.Errorf("body = %q, want raw audio bytes passthrough", gotBody)
	}
}

func TestCreateTranscription_DefaultsLanguageWhenEmpty(t *testing.T) {
	var gotQuery string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"success","transcription":""}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	_, err := p.CreateTranscription(context.Background(), &models.TranscriptionRequest{
		Model:    "pulse-pro",
		FileData: []byte("audio"),
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if gotQuery != "model=pulse-pro&language=en" {
		t.Errorf("query = %q, want model=pulse-pro&language=en (default language)", gotQuery)
	}
}

func TestCreateTranscription_UpstreamError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"error":{"message":"invalid api key","type":"authentication_error","code":"invalid_key"}}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	_, err := p.CreateTranscription(context.Background(), &models.TranscriptionRequest{Model: "pulse", FileData: []byte("audio")})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Message != "invalid api key" {
		t.Errorf("message = %q, want %q", apiErr.Message, "invalid api key")
	}
}

// ---------------------------------------------------------------------------
// Close
// ---------------------------------------------------------------------------

func TestClose(t *testing.T) {
	p := newTestProvider(t, "https://api.smallest.ai/waves/v1")
	if err := p.Close(); err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}
