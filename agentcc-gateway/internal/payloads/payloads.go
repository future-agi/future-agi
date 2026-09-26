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
	"encoding/json"
	"regexp"
	"strconv"
	"strings"

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
		return marshalPtr(rc.SearchRequest), marshalPtr(rc.SearchResponse)

	case "ocr":
		return marshalPtr(rc.OCRRequest), marshal(ocrResponse(rc.OCRResponse))

	default:
		return nil, nil
	}
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

// ocrResponse keeps the extracted markdown, which is the result, and drops the
// base64 page images, which are the input rendered back.
func ocrResponse(resp *models.OCRResponse) any {
	if resp == nil {
		return nil
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
				images = append(images, entry)
			}
			page["images"] = images
		}
		pages = append(pages, page)
	}
	return map[string]any{
		"object":     resp.Object,
		"model":      resp.Model,
		"pages":      pages,
		"usage_info": resp.UsageInfo,
	}
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
