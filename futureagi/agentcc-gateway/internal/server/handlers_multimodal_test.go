package server

import (
	"bytes"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ---------------------------------------------------------------------------
// Embedding Handler Tests
// ---------------------------------------------------------------------------

func TestMultimodalEmbedding_MissingModel(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Empty JSON body → missing model.
	req := httptest.NewRequest("POST", "/v1/embeddings", bytes.NewBufferString(`{}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_model" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_model")
	}
}

func TestMultimodalEmbedding_InvalidJSON(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	req := httptest.NewRequest("POST", "/v1/embeddings", bytes.NewBufferString(`not json`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "invalid_json" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "invalid_json")
	}
}

func TestMultimodalEmbedding_ProviderNotSupported(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// OpenAI provider now implements EmbeddingProvider; the mock doesn't handle
	// the endpoint, so the upstream returns a non-JSON 404 → gateway 502.
	body := `{"model":"gpt-4o","input":"Hello world"}`
	req := httptest.NewRequest("POST", "/v1/embeddings", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadGateway, w.Body.String())
	}
}

func TestMultimodalEmbedding_EmptyBody(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Completely empty body → invalid JSON.
	req := httptest.NewRequest("POST", "/v1/embeddings", bytes.NewBufferString(``))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
	}
}

// ---------------------------------------------------------------------------
// Image Handler Tests
// ---------------------------------------------------------------------------

func TestMultimodalImage_MissingModel(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	body := `{"prompt":"a cat"}`
	req := httptest.NewRequest("POST", "/v1/images/generations", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_model" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_model")
	}
}

func TestMultimodalImage_MissingPrompt(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	body := `{"model":"dall-e-3"}`
	req := httptest.NewRequest("POST", "/v1/images/generations", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_prompt" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_prompt")
	}
}

func TestMultimodalImage_InvalidJSON(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	req := httptest.NewRequest("POST", "/v1/images/generations", bytes.NewBufferString(`{broken`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "invalid_json" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "invalid_json")
	}
}

func TestMultimodalImage_ProviderNotSupported(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// OpenAI provider now implements ImageProvider; the mock doesn't handle
	// the endpoint, so the upstream returns a non-JSON 404 → gateway 502.
	body := `{"model":"gpt-4o","prompt":"a cat sitting on a rainbow"}`
	req := httptest.NewRequest("POST", "/v1/images/generations", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadGateway, w.Body.String())
	}
}

func TestMultimodalImage_EmptyBody(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Empty JSON → missing model.
	req := httptest.NewRequest("POST", "/v1/images/generations", bytes.NewBufferString(`{}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_model" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_model")
	}
}

// ---------------------------------------------------------------------------
// Rerank Handler Tests
// ---------------------------------------------------------------------------

func TestMultimodalRerank_MissingModel(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	body := `{"query":"search query","documents":["doc1","doc2"]}`
	req := httptest.NewRequest("POST", "/v1/rerank", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_model" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_model")
	}
}

func TestMultimodalRerank_MissingQuery(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	body := `{"model":"rerank-english-v3.0","documents":["doc1","doc2"]}`
	req := httptest.NewRequest("POST", "/v1/rerank", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_query" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_query")
	}
}

func TestMultimodalRerank_MissingDocuments(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	body := `{"model":"rerank-english-v3.0","query":"search query"}`
	req := httptest.NewRequest("POST", "/v1/rerank", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_documents" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_documents")
	}
}

func TestMultimodalRerank_EmptyDocuments(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	body := `{"model":"rerank-english-v3.0","query":"search query","documents":[]}`
	req := httptest.NewRequest("POST", "/v1/rerank", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_documents" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_documents")
	}
}

func TestMultimodalRerank_InvalidJSON(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	req := httptest.NewRequest("POST", "/v1/rerank", bytes.NewBufferString(`{"bad`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "invalid_json" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "invalid_json")
	}
}

func TestMultimodalRerank_ProviderNotSupported(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// gpt-4o provider does not implement RerankProvider → 501.
	body := `{"model":"gpt-4o","query":"search query","documents":["doc1","doc2"]}`
	req := httptest.NewRequest("POST", "/v1/rerank", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusNotImplemented {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusNotImplemented, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "not_supported" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "not_supported")
	}
}

// ---------------------------------------------------------------------------
// Speech Handler Tests
// ---------------------------------------------------------------------------

