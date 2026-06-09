package openai

import (
	"context"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// newTestProvider creates a provider pointing at the given test server.
func newTestProvider(t *testing.T, serverURL string) *Provider {
	t.Helper()
	p, err := New("test-openai", config.ProviderConfig{
		BaseURL: serverURL,
		APIKey:  "test-key",
	})
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	return p
}

// ─────────────────────────────────────────────────────────────
// CreateImage tests
// ─────────────────────────────────────────────────────────────

func TestCreateImage_Success(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify request.
		if r.Method != "POST" {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/v1/images/generations" {
			t.Errorf("path = %s, want /v1/images/generations", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			t.Errorf("auth = %q, want %q", got, "Bearer test-key")
		}
		if got := r.Header.Get("Content-Type"); got != "application/json" {
			t.Errorf("content-type = %q, want application/json", got)
		}

		// Verify body.
		var req models.ImageRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatalf("decode request body: %v", err)
		}
		if req.Model != "dall-e-3" {
			t.Errorf("model = %q, want dall-e-3", req.Model)
		}
		if req.Prompt != "a cat on a roof" {
			t.Errorf("prompt = %q, want %q", req.Prompt, "a cat on a roof")
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.ImageResponse{
			Created: 1234567890,
			Data: []models.ImageData{
				{URL: "https://example.com/image.png", RevisedPrompt: "a tabby cat on a roof"},
			},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	resp, err := p.CreateImage(context.Background(), &models.ImageRequest{
		Model:  "dall-e-3",
		Prompt: "a cat on a roof",
		Size:   "1024x1024",
	})
	if err != nil {
		t.Fatalf("CreateImage error: %v", err)
	}
	if len(resp.Data) != 1 {
		t.Fatalf("data length = %d, want 1", len(resp.Data))
	}
	if resp.Data[0].URL != "https://example.com/image.png" {
		t.Errorf("url = %q, want %q", resp.Data[0].URL, "https://example.com/image.png")
	}
	if resp.Data[0].RevisedPrompt != "a tabby cat on a roof" {
		t.Errorf("revised_prompt = %q, want %q", resp.Data[0].RevisedPrompt, "a tabby cat on a roof")
	}
}

func TestCreateImage_ModelPrefixStripped(t *testing.T) {
	var receivedModel string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req models.ImageRequest
		json.NewDecoder(r.Body).Decode(&req)
		receivedModel = req.Model
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.ImageResponse{Created: 1, Data: []models.ImageData{{URL: "https://example.com/img.png"}}})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateImage(context.Background(), &models.ImageRequest{
		Model:  "openai/dall-e-3",
		Prompt: "test",
	})
	if err != nil {
		t.Fatalf("CreateImage error: %v", err)
	}
	if receivedModel != "dall-e-3" {
		t.Errorf("model sent to upstream = %q, want %q", receivedModel, "dall-e-3")
	}
}

func TestCreateImage_ProviderError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(models.ErrorResponse{
			Error: models.ErrorDetail{
				Type:    "invalid_request_error",
				Message: "invalid size",
			},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateImage(context.Background(), &models.ImageRequest{
		Model:  "dall-e-3",
		Prompt: "test",
		Size:   "999x999",
	})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Message != "invalid size" {
		t.Errorf("message = %q, want %q", apiErr.Message, "invalid size")
	}
}

func TestCreateImage_ContextCanceled(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate slow server.
		<-r.Context().Done()
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	_, err := p.CreateImage(ctx, &models.ImageRequest{
		Model:  "dall-e-3",
		Prompt: "test",
	})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
}

func TestCreateImage_B64Response(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.ImageResponse{
			Created: 1,
			Data:    []models.ImageData{{B64JSON: "iVBORw0KGgoAAAANS..."}},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	resp, err := p.CreateImage(context.Background(), &models.ImageRequest{
		Model:          "dall-e-3",
		Prompt:         "test",
		ResponseFormat: "b64_json",
	})
	if err != nil {
		t.Fatalf("CreateImage error: %v", err)
	}
	if resp.Data[0].B64JSON != "iVBORw0KGgoAAAANS..." {
		t.Errorf("b64_json = %q, want non-empty", resp.Data[0].B64JSON)
	}
}

func TestCreateImage_MultipleImages(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.ImageResponse{
			Created: 1,
			Data: []models.ImageData{
				{URL: "https://example.com/1.png"},
				{URL: "https://example.com/2.png"},
			},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	n := 2
	resp, err := p.CreateImage(context.Background(), &models.ImageRequest{
		Model:  "dall-e-2",
		Prompt: "test",
		N:      &n,
	})
	if err != nil {
		t.Fatalf("CreateImage error: %v", err)
	}
	if len(resp.Data) != 2 {
		t.Errorf("data length = %d, want 2", len(resp.Data))
	}
}

// ─────────────────────────────────────────────────────────────
// CreateSpeech tests
// ─────────────────────────────────────────────────────────────

func TestCreateSpeech_Success(t *testing.T) {
	audioData := "fake-audio-bytes"
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/v1/audio/speech" {
			t.Errorf("path = %s, want /v1/audio/speech", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			t.Errorf("auth = %q, want %q", got, "Bearer test-key")
		}

		var req models.SpeechRequest
		json.NewDecoder(r.Body).Decode(&req)
		if req.Model != "tts-1" {
			t.Errorf("model = %q, want tts-1", req.Model)
		}
		if req.Voice != "alloy" {
			t.Errorf("voice = %q, want alloy", req.Voice)
		}

		w.Header().Set("Content-Type", "audio/mpeg")
		w.Write([]byte(audioData))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	body, contentType, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{
		Model: "tts-1",
		Input: "Hello world",
		Voice: "alloy",
	})
	if err != nil {
		t.Fatalf("CreateSpeech error: %v", err)
	}
	defer body.Close()

	if contentType != "audio/mpeg" {
		t.Errorf("content-type = %q, want audio/mpeg", contentType)
	}

	data, err := io.ReadAll(body)
	if err != nil {
		t.Fatalf("ReadAll error: %v", err)
	}
	if string(data) != audioData {
		t.Errorf("audio data = %q, want %q", string(data), audioData)
	}
}

func TestCreateSpeech_ModelPrefixStripped(t *testing.T) {
	var receivedModel string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req models.SpeechRequest
		json.NewDecoder(r.Body).Decode(&req)
		receivedModel = req.Model
		w.Header().Set("Content-Type", "audio/mpeg")
		w.Write([]byte("audio"))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	body, _, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{
		Model: "openai/tts-1-hd",
		Input: "test",
		Voice: "nova",
	})
	if err != nil {
		t.Fatalf("CreateSpeech error: %v", err)
	}
	body.Close()

	if receivedModel != "tts-1-hd" {
		t.Errorf("model sent = %q, want tts-1-hd", receivedModel)
	}
}

func TestCreateSpeech_DefaultContentType(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Explicitly clear Content-Type to simulate missing header.
		w.Header()["Content-Type"] = nil
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("audio"))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	body, contentType, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{
		Model: "tts-1",
		Input: "test",
		Voice: "alloy",
	})
	if err != nil {
		t.Fatalf("CreateSpeech error: %v", err)
	}
	defer body.Close()

	if contentType != "audio/mpeg" {
		t.Errorf("content-type = %q, want default audio/mpeg", contentType)
	}
}

func TestCreateSpeech_ProviderError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(models.ErrorResponse{
			Error: models.ErrorDetail{
				Type:    "invalid_request_error",
				Message: "invalid voice",
			},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, _, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{
		Model: "tts-1",
		Input: "test",
		Voice: "bad",
	})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Message != "invalid voice" {
		t.Errorf("message = %q, want %q", apiErr.Message, "invalid voice")
	}
}

func TestCreateSpeech_SemaphoreReleasedOnClose(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "audio/mpeg")
		w.Write([]byte("audio"))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)

	// Fill semaphore to 1 below capacity, then make a speech call.
	// After closing the body, semaphore should be released.
	body, _, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{
		Model: "tts-1",
		Input: "test",
		Voice: "alloy",
	})
	if err != nil {
		t.Fatalf("CreateSpeech error: %v", err)
	}

	// Semaphore slot is held (not released via defer since speech streams).
	// Verify by checking semaphore length.
	if len(p.semaphore) != 1 {
		t.Errorf("semaphore occupied = %d, want 1", len(p.semaphore))
	}

	body.Close()

	// After close, semaphore should be released.
	if len(p.semaphore) != 0 {
		t.Errorf("semaphore after close = %d, want 0", len(p.semaphore))
	}
}

