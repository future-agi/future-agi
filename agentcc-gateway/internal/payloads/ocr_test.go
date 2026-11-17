package payloads

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

const (
	ocrDocURL   = "https://bank.example/statements/8812.pdf?sig=Zm9vYmFy"
	ocrSecret   = "Patient: Jane Doe, SSN 123-45-6789"
	ocrAnnotKey = "diagnosis_code"
)

// ocrPayloadRC is an OCR request for a 3-page medical document, decoded from
// the wire shapes, served by mistral; its content mode is full.
func ocrPayloadRC(t *testing.T) *models.RequestContext {
	t.Helper()
	var req models.OCRRequest
	if err := json.Unmarshal([]byte(`{"model":"mistral-ocr-4-0","document":{"type":"document_url","document_url":"`+ocrDocURL+`"},"pages":"0-2","document_annotation_format":{"type":"json_schema"}}`), &req); err != nil {
		t.Fatal(err)
	}
	var resp models.OCRResponse
	body := `{"model":"mistral-ocr-4-0","pages":[` +
		`{"index":0,"markdown":"` + ocrSecret + `","images":[{"image_base64":"` + strings.Repeat("Q", 4096) + `"}]},` +
		`{"index":1,"markdown":"page two"},{"index":2,"markdown":"page three"}],` +
		`"document_annotation":"{\"` + ocrAnnotKey + `\":\"F32\"}",` +
		`"usage_info":{"pages_processed":3,"doc_size_bytes":48213}}`
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatal(err)
	}
	return &models.RequestContext{
		EndpointType: "ocr",
		Provider:     "mistral",
		Metadata:     map[string]string{},
		Privacy:      models.Privacy{ContentMode: models.ContentModeFull, Source: models.PrivacySourceDefault},
		OCRRequest:   &req,
		OCRResponse:  &resp,
	}
}

// OCR payloads are metadata by default, even under the full content mode:
// pages, bytes, provider and a hash of the document reference, never the
// document, its URL, its extracted text or its annotation (IDs, invoices,
// medical records). Only the org's opt-in, under full, adds the text.
func TestOCRMetadataOnlyByDefault(t *testing.T) {
	rc := ocrPayloadRC(t)
	req, resp := ForEndpoint(rc)
	all := string(req) + string(resp)

	for _, leaked := range []string{ocrSecret, "Jane Doe", ocrDocURL, "bank.example", ocrAnnotKey, "page two", "QQQQ"} {
		if strings.Contains(all, leaked) {
			t.Errorf("payload carries %q:\nrequest  %s\nresponse %s", leaked, req, resp)
		}
	}

	sum := sha256.Sum256([]byte(ocrDocURL))
	wantHash := hex.EncodeToString(sum[:])
	var r struct {
		Model    string `json:"model"`
		Pages    string `json:"pages"`
		Document struct {
			Type      string `json:"type"`
			RefSHA256 string `json:"ref_sha256"`
			RefBytes  int    `json:"ref_bytes"`
		} `json:"document"`
	}
	if err := json.Unmarshal(req, &r); err != nil {
		t.Fatalf("request payload: %v: %s", err, req)
	}
	if r.Model != "mistral-ocr-4-0" || r.Pages != "0-2" || r.Document.Type != "document_url" ||
		r.Document.RefSHA256 != wantHash || r.Document.RefBytes != len(ocrDocURL) {
		t.Errorf("request payload = %s, want model, pages 0-2, type and the reference's sha256 %s and length", req, wantHash)
	}

	var out struct {
		Provider       string `json:"provider"`
		PagesProcessed int    `json:"pages_processed"`
		AnnotatedPages int    `json:"annotated_pages"`
		DocSizeBytes   int    `json:"doc_size_bytes"`
		RefSHA256      string `json:"document_ref_sha256"`
		Pages          any    `json:"pages"`
	}
	if err := json.Unmarshal(resp, &out); err != nil {
		t.Fatalf("response payload: %v: %s", err, resp)
	}
	if out.Provider != "mistral" || out.PagesProcessed != 3 || out.AnnotatedPages != 3 || out.DocSizeBytes != 48213 || out.RefSHA256 != wantHash {
		t.Errorf("response payload = %s, want provider, 3 pages processed and annotated, 48213 bytes and the hash", resp)
	}
	if out.Pages != nil {
		t.Errorf("response payload lists pages by default: %s", resp)
	}

	// Opted in under full: the text, the annotation and the URL are kept;
	// page images stay elided.
	rc.OCRRequest.KeepText = true
	req, resp = ForEndpoint(rc)
	for _, kept := range []string{ocrSecret, ocrAnnotKey} {
		if !strings.Contains(string(resp), kept) {
			t.Errorf("opted-in response payload lacks %q: %s", kept, resp)
		}
	}
	if !strings.Contains(string(req), "bank.example") {
		t.Errorf("opted-in request payload lacks the document URL: %s", req)
	}
	if strings.Contains(string(resp), "QQQQ") || !strings.Contains(string(resp), "image_base64_bytes") {
		t.Errorf("opted-in payload carries the page image instead of its size: %s", resp)
	}

	// The opt-in never loosens a stricter content mode.
	rc.Privacy = models.Privacy{ContentMode: models.ContentModeMetadata, Source: models.PrivacySourceKey}
	req, resp = ForEndpoint(rc)
	if strings.Contains(string(req)+string(resp), "Jane Doe") || strings.Contains(string(req), "bank.example") {
		t.Errorf("opt-in under metadata kept content:\nrequest  %s\nresponse %s", req, resp)
	}
}

// An inline document (a data: URI) is hashed whole, so the hash identifies
// the document; even an opted-in payload never carries the inline bytes.
func TestOCRInlineDocumentNeverCarried(t *testing.T) {
	inline := "data:application/pdf;base64," + strings.Repeat("JVBERi0xLjQK", 64)
	rc := ocrPayloadRC(t)
	rc.OCRRequest.Document = models.OCRDocument{Type: "document_url", DocumentURL: inline}
	rc.OCRRequest.KeepText = true

	req, _ := ForEndpoint(rc)
	if strings.Contains(string(req), "JVBERi0xLjQK") {
		t.Errorf("inline document carried into the payload: %s", req)
	}
	sum := sha256.Sum256([]byte(inline))
	if !strings.Contains(string(req), hex.EncodeToString(sum[:])) {
		t.Errorf("request payload lacks the inline document's hash: %s", req)
	}
}