func TestMultimodalSpeech_MissingModel(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	body := `{"input":"Hello world","voice":"alloy"}`
	req := httptest.NewRequest("POST", "/v1/audio/speech", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_model" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_model")
	}
}

func TestMultimodalSpeech_MissingInput(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	body := `{"model":"tts-1","voice":"alloy"}`
	req := httptest.NewRequest("POST", "/v1/audio/speech", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_input" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_input")
	}
}

func TestMultimodalSpeech_MissingVoice(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	body := `{"model":"tts-1","input":"Hello world"}`
	req := httptest.NewRequest("POST", "/v1/audio/speech", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_voice" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_voice")
	}
}

func TestMultimodalSpeech_InvalidJSON(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	req := httptest.NewRequest("POST", "/v1/audio/speech", bytes.NewBufferString(`invalid`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "invalid_json" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "invalid_json")
	}
}

func TestMultimodalSpeech_ProviderNotSupported(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// OpenAI provider now implements SpeechProvider; the mock doesn't handle
	// the endpoint, so the upstream returns a non-JSON 404 → gateway 502.
	body := `{"model":"gpt-4o","input":"Hello world","voice":"alloy"}`
	req := httptest.NewRequest("POST", "/v1/audio/speech", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadGateway, w.Body.String())
	}
}

func TestMultimodalSpeech_EmptyBody(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Empty JSON → missing model (first validation).
	req := httptest.NewRequest("POST", "/v1/audio/speech", bytes.NewBufferString(`{}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_model" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_model")
	}
}

// ---------------------------------------------------------------------------
// Transcription Handler Tests
// ---------------------------------------------------------------------------

func TestMultimodalTranscription_NonMultipartBody(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Sending a plain JSON body instead of multipart → parse error.
	req := httptest.NewRequest("POST", "/v1/audio/transcriptions", bytes.NewBufferString(`{"model":"whisper-1"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "invalid_form" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "invalid_form")
	}
}

func TestMultimodalTranscription_MissingFile(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Multipart form without the "file" field → missing file error.
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)
	writer.WriteField("model", "whisper-1")
	writer.Close()

	req := httptest.NewRequest("POST", "/v1/audio/transcriptions", &buf)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_file" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_file")
	}
}

func TestMultimodalTranscription_MissingModel(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Multipart form with file but no model field → missing model.
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)
	part, err := writer.CreateFormFile("file", "audio.mp3")
	if err != nil {
		t.Fatalf("creating form file: %v", err)
	}
	part.Write([]byte("fake audio data"))
	writer.Close()

	req := httptest.NewRequest("POST", "/v1/audio/transcriptions", &buf)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Code != "missing_model" {
		t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_model")
	}
}

func TestMultimodalTranscription_ProviderNotSupported(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// OpenAI provider now implements TranscriptionProvider; the mock doesn't
	// handle the endpoint, so the upstream returns a non-JSON 404 → gateway 502.
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)
	part, err := writer.CreateFormFile("file", "audio.mp3")
	if err != nil {
		t.Fatalf("creating form file: %v", err)
	}
	part.Write([]byte("fake audio data"))
	writer.WriteField("model", "gpt-4o")
	writer.Close()

	req := httptest.NewRequest("POST", "/v1/audio/transcriptions", &buf)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadGateway, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// Table-Driven Validation Tests (all JSON-body multimodal endpoints)
// ---------------------------------------------------------------------------

