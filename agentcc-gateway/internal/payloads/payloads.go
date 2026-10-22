// Package payloads renders a request context's request and response into JSON
// for the sinks that record content — the request-log webhook and exported
// trace spans.
//
// It exists so those two agree. They are different destinations but the same
// question: what does this request actually look like? Answering it twice
// invites them to drift, and a trace whose body disagrees with the log of the
// same request is worse than either alone.
//
// Large binary payloads are summarized rather than carried. A base64 image or
// an embedding vector is megabytes that no reviewer reads and no evaluator
// scores, and including it would crowd out the prompt text that is the point.
// What replaces it says how big it was, so nothing looks silently absent.
package payloads

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"regexp"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ForEndpoint returns the request and response payloads for a non-chat
// endpoint. Chat-shaped traffic returns nil, nil: rc.Request and rc.Response
// already describe it, and callers marshal those directly.
func ForEndpoint(rc *models.RequestContext) (request, response json.RawMessage) {
	switch rc.EndpointType {
	case "embedding", "genai_embed":
		return marshalPtr(rc.EmbeddingRequest), marshal(embeddingResponse(rc.EmbeddingResponse))

	case "image":
		return marshalPtr(rc.ImageRequest), marshal(imageResponse(rc.ImageResponse))

	case "speech", "speech_stream":
		if rc.SpeechRequest == nil {
			return nil, nil
		}
		resp := map[string]any{
			"binary":       true,
			"content_type": rc.Metadata["response_content_type"],
		}
		if size := rc.Metadata["response_size_bytes"]; size != "" {
			resp["size_bytes"] = size
		}
		return marshalPtr(rc.SpeechRequest), marshal(resp)

	case "transcription":
		if rc.TranscriptionReq == nil {
			return nil, marshalPtr(rc.TranscriptionResp)
		}
		req := map[string]any{
			"model":           rc.TranscriptionReq.Model,
			"file_name":       rc.TranscriptionReq.FileName,
			"file_size_bytes": len(rc.TranscriptionReq.FileData),
			"language":        rc.TranscriptionReq.Language,
			"response_format": rc.TranscriptionReq.ResponseFormat,
		}
		if rc.TranscriptionReq.Temperature != nil {
			req["temperature"] = *rc.TranscriptionReq.Temperature
		}
		if audioSeconds := rc.Metadata["audio_seconds"]; audioSeconds != "" {
			req["audio_seconds"] = audioSeconds
		}
		return marshal(req), marshalPtr(rc.TranscriptionResp)

	case "translation":
		if rc.TranslationReq == nil {
			return nil, marshalPtr(rc.TranslationResp)
		}
		req := map[string]any{
			"model":           rc.TranslationReq.Model,
			"file_name":       rc.TranslationReq.FileName,
			"file_size_bytes": len(rc.TranslationReq.FileData),
			"prompt":          rc.TranslationReq.Prompt,
			"response_format": rc.TranslationReq.ResponseFormat,
		}
		if rc.TranslationReq.Temperature != nil {
			req["temperature"] = *rc.TranslationReq.Temperature
		}
		return marshal(req), marshalPtr(rc.TranslationResp)

	case "rerank":
		// Query and documents are the whole content; the size cap handles bulk.
		return marshalPtr(rc.RerankRequest), marshalPtr(rc.RerankResponse)

	case "search":
		if call, ok := capability.CallOf(rc); ok {
			return capabilityPayloads(rc, call)
		}
		return retained(capability.RetentionFor(rc, nil), marshalPtr(rc.SearchRequest), marshalPtr(rc.SearchResponse))

	case "ocr":
		return ocrPayloads(rc)

	case "fetch", "crawl", "documents", "guardrails", "evals", "browser",
		"sandbox_usage", "speech_session", "video", "payments":
		if call, ok := capability.CallOf(rc); ok {
			return capabilityPayloads(rc, call)
		}
		return nil, nil

	default:
		return nil, nil
	}
}

// capabilityPayloads are a capability call's request and response as its
// retention allows: the bodies under full, a
// description without content under metadata, nothing under none.
// capability.RetentionFor is the most restrictive of the request's content
// mode, the served vendor's results_retention and the org's override.
func capabilityPayloads(rc *models.RequestContext, call *capability.Call) (request, response json.RawMessage) {
	switch capability.RetentionFor(rc, call.Card()) {
	case models.ContentModeFull:
		return call.Request(), call.Response()
	case models.ContentModeMetadata:
		return call.MetadataRequest(), call.MetadataResponse()
	default:
		return nil, nil
	}
}

// retained keeps a capability payload only under full retention: a call
// with no capability record (the search endpoint's provider adapters) has
// no card, so its content mode alone decides.
func retained(mode models.ContentMode, request, response json.RawMessage) (json.RawMessage, json.RawMessage) {
	if mode != models.ContentModeFull {
		return nil, nil
	}
	return request, response
}

// embeddingResponse keeps the shape and the usage but drops the vectors. The
// numbers are the one part of an embedding response nobody reads back.
func embeddingResponse(resp *models.EmbeddingResponse) any {
	if resp == nil {
		return nil
	}
	data := make([]map[string]any, 0, len(resp.Data))
	for _, d := range resp.Data {
		data = append(data, map[string]any{
			"object":          d.Object,
			"index":           d.Index,
			"embedding_bytes": len(d.Embedding),
		})
	}
	return map[string]any{
		"object": resp.Object,
		"model":  resp.Model,
		"usage":  resp.Usage,
		"data":   data,
	}
}