func TestCreateSpeech_SemaphoreReleasedOnError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"message":"server error"}}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, _, err := p.CreateSpeech(context.Background(), &models.SpeechRequest{
		Model: "tts-1",
		Input: "test",
		Voice: "alloy",
	})
	if err == nil {
		t.Fatal("expected error, got nil")
	}

	// Semaphore should be released on error path.
	if len(p.semaphore) != 0 {
		t.Errorf("semaphore after error = %d, want 0", len(p.semaphore))
	}
}

// ─────────────────────────────────────────────────────────────
// CreateTranscription tests
// ─────────────────────────────────────────────────────────────

func TestCreateTranscription_Success(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/v1/audio/transcriptions" {
			t.Errorf("path = %s, want /v1/audio/transcriptions", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			t.Errorf("auth = %q, want %q", got, "Bearer test-key")
		}

		// Verify multipart form data.
		mediaType, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err != nil {
			t.Fatalf("parse content-type: %v", err)
		}
		if !strings.HasPrefix(mediaType, "multipart/") {
			t.Fatalf("content-type = %q, want multipart/*", mediaType)
		}

		mr := multipart.NewReader(r.Body, params["boundary"])

		fields := make(map[string]string)
		var fileContent []byte
		var fileName string

		for {
			part, err := mr.NextPart()
			if err == io.EOF {
				break
			}
			if err != nil {
				t.Fatalf("reading multipart: %v", err)
			}
			name := part.FormName()
			if name == "file" {
				fileName = part.FileName()
				fileContent, _ = io.ReadAll(part)
			} else {
				data, _ := io.ReadAll(part)
				fields[name] = string(data)
			}
			part.Close()
		}

		if fileName != "recording.wav" {
			t.Errorf("filename = %q, want recording.wav", fileName)
		}
		if string(fileContent) != "fake-audio" {
			t.Errorf("file content = %q, want fake-audio", string(fileContent))
		}
		if fields["model"] != "whisper-1" {
			t.Errorf("model = %q, want whisper-1", fields["model"])
		}
		if fields["language"] != "en" {
			t.Errorf("language = %q, want en", fields["language"])
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.TranscriptionResponse{Text: "hello world"})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	resp, err := p.CreateTranscription(context.Background(), &models.TranscriptionRequest{
		Model:    "whisper-1",
		FileData: []byte("fake-audio"),
		FileName: "recording.wav",
		Language: "en",
	})
	if err != nil {
		t.Fatalf("CreateTranscription error: %v", err)
	}
	if resp.Text != "hello world" {
		t.Errorf("text = %q, want %q", resp.Text, "hello world")
	}
}