func TestMultimodalValidation_TableDriven(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	tests := []struct {
		name       string
		endpoint   string
		body       string
		wantStatus int
		wantCode   string
	}{
		// Embedding validations.
		{
			name:       "embedding/missing_model",
			endpoint:   "/v1/embeddings",
			body:       `{"input":"test"}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_model",
		},
		{
			name:       "embedding/invalid_json",
			endpoint:   "/v1/embeddings",
			body:       `{bad`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "invalid_json",
		},
		{
			name:       "embedding/provider_not_supported",
			endpoint:   "/v1/embeddings",
			body:       `{"model":"gpt-4o","input":"test"}`,
			wantStatus: http.StatusBadGateway, // OpenAI now implements EmbeddingProvider; mock returns 404
			wantCode:   "provider_404",
		},

		// Image validations.
		{
			name:       "image/missing_model",
			endpoint:   "/v1/images/generations",
			body:       `{"prompt":"a cat"}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_model",
		},
		{
			name:       "image/missing_prompt",
			endpoint:   "/v1/images/generations",
			body:       `{"model":"dall-e-3"}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_prompt",
		},
		{
			name:       "image/invalid_json",
			endpoint:   "/v1/images/generations",
			body:       `nope`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "invalid_json",
		},
		{
			name:       "image/provider_not_supported",
			endpoint:   "/v1/images/generations",
			body:       `{"model":"gpt-4o","prompt":"cat"}`,
			wantStatus: http.StatusBadGateway, // OpenAI now implements ImageProvider; mock returns 404
			wantCode:   "provider_404",
		},

		// Rerank validations.
		{
			name:       "rerank/missing_model",
			endpoint:   "/v1/rerank",
			body:       `{"query":"q","documents":["d"]}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_model",
		},
		{
			name:       "rerank/missing_query",
			endpoint:   "/v1/rerank",
			body:       `{"model":"m","documents":["d"]}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_query",
		},
		{
			name:       "rerank/missing_documents",
			endpoint:   "/v1/rerank",
			body:       `{"model":"m","query":"q"}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_documents",
		},
		{
			name:       "rerank/empty_documents",
			endpoint:   "/v1/rerank",
			body:       `{"model":"m","query":"q","documents":[]}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_documents",
		},
		{
			name:       "rerank/invalid_json",
			endpoint:   "/v1/rerank",
			body:       `xxx`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "invalid_json",
		},
		{
			name:       "rerank/provider_not_supported",
			endpoint:   "/v1/rerank",
			body:       `{"model":"gpt-4o","query":"q","documents":["d1"]}`,
			wantStatus: http.StatusNotImplemented,
			wantCode:   "not_supported",
		},

		// Speech validations.
		{
			name:       "speech/missing_model",
			endpoint:   "/v1/audio/speech",
			body:       `{"input":"hi","voice":"alloy"}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_model",
		},
		{
			name:       "speech/missing_input",
			endpoint:   "/v1/audio/speech",
			body:       `{"model":"tts-1","voice":"alloy"}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_input",
		},
		{
			name:       "speech/missing_voice",
			endpoint:   "/v1/audio/speech",
			body:       `{"model":"tts-1","input":"hi"}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "missing_voice",
		},
		{
			name:       "speech/invalid_json",
			endpoint:   "/v1/audio/speech",
			body:       `>>>`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "invalid_json",
		},
		{
			name:       "speech/provider_not_supported",
			endpoint:   "/v1/audio/speech",
			body:       `{"model":"gpt-4o","input":"hi","voice":"alloy"}`,
			wantStatus: http.StatusBadGateway, // OpenAI now implements SpeechProvider; mock returns 404
			wantCode:   "provider_404",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest("POST", tt.endpoint, bytes.NewBufferString(tt.body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			if w.Code != tt.wantStatus {
				t.Errorf("status = %d, want %d. Body: %s", w.Code, tt.wantStatus, w.Body.String())
			}

			var errResp models.ErrorResponse
			if err := json.Unmarshal(w.Body.Bytes(), &errResp); err != nil {
				t.Fatalf("parsing error response: %v. Body: %s", err, w.Body.String())
			}
			if errResp.Error.Code != tt.wantCode {
				t.Errorf("error code = %q, want %q", errResp.Error.Code, tt.wantCode)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Error Response Format Tests
// ---------------------------------------------------------------------------

func TestMultimodalErrors_ResponseFormat(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Verify that error responses follow the OpenAI error format with proper structure.
	endpoints := []struct {
		path string
		body string
	}{
		{"/v1/embeddings", `{}`},
		{"/v1/images/generations", `{}`},
		{"/v1/audio/speech", `{}`},
		{"/v1/rerank", `{}`},
	}

	for _, ep := range endpoints {
		t.Run(ep.path, func(t *testing.T) {
			req := httptest.NewRequest("POST", ep.path, bytes.NewBufferString(ep.body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			// All should be 400 (missing model is the first validation).
			if w.Code != http.StatusBadRequest {
				t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
			}

			// Verify Content-Type is JSON.
			ct := w.Header().Get("Content-Type")
			if ct != "application/json" {
				t.Errorf("Content-Type = %q, want application/json", ct)
			}

			// Verify error response shape.
			var raw map[string]json.RawMessage
			if err := json.Unmarshal(w.Body.Bytes(), &raw); err != nil {
				t.Fatalf("not valid JSON: %v", err)
			}
			if _, ok := raw["error"]; !ok {
				t.Error("response should have 'error' field")
			}

			var errResp models.ErrorResponse
			json.Unmarshal(w.Body.Bytes(), &errResp)
			if errResp.Error.Message == "" {
				t.Error("error message should not be empty")
			}
			if errResp.Error.Type == "" {
				t.Error("error type should not be empty")
			}
			if errResp.Error.Code == "" {
				t.Error("error code should not be empty")
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Provider 501 Response Format Tests
// ---------------------------------------------------------------------------

func TestMultimodalProviderNotSupported_ResponseFormat(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Only test interfaces NOT implemented by OpenAI provider.
	// Image, Speech, Transcription, and Embedding are now implemented; rerank is not.
	tests := []struct {
		name     string
		endpoint string
		body     string
		wantMsg  string
	}{
		{
			name:     "rerank",
			endpoint: "/v1/rerank",
			body:     `{"model":"gpt-4o","query":"q","documents":["d"]}`,
			wantMsg:  "does not support reranking",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest("POST", tt.endpoint, bytes.NewBufferString(tt.body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			if w.Code != http.StatusNotImplemented {
				t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusNotImplemented, w.Body.String())
			}

			var errResp models.ErrorResponse
			json.Unmarshal(w.Body.Bytes(), &errResp)
			if errResp.Error.Code != "not_supported" {
				t.Errorf("error code = %q, want %q", errResp.Error.Code, "not_supported")
			}
			if errResp.Error.Type != models.ErrTypeServer {
				t.Errorf("error type = %q, want %q", errResp.Error.Type, models.ErrTypeServer)
			}

			// Verify the message contains the expected capability description.
			if !bytes.Contains([]byte(errResp.Error.Message), []byte(tt.wantMsg)) {
				t.Errorf("error message %q should contain %q", errResp.Error.Message, tt.wantMsg)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Transcription Multipart Validation Tests (table-driven)
// ---------------------------------------------------------------------------

func TestMultimodalTranscription_Validation(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	t.Run("non_multipart_body", func(t *testing.T) {
		req := httptest.NewRequest("POST", "/v1/audio/transcriptions", bytes.NewBufferString(`hello`))
		req.Header.Set("Content-Type", "text/plain")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		if w.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
		}
	})

	t.Run("multipart_missing_file", func(t *testing.T) {
		var buf bytes.Buffer
		writer := multipart.NewWriter(&buf)
		writer.WriteField("model", "whisper-1")
		writer.Close()

		req := httptest.NewRequest("POST", "/v1/audio/transcriptions", &buf)
		req.Header.Set("Content-Type", writer.FormDataContentType())
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		if w.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
		}

		var errResp models.ErrorResponse
		json.Unmarshal(w.Body.Bytes(), &errResp)
		if errResp.Error.Code != "missing_file" {
			t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_file")
		}
	})

	t.Run("multipart_missing_model", func(t *testing.T) {
		var buf bytes.Buffer
		writer := multipart.NewWriter(&buf)
		part, _ := writer.CreateFormFile("file", "test.mp3")
		part.Write([]byte("fake audio"))
		writer.Close()

		req := httptest.NewRequest("POST", "/v1/audio/transcriptions", &buf)
		req.Header.Set("Content-Type", writer.FormDataContentType())
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		if w.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadRequest, w.Body.String())
		}

		var errResp models.ErrorResponse
		json.Unmarshal(w.Body.Bytes(), &errResp)
		if errResp.Error.Code != "missing_model" {
			t.Errorf("error code = %q, want %q", errResp.Error.Code, "missing_model")
		}
	})

	t.Run("provider_not_supported", func(t *testing.T) {
		// OpenAI provider now implements TranscriptionProvider; the mock doesn't
		// handle the endpoint, so the upstream returns a non-JSON 404 → gateway 502.
		var buf bytes.Buffer
		writer := multipart.NewWriter(&buf)
		part, _ := writer.CreateFormFile("file", "audio.mp3")
		part.Write([]byte("fake audio"))
		writer.WriteField("model", "gpt-4o")
		writer.Close()

		req := httptest.NewRequest("POST", "/v1/audio/transcriptions", &buf)
		req.Header.Set("Content-Type", writer.FormDataContentType())
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		if w.Code != http.StatusBadGateway {
			t.Errorf("status = %d, want %d. Body: %s", w.Code, http.StatusBadGateway, w.Body.String())
		}
	})
}

// ---------------------------------------------------------------------------
// Speech Validation Order Tests
// ---------------------------------------------------------------------------

func TestMultimodalSpeech_ValidationOrder(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Verify that validation happens in order: model, then input, then voice.
	// With only input, model should be checked first.
	t.Run("model_checked_first", func(t *testing.T) {
		body := `{"input":"hi"}`
		req := httptest.NewRequest("POST", "/v1/audio/speech", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		var errResp models.ErrorResponse
		json.Unmarshal(w.Body.Bytes(), &errResp)
		if errResp.Error.Code != "missing_model" {
			t.Errorf("error code = %q, want missing_model (should be checked first)", errResp.Error.Code)
		}
	})

	// With model but missing both input and voice, input should be checked before voice.
	t.Run("input_checked_before_voice", func(t *testing.T) {
		body := `{"model":"tts-1"}`
		req := httptest.NewRequest("POST", "/v1/audio/speech", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		var errResp models.ErrorResponse
		json.Unmarshal(w.Body.Bytes(), &errResp)
		if errResp.Error.Code != "missing_input" {
			t.Errorf("error code = %q, want missing_input (should be checked before voice)", errResp.Error.Code)
		}
	})
}

// ---------------------------------------------------------------------------
// Image Validation Order Tests
// ---------------------------------------------------------------------------

func TestMultimodalImage_ValidationOrder(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// With only prompt, model should be checked first.
	t.Run("model_checked_first", func(t *testing.T) {
		body := `{"prompt":"a cat"}`
		req := httptest.NewRequest("POST", "/v1/images/generations", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		var errResp models.ErrorResponse
		json.Unmarshal(w.Body.Bytes(), &errResp)
		if errResp.Error.Code != "missing_model" {
			t.Errorf("error code = %q, want missing_model (should be checked first)", errResp.Error.Code)
		}
	})
}

// ---------------------------------------------------------------------------
// Rerank Validation Order Tests
// ---------------------------------------------------------------------------

func TestMultimodalRerank_ValidationOrder(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Model should be checked first, then query, then documents.
	t.Run("model_checked_first", func(t *testing.T) {
		body := `{"query":"q","documents":["d"]}`
		req := httptest.NewRequest("POST", "/v1/rerank", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		var errResp models.ErrorResponse
		json.Unmarshal(w.Body.Bytes(), &errResp)
		if errResp.Error.Code != "missing_model" {
			t.Errorf("error code = %q, want missing_model (should be checked first)", errResp.Error.Code)
		}
	})

	t.Run("query_checked_before_documents", func(t *testing.T) {
		body := `{"model":"m"}`
		req := httptest.NewRequest("POST", "/v1/rerank", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		var errResp models.ErrorResponse
		json.Unmarshal(w.Body.Bytes(), &errResp)
		if errResp.Error.Code != "missing_query" {
			t.Errorf("error code = %q, want missing_query (should be checked before documents)", errResp.Error.Code)
		}
	})
}

// ---------------------------------------------------------------------------
// Unknown Model Tests (all multimodal endpoints)
// ---------------------------------------------------------------------------

func TestMultimodalUnknownModel(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	tests := []struct {
		name     string
		endpoint string
		body     string
	}{
		{
			name:     "embedding_unknown_model",
			endpoint: "/v1/embeddings",
			body:     `{"model":"nonexistent-model-xyz","input":"test"}`,
		},
		{
			name:     "image_unknown_model",
			endpoint: "/v1/images/generations",
			body:     `{"model":"nonexistent-model-xyz","prompt":"cat"}`,
		},
		{
			name:     "speech_unknown_model",
			endpoint: "/v1/audio/speech",
			body:     `{"model":"nonexistent-model-xyz","input":"hi","voice":"alloy"}`,
		},
		{
			name:     "rerank_unknown_model",
			endpoint: "/v1/rerank",
			body:     `{"model":"nonexistent-model-xyz","query":"q","documents":["d"]}`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest("POST", tt.endpoint, bytes.NewBufferString(tt.body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			// Unknown models handled by the mock return a non-JSON 404, which may
			// surface as: 404 (model not found in registry), 501 (provider doesn't
			// implement the interface), or 502 (upstream returned non-JSON error
			// body and ErrUpstreamProvider defaults to 502).
			if w.Code != http.StatusNotFound && w.Code != http.StatusNotImplemented && w.Code != http.StatusBadGateway {
				t.Errorf("status = %d, want 404, 501, or 502 for unknown model. Body: %s", w.Code, w.Body.String())
			}
		})
	}
}
