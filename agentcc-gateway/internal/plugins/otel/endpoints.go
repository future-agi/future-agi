package otel

import (
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
)

// attachEndpointAttributes sets the convention's own attributes for non-chat
// endpoints, so an image or audio span is queryable by prompt, size or
// transcript rather than only as a blob of JSON in input.value.
//
// These are dimensions, not payloads: everything here is small and worth
// filtering or grouping by. The full record still goes in input.value /
// output.value when body capture is on, and the bulk — base64 images, audio
// bytes, embedding vectors — is never carried by either.
func (p *Plugin) attachEndpointAttributes(span *otelpkg.Span, rc *models.RequestContext) {
	switch rc.EndpointType {
	case "image":
		if req := rc.ImageRequest; req != nil {
			setIfNotEmpty(span, "gen_ai.image.prompt", req.Prompt)
			setIfNotEmpty(span, "gen_ai.image.size", req.Size)
			setIfNotEmpty(span, "gen_ai.image.quality", req.Quality)
			setIfNotEmpty(span, "gen_ai.image.style", req.Style)
			setIfNotEmpty(span, "gen_ai.image.format", req.ResponseFormat)
			if req.N != nil {
				span.SetAttribute("gen_ai.image.count", *req.N)
			}
		}
		if resp := rc.ImageResponse; resp != nil {
			var urls []string
			for _, d := range resp.Data {
				if d.URL != "" {
					urls = append(urls, d.URL)
				}
				setIfNotEmpty(span, "gen_ai.image.revised_prompt", d.RevisedPrompt)
			}
			if len(urls) > 0 {
				span.SetAttribute("gen_ai.image.output_urls", strings.Join(urls, ","))
			}
		}

	case "transcription":
		if req := rc.TranscriptionReq; req != nil {
			setIfNotEmpty(span, "gen_ai.audio.language", req.Language)
		}
		if secs := rc.Metadata["audio_seconds"]; secs != "" {
			if v, err := strconv.ParseFloat(secs, 64); err == nil {
				span.SetAttribute("gen_ai.audio.duration_secs", v)
			}
		}
		if resp := rc.TranscriptionResp; resp != nil {
			setIfNotEmpty(span, "gen_ai.audio.transcript", resp.Text)
		}

	case "translation":
		if resp := rc.TranslationResp; resp != nil {
			setIfNotEmpty(span, "gen_ai.audio.transcript", resp.Text)
		}

	case "speech", "speech_stream":
		if req := rc.SpeechRequest; req != nil {
			// The text being spoken is the prompt for a TTS call.
			setIfNotEmpty(span, "gen_ai.audio.transcript", req.Input)
			setIfNotEmpty(span, "gen_ai.audio.mime_type", rc.Metadata["response_content_type"])
		}

	case "embedding", "genai_embed":
		if req := rc.EmbeddingRequest; req != nil && req.Dimensions != nil {
			span.SetAttribute("gen_ai.embeddings.dimension.count", *req.Dimensions)
		}

	case "rerank":
		if req := rc.RerankRequest; req != nil {
			setIfNotEmpty(span, "gen_ai.reranker.query", req.Query)
			setIfNotEmpty(span, "gen_ai.reranker.model", req.Model)
			if req.TopN != nil {
				span.SetAttribute("gen_ai.reranker.top_n", *req.TopN)
			}
		}

	case "search":
		if req := rc.SearchRequest; req != nil {
			setIfNotEmpty(span, "gen_ai.retrieval.query", string(req.QueryRaw))
			if req.MaxResults > 0 {
				span.SetAttribute("gen_ai.retrieval.top_k", req.MaxResults)
			}
		}
	}
}

func setIfNotEmpty(span *otelpkg.Span, key, value string) {
	if value != "" {
		span.SetAttribute(key, value)
	}
}