func TestCreateTranscription_ModelPrefixStripped(t *testing.T) {
	var receivedModel string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mediaType, params, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if strings.HasPrefix(mediaType, "multipart/") {
			mr := multipart.NewReader(r.Body, params["boundary"])
			for {
				part, err := mr.NextPart()
				if err != nil {
					break
				}
				if part.FormName() == "model" {
					data, _ := io.ReadAll(part)
					receivedModel = string(data)
				}
				part.Close()
			}
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.TranscriptionResponse{Text: "ok"})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateTranscription(context.Background(), &models.TranscriptionRequest{
		Model:    "openai/whisper-1",
		FileData: []byte("audio"),
		FileName: "test.wav",
	})
	if err != nil {
		t.Fatalf("CreateTranscription error: %v", err)
	}
	if receivedModel != "whisper-1" {
		t.Errorf("model = %q, want whisper-1", receivedModel)
	}
}

func TestCreateTranscription_DefaultFileName(t *testing.T) {
	var gotFileName string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, params, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		mr := multipart.NewReader(r.Body, params["boundary"])
		for {
			part, err := mr.NextPart()
			if err != nil {
				break
			}
			if part.FormName() == "file" {
				gotFileName = part.FileName()
			}
			part.Close()
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.TranscriptionResponse{Text: "ok"})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateTranscription(context.Background(), &models.TranscriptionRequest{
		Model:    "whisper-1",
		FileData: []byte("audio"),
		// No FileName — should default to "audio.wav".
	})
	if err != nil {
		t.Fatalf("CreateTranscription error: %v", err)
	}
	if gotFileName != "audio.wav" {
		t.Errorf("filename = %q, want audio.wav", gotFileName)
	}
}