// imageResponse keeps URLs and revised prompts — both worth reading — and
// replaces inline base64 images with their size.
func imageResponse(resp *models.ImageResponse) any {
	if resp == nil {
		return nil
	}
	data := make([]map[string]any, 0, len(resp.Data))
	for _, d := range resp.Data {
		entry := map[string]any{}
		if d.URL != "" {
			entry["url"] = d.URL
		}
		if d.RevisedPrompt != "" {
			entry["revised_prompt"] = d.RevisedPrompt
		}
		if d.B64JSON != "" {
			entry["b64_json_bytes"] = len(d.B64JSON)
		}
		data = append(data, entry)
	}
	return map[string]any{"created": resp.Created, "data": data}
}

// ocrPayloads describes an OCR call by its metadata: the model, the
// document's type and a hash of its reference, the page selection, the
// provider, and the pages and bytes processed. The document, its URL, its
// extracted text and its annotation are personal data more often than not
// (IDs, invoices, medical records), so they are added only when the request's
// content mode is full and its org opted in (OCRRequest.KeepText).
func ocrPayloads(rc *models.RequestContext) (request, response json.RawMessage) {
	req := rc.OCRRequest
	keepText := req != nil && req.KeepText && rc.Privacy.ContentMode == models.ContentModeFull
	var refHash string
	if req != nil {
		ref := req.Document.Reference()
		refHash = sha256Hex(ref)
		doc := map[string]any{"type": req.Document.Type, "ref_sha256": refHash, "ref_bytes": len(ref)}
		if keepText {
			doc["ref"] = ElideInlineData(ref)
		}
		out := map[string]any{"model": req.Model, "document": doc}
		if len(req.Pages) > 0 {
			out["pages"] = req.Pages
		}
		request = marshal(out)
	}
	if resp := rc.OCRResponse; resp != nil {
		response = marshal(ocrResponse(resp, rc.Provider, refHash, keepText))
	}
	return request, response
}

// ocrResponse is the metering view of an OCR response, plus, with keepText,
// the extracted markdown and annotation, with base64 page images replaced by
// their size (they are the input rendered back).
func ocrResponse(resp *models.OCRResponse, provider, refHash string, keepText bool) map[string]any {
	processed, _ := resp.PagesProcessed()
	out := map[string]any{
		"model":           resp.Model,
		"provider":        provider,
		"pages_processed": processed,
		"annotated_pages": resp.AnnotatedPages(),
	}
	if resp.Object != "" {
		out["object"] = resp.Object
	}
	if refHash != "" {
		out["document_ref_sha256"] = refHash
	}
	if resp.UsageInfo != nil {
		out["doc_size_bytes"] = resp.UsageInfo.DocSizeBytes
	}
	if !keepText {
		return out
	}
	pages := make([]map[string]any, 0, len(resp.Pages))
	for _, p := range resp.Pages {
		page := map[string]any{
			"index":    p.Index,
			"markdown": p.Markdown,
		}
		if p.Dimensions != nil {
			page["dimensions"] = p.Dimensions
		}
		if len(p.Images) > 0 {
			images := make([]map[string]any, 0, len(p.Images))
			for _, img := range p.Images {
				entry := map[string]any{"image_base64_bytes": len(img.ImageBase64)}
				if img.BBox != nil {
					entry["bbox"] = img.BBox
				}
				if len(img.ImageAnnotation) > 0 {
					entry["image_annotation"] = img.ImageAnnotation
				}
				images = append(images, entry)
			}
			page["images"] = images
		}
		pages = append(pages, page)
	}
	out["pages"] = pages
	if len(resp.DocumentAnnotation) > 0 {
		out["document_annotation"] = resp.DocumentAnnotation
	}
	return out
}

// sha256Hex is the hex SHA-256 of s: for a data: URI, of the inline document
// itself.
func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// marshalPtr exists because a nil pointer inside an `any` is not `== nil`:
// passing one to marshal would emit the JSON literal `null` rather than
// omitting the field, so an absent payload would read as a present empty one.
func marshalPtr[T any](v *T) json.RawMessage {
	if v == nil {
		return nil
	}
	return marshal(v)
}

func marshal(v any) json.RawMessage {
	if v == nil {
		return nil
	}
	b, err := json.Marshal(v)
	if err != nil {
		return nil
	}
	return b
}

// dataURI matches an inline base64 data URI, the way images and audio arrive
// inside chat message content.
var dataURI = regexp.MustCompile(`data:([a-zA-Z0-9.+/-]+);base64,([A-Za-z0-9+/=]{256,})`)

// ElideInlineData replaces inline base64 data URIs with a note of their type
// and size.
//
// Vision and audio requests carry the payload inside the message content, so
// unlike the multimodal endpoints there is no separate field to leave behind.
// Carried through, one image is megabytes: it would blow the per-attribute cap
// and truncate the surrounding JSON into something unparseable, losing the
// prompt as well as the image. The mime type and size are what a reviewer can
// actually use.
func ElideInlineData(s string) string {
	if !strings.Contains(s, ";base64,") {
		return s
	}
	return dataURI.ReplaceAllStringFunc(s, func(match string) string {
		parts := dataURI.FindStringSubmatch(match)
		if len(parts) != 3 {
			return match
		}
		return "data:" + parts[1] + ";base64,<elided " + strconv.Itoa(len(parts[2])) + " bytes>"
	})
}
