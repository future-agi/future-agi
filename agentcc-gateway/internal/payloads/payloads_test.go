package payloads

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// An absent payload must be absent, not the literal null — a nil pointer in an
// `any` is not == nil, so this is easy to get wrong and invisible once wrong.
func TestAbsentPayloadsAreOmitted(t *testing.T) {
	for _, endpoint := range []string{"embedding", "image", "rerank", "search", "ocr", "transcription", "translation"} {
		rc := &models.RequestContext{EndpointType: endpoint, Metadata: map[string]string{}}
		req, resp := ForEndpoint(rc)
		if string(req) == "null" || string(resp) == "null" {
			t.Errorf("%s: emitted a null literal instead of omitting (req=%q resp=%q)", endpoint, req, resp)
		}
	}
}

func TestChatFallsThrough(t *testing.T) {
	rc := &models.RequestContext{EndpointType: "chat", Metadata: map[string]string{}}
	req, resp := ForEndpoint(rc)
	if req != nil || resp != nil {
		t.Error("chat should be left to the caller's own marshalling")
	}
}

// The prompt is the point of an image request; the base64 result is megabytes
// nobody reads. Keep the first, summarize the second.
func TestImageKeepsPromptAndElidesBase64(t *testing.T) {
	big := strings.Repeat("A", 400000)
	rc := &models.RequestContext{
		EndpointType:  "image",
		Metadata:      map[string]string{},
		ImageRequest:  &models.ImageRequest{Model: "dall-e-3", Prompt: "a red bicycle"},
		ImageResponse: &models.ImageResponse{Data: []models.ImageData{{B64JSON: big, RevisedPrompt: "a red racing bicycle"}}},
	}
	req, resp := ForEndpoint(rc)

	if !strings.Contains(string(req), "a red bicycle") {
		t.Errorf("prompt missing from request payload: %s", req)
	}
	if strings.Contains(string(resp), big) {
		t.Error("base64 image carried into the payload")
	}
	if len(resp) > 1000 {
		t.Errorf("response payload is %d bytes; the image was not elided", len(resp))
	}
	if !strings.Contains(string(resp), "b64_json_bytes") {
		t.Error("elided image should record its size")
	}
	if !strings.Contains(string(resp), "a red racing bicycle") {
		t.Error("revised prompt should survive — it is model output")
	}
}

// Vectors are the one part of an embedding response nobody reads back, and the
// largest. The input text is what matters.
func TestEmbeddingKeepsInputAndElidesVectors(t *testing.T) {
	vec, _ := json.Marshal(make([]float64, 3072))
	rc := &models.RequestContext{
		EndpointType:     "embedding",
		Metadata:         map[string]string{},
		EmbeddingRequest: &models.EmbeddingRequest{Model: "text-embedding-3-large", Input: json.RawMessage(`"embed this sentence"`)},
		EmbeddingResponse: &models.EmbeddingResponse{
			Object: "list", Model: "text-embedding-3-large",
			Data: []models.EmbeddingData{{Object: "embedding", Index: 0, Embedding: vec}},
		},
	}
	req, resp := ForEndpoint(rc)

	if !strings.Contains(string(req), "embed this sentence") {
		t.Errorf("input text missing: %s", req)
	}
	if strings.Contains(string(resp), "0,0,0,0") {
		t.Error("embedding vector carried into the payload")
	}
	if !strings.Contains(string(resp), "embedding_bytes") {
		t.Error("elided vector should record its size")
	}
}

// OCR's result is the extracted markdown; the page images are the input
// rendered back and can be large.
func TestOCRKeepsMarkdownAndElidesPageImages(t *testing.T) {
	big := strings.Repeat("B", 300000)
	rc := &models.RequestContext{
		EndpointType: "ocr",
		Metadata:     map[string]string{},
		OCRRequest:   &models.OCRRequest{Model: "ocr-1", Document: models.OCRDocument{Type: "document_url", DocumentURL: "https://x/y.pdf"}},
		OCRResponse: &models.OCRResponse{Pages: []models.OCRPage{
			{Index: 0, Markdown: "# Invoice\ntotal 42", Images: []models.OCRImage{{ImageBase64: big}}},
		}},
	}
	req, resp := ForEndpoint(rc)

	if !strings.Contains(string(req), "y.pdf") {
		t.Errorf("document reference missing: %s", req)
	}
	if strings.Contains(string(resp), big) {
		t.Error("page image carried into the payload")
	}
	if !strings.Contains(string(resp), "total 42") {
		t.Error("extracted markdown missing — that is the OCR result")
	}
}

// Rerank and search are text end to end; nothing to elide.
func TestTextEndpointsCarryContent(t *testing.T) {
	rc := &models.RequestContext{
		EndpointType:  "rerank",
		Metadata:      map[string]string{},
		RerankRequest: &models.RerankRequest{Model: "rerank-1", Query: "best bicycle", Documents: []string{"a doc"}},
	}
	req, _ := ForEndpoint(rc)
	if !strings.Contains(string(req), "best bicycle") || !strings.Contains(string(req), "a doc") {
		t.Errorf("rerank query/documents missing: %s", req)
	}
}

// Audio bytes never travel; the request is described by size instead.
func TestTranscriptionDescribesAudioWithoutCarryingIt(t *testing.T) {
	rc := &models.RequestContext{
		EndpointType:     "transcription",
		Metadata:         map[string]string{},
		TranscriptionReq: &models.TranscriptionRequest{Model: "whisper-1", FileName: "call.mp3", FileData: make([]byte, 5000)},
	}
	req, _ := ForEndpoint(rc)
	if strings.Contains(string(req), "file_data") {
		t.Error("audio bytes carried into the payload")
	}
	if !strings.Contains(string(req), "file_size_bytes") || !strings.Contains(string(req), "call.mp3") {
		t.Errorf("audio not described: %s", req)
	}
}