func TestCreateTranscription_OptionalFields(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, params, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		mr := multipart.NewReader(r.Body, params["boundary"])
		fields := make(map[string]string)
		for {
			part, err := mr.NextPart()
			if err != nil {
				break
			}
			if part.FormName() != "file" {
				data, _ := io.ReadAll(part)
				fields[part.FormName()] = string(data)
			}
			part.Close()
		}

		if fields["response_format"] != "verbose_json" {
			t.Errorf("response_format = %q, want verbose_json", fields["response_format"])
		}
		if fields["temperature"] != "0.5" {
			t.Errorf("temperature = %q, want 0.5", fields["temperature"])
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.TranscriptionResponse{Text: "ok"})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	temp := 0.5
	_, err := p.CreateTranscription(context.Background(), &models.TranscriptionRequest{
		Model:          "whisper-1",
		FileData:       []byte("audio"),
		FileName:       "test.mp3",
		ResponseFormat: "verbose_json",
		Temperature:    &temp,
	})
	if err != nil {
		t.Fatalf("CreateTranscription error: %v", err)
	}
}

func TestCreateTranscription_OmitsEmptyOptionalFields(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, params, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		mr := multipart.NewReader(r.Body, params["boundary"])
		fields := make(map[string]string)
		for {
			part, err := mr.NextPart()
			if err != nil {
				break
			}
			if part.FormName() != "file" {
				data, _ := io.ReadAll(part)
				fields[part.FormName()] = string(data)
			}
			part.Close()
		}

		// Only "model" should be present (no language, response_format, temperature).
		if _, ok := fields["language"]; ok {
			t.Error("language field should be omitted when empty")
		}
		if _, ok := fields["response_format"]; ok {
			t.Error("response_format field should be omitted when empty")
		}
		if _, ok := fields["temperature"]; ok {
			t.Error("temperature field should be omitted when nil")
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.TranscriptionResponse{Text: "ok"})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateTranscription(context.Background(), &models.TranscriptionRequest{
		Model:    "whisper-1",
		FileData: []byte("audio"),
		FileName: "test.wav",
	})
	if err != nil {
		t.Fatalf("CreateTranscription error: %v", err)
	}
}

func TestCreateTranscription_ProviderError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(models.ErrorResponse{
			Error: models.ErrorDetail{
				Type:    "invalid_request_error",
				Message: "invalid audio format",
			},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateTranscription(context.Background(), &models.TranscriptionRequest{
		Model:    "whisper-1",
		FileData: []byte("not-audio"),
		FileName: "bad.txt",
	})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Message != "invalid audio format" {
		t.Errorf("message = %q, want %q", apiErr.Message, "invalid audio format")
	}
}

// ─────────────────────────────────────────────────────────────
// ChatCompletion basic tests
// ─────────────────────────────────────────────────────────────

func TestChatCompletion_Success(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/v1/chat/completions" {
			t.Errorf("path = %s, want /v1/chat/completions", r.URL.Path)
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.ChatCompletionResponse{
			ID:    "chatcmpl-123",
			Model: "gpt-4o",
			Choices: []models.Choice{
				{
					Index: 0,
					Message: models.Message{
						Role:    "assistant",
						Content: json.RawMessage(`"Hello!"`),
					},
					FinishReason: "stop",
				},
			},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	resp, err := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model: "gpt-4o",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	})
	if err != nil {
		t.Fatalf("ChatCompletion error: %v", err)
	}
	if resp.ID != "chatcmpl-123" {
		t.Errorf("id = %q, want chatcmpl-123", resp.ID)
	}
	if len(resp.Choices) != 1 {
		t.Fatalf("choices = %d, want 1", len(resp.Choices))
	}
	if string(resp.Choices[0].Message.Content) != `"Hello!"` {
		t.Errorf("content = %s, want %q", resp.Choices[0].Message.Content, `"Hello!"`)
	}
}

func TestChatCompletion_ModelPassedThrough(t *testing.T) {
	// Upstream Groq, Together, Fireworks etc. use slash-prefixed model IDs
	// natively (groq/compound-mini, meta-llama/…, accounts/fireworks/…), so
	// the provider must pass req.Model through untouched.
	cases := []string{
		"gpt-4o",
		"groq/compound-mini",
		"meta-llama/llama-4-scout-17b-16e-instruct",
		"openai/gpt-oss-120b",
	}
	for _, model := range cases {
		t.Run(model, func(t *testing.T) {
			var receivedModel string
			ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var req models.ChatCompletionRequest
				json.NewDecoder(r.Body).Decode(&req)
				receivedModel = req.Model
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(models.ChatCompletionResponse{ID: "1", Choices: []models.Choice{{Message: models.Message{Content: json.RawMessage(`"ok"`)}}}})
			}))
			defer ts.Close()

			p := newTestProvider(t, ts.URL)
			_, err := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
				Model:    model,
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
			})
			if err != nil {
				t.Fatalf("error: %v", err)
			}
			if receivedModel != model {
				t.Errorf("model = %q, want %q", receivedModel, model)
			}
		})
	}
}

func TestResolveModelName(t *testing.T) {
	// resolveModelName is now a pass-through: upstream OpenAI-compat providers
	// (Groq, Together, Fireworks, …) use slash-prefixed model IDs natively,
	// so we never strip. See comment on resolveModelName.
	tests := []struct {
		input string
		want  string
	}{
		{"gpt-4o", "gpt-4o"},
		{"openai/gpt-4o", "openai/gpt-4o"},
		{"anthropic/claude-3-opus", "anthropic/claude-3-opus"},
		{"groq/compound-mini", "groq/compound-mini"},
		{"meta-llama/llama-4-scout-17b-16e-instruct", "meta-llama/llama-4-scout-17b-16e-instruct"},
		{"accounts/fireworks/models/llama-v3p1-70b-instruct", "accounts/fireworks/models/llama-v3p1-70b-instruct"},
		{"/leading-slash", "/leading-slash"},
		{"no-prefix", "no-prefix"},
	}
	for _, tc := range tests {
		got := resolveModelName(tc.input)
		if got != tc.want {
			t.Errorf("resolveModelName(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

func TestNewProvider_Defaults(t *testing.T) {
	p, err := New("test", config.ProviderConfig{
		BaseURL: "https://api.openai.com",
		APIKey:  "sk-test",
	})
	if err != nil {
		t.Fatalf("New error: %v", err)
	}
	if p.id != "test" {
		t.Errorf("id = %q, want test", p.id)
	}
	if p.baseURL != "https://api.openai.com" {
		t.Errorf("baseURL = %q, want https://api.openai.com", p.baseURL)
	}
	if cap(p.semaphore) != 100 {
		t.Errorf("semaphore cap = %d, want 100", cap(p.semaphore))
	}
}

// ─────────────────────────────────────────────────────────────
// CreateEmbedding tests
// ─────────────────────────────────────────────────────────────

func TestCreateEmbedding_Success(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/v1/embeddings" {
			t.Errorf("path = %s, want /v1/embeddings", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			t.Errorf("auth = %q, want %q", got, "Bearer test-key")
		}

		var req models.EmbeddingRequest
		json.NewDecoder(r.Body).Decode(&req)
		if req.Model != "text-embedding-3-small" {
			t.Errorf("model = %q, want text-embedding-3-small", req.Model)
		}

		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2,0.3]}],"model":"text-embedding-3-small","usage":{"prompt_tokens":5,"total_tokens":5}}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	resp, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "text-embedding-3-small",
		Input: json.RawMessage(`"hello world"`),
	})
	if err != nil {
		t.Fatalf("CreateEmbedding error: %v", err)
	}
	if resp.Object != "list" {
		t.Errorf("object = %q, want list", resp.Object)
	}
	if len(resp.Data) != 1 {
		t.Fatalf("data length = %d, want 1", len(resp.Data))
	}
	if resp.Data[0].Index != 0 {
		t.Errorf("index = %d, want 0", resp.Data[0].Index)
	}
	if resp.Usage == nil || resp.Usage.PromptTokens != 5 {
		t.Errorf("usage.prompt_tokens = %v, want 5", resp.Usage)
	}
}

func TestCreateEmbedding_ModelPrefixStripped(t *testing.T) {
	var receivedModel string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req models.EmbeddingRequest
		json.NewDecoder(r.Body).Decode(&req)
		receivedModel = req.Model
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"object":"list","data":[],"model":"m","usage":{"prompt_tokens":0,"total_tokens":0}}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "openai/text-embedding-3-large",
		Input: json.RawMessage(`"test"`),
	})
	if err != nil {
		t.Fatalf("error: %v", err)
	}
	if receivedModel != "text-embedding-3-large" {
		t.Errorf("model = %q, want text-embedding-3-large", receivedModel)
	}
}

func TestCreateEmbedding_ArrayInput(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1]},{"object":"embedding","index":1,"embedding":[0.2]}],"model":"m","usage":{"prompt_tokens":2,"total_tokens":2}}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	resp, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "text-embedding-3-small",
		Input: json.RawMessage(`["hello","world"]`),
	})
	if err != nil {
		t.Fatalf("error: %v", err)
	}
	if len(resp.Data) != 2 {
		t.Errorf("data length = %d, want 2", len(resp.Data))
	}
}

func TestCreateEmbedding_ProviderError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(models.ErrorResponse{
			Error: models.ErrorDetail{Type: "invalid_request_error", Message: "bad model"},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "bad-model",
		Input: json.RawMessage(`"test"`),
	})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Message != "bad model" {
		t.Errorf("message = %q, want %q", apiErr.Message, "bad model")
	}
}

// ─────────────────────────────────────────────────────────────
// Batch API tests
// ─────────────────────────────────────────────────────────────

func TestSubmitBatch_Success(t *testing.T) {
	var fileUploadCalled, batchCreateCalled bool
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/files" && r.Method == "POST":
			fileUploadCalled = true
			// Verify multipart with purpose=batch.
			if err := r.ParseMultipartForm(10 * 1024 * 1024); err != nil {
				t.Errorf("parse multipart: %v", err)
			}
			if got := r.FormValue("purpose"); got != "batch" {
				t.Errorf("purpose = %q, want batch", got)
			}
			// Verify file field exists.
			file, _, err := r.FormFile("file")
			if err != nil {
				t.Errorf("missing file field: %v", err)
			} else {
				file.Close()
			}

			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]string{"id": "file-abc123"})

		case r.URL.Path == "/v1/batches" && r.Method == "POST":
			batchCreateCalled = true
			var req struct {
				InputFileID      string `json:"input_file_id"`
				Endpoint         string `json:"endpoint"`
				CompletionWindow string `json:"completion_window"`
			}
			json.NewDecoder(r.Body).Decode(&req)
			if req.InputFileID != "file-abc123" {
				t.Errorf("input_file_id = %q, want file-abc123", req.InputFileID)
			}
			if req.Endpoint != "/v1/chat/completions" {
				t.Errorf("endpoint = %q, want /v1/chat/completions", req.Endpoint)
			}
			if req.CompletionWindow != "24h" {
				t.Errorf("completion_window = %q, want 24h", req.CompletionWindow)
			}

			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]string{"id": "batch_xyz"})

		default:
			w.WriteHeader(404)
		}
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	batchID, err := p.SubmitBatch(context.Background(), []models.BatchRequest{
		{
			CustomID: "req-1",
			Method:   "POST",
			URL:      "/v1/chat/completions",
			Body: models.ChatCompletionRequest{
				Model:    "gpt-4o",
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hello"`)}},
			},
		},
		{
			CustomID: "req-2",
			Method:   "POST",
			URL:      "/v1/chat/completions",
			Body: models.ChatCompletionRequest{
				Model:    "openai/gpt-4o",
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"World"`)}},
			},
		},
	})
	if err != nil {
		t.Fatalf("SubmitBatch error: %v", err)
	}
	if batchID != "batch_xyz" {
		t.Errorf("batchID = %q, want batch_xyz", batchID)
	}
	if !fileUploadCalled {
		t.Error("file upload was not called")
	}
	if !batchCreateCalled {
		t.Error("batch create was not called")
	}
}

func TestSubmitBatch_FileUploadError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(models.ErrorResponse{
			Error: models.ErrorDetail{Type: "invalid_request_error", Message: "file too large"},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.SubmitBatch(context.Background(), []models.BatchRequest{
		{CustomID: "r1", Body: models.ChatCompletionRequest{Model: "gpt-4o"}},
	})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Message != "file too large" {
		t.Errorf("message = %q, want %q", apiErr.Message, "file too large")
	}
}

func TestGetBatch_Success(t *testing.T) {
	completedAt := int64(1700000100)
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			t.Errorf("method = %s, want GET", r.Method)
		}
		if r.URL.Path != "/v1/batches/batch_123" {
			t.Errorf("path = %s, want /v1/batches/batch_123", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			t.Errorf("auth = %q, want %q", got, "Bearer test-key")
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"id":             "batch_123",
			"status":         "completed",
			"output_file_id": "file-out-456",
			"error_file_id":  "",
			"created_at":     1700000000,
			"completed_at":   completedAt,
			"request_counts": map[string]int{
				"total":     10,
				"completed": 9,
				"failed":    1,
			},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	status, err := p.GetBatch(context.Background(), "batch_123")
	if err != nil {
		t.Fatalf("GetBatch error: %v", err)
	}
	if status.ID != "batch_123" {
		t.Errorf("id = %q, want batch_123", status.ID)
	}
	if status.Status != models.BatchStatusCompleted {
		t.Errorf("status = %q, want %q", status.Status, models.BatchStatusCompleted)
	}
	if status.Total != 10 {
		t.Errorf("total = %d, want 10", status.Total)
	}
	if status.Completed != 9 {
		t.Errorf("completed = %d, want 9", status.Completed)
	}
	if status.Failed != 1 {
		t.Errorf("failed = %d, want 1", status.Failed)
	}
	if status.OutputFileID != "file-out-456" {
		t.Errorf("output_file_id = %q, want file-out-456", status.OutputFileID)
	}
}

func TestGetBatch_StatusMapping(t *testing.T) {
	tests := []struct {
		oaiStatus  string
		wantStatus string
	}{
		{"validating", models.BatchStatusProcessing},
		{"in_progress", models.BatchStatusProcessing},
		{"completed", models.BatchStatusCompleted},
		{"failed", models.BatchStatusFailed},
		{"cancelled", models.BatchStatusCancelled},
		{"cancelling", models.BatchStatusCancelled},
		{"expired", models.BatchStatusExpired},
		{"unknown", models.BatchStatusQueued},
	}

	for _, tc := range tests {
		t.Run(tc.oaiStatus, func(t *testing.T) {
			ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(map[string]interface{}{
					"id":             "b1",
					"status":         tc.oaiStatus,
					"created_at":     1700000000,
					"request_counts": map[string]int{"total": 1, "completed": 0, "failed": 0},
				})
			}))
			defer ts.Close()

			p := newTestProvider(t, ts.URL)
			status, err := p.GetBatch(context.Background(), "b1")
			if err != nil {
				t.Fatalf("error: %v", err)
			}
			if status.Status != tc.wantStatus {
				t.Errorf("status = %q, want %q", status.Status, tc.wantStatus)
			}
		})
	}
}

func TestGetBatch_NotFound(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(models.ErrorResponse{
			Error: models.ErrorDetail{Type: "not_found_error", Message: "batch not found"},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.GetBatch(context.Background(), "nonexistent")
	if err == nil {
		t.Fatal("expected error, got nil")
	}
}

func TestCancelBatch_Success(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/v1/batches/batch_123/cancel" {
			t.Errorf("path = %s, want /v1/batches/batch_123/cancel", r.URL.Path)
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"id":     "batch_123",
			"status": "cancelling",
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	err := p.CancelBatch(context.Background(), "batch_123")
	if err != nil {
		t.Fatalf("CancelBatch error: %v", err)
	}
}

func TestCancelBatch_NotFound(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(models.ErrorResponse{
			Error: models.ErrorDetail{Type: "not_found_error", Message: "batch not found"},
		})
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	err := p.CancelBatch(context.Background(), "nonexistent")
	if err == nil {
		t.Fatal("expected error, got nil")
	}
}

func TestMapOpenAIBatchStatus(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"validating", models.BatchStatusProcessing},
		{"in_progress", models.BatchStatusProcessing},
		{"completed", models.BatchStatusCompleted},
		{"failed", models.BatchStatusFailed},
		{"cancelled", models.BatchStatusCancelled},
		{"cancelling", models.BatchStatusCancelled},
		{"expired", models.BatchStatusExpired},
		{"anything_else", models.BatchStatusQueued},
	}
	for _, tc := range tests {
		got := mapOpenAIBatchStatus(tc.input)
		if got != tc.want {
			t.Errorf("mapOpenAIBatchStatus(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

func TestNewProvider_TrailingSlashStripped(t *testing.T) {
	p, err := New("test", config.ProviderConfig{
		BaseURL: "https://api.openai.com/",
		APIKey:  "sk-test",
	})
	if err != nil {
		t.Fatalf("New error: %v", err)
	}
	if p.baseURL != "https://api.openai.com" {
		t.Errorf("baseURL = %q, want no trailing slash", p.baseURL)
	}
}
