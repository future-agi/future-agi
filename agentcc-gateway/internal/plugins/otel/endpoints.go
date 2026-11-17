package otel

import (
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
)

// attachEndpointAttributes sets the convention's own attributes for non-chat
// endpoints. Prompts, transcripts and queries are content even when small: they
// need the same selected redactor as bodies, independently of include_bodies.
// Actual dimensions (model, size, counts, language) remain queryable unchanged.
func (p *Plugin) attachEndpointAttributes(span *otelpkg.Span, rc *models.RequestContext, contentMode models.ContentMode) {
	r := p.redactorFor(rc)
	mode := privacyMode(rc)
	scrub := func(value string) string { return redact(value, r, mode) }
	switch rc.EndpointType {
	case "image":
		if req := rc.ImageRequest; req != nil {
			setIfNotEmpty(span, "gen_ai.image.prompt", scrub(req.Prompt))
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
					ref := d.URL
					if contentMode != models.ContentModeFull {
						ref = imageURLInfo(ref).placeholder(contentMode)
					} else if r != nil && r.ShouldRedact() {
						// URLs/data URIs can encode content patterns cannot inspect.
						ref = "[REDACTED]"
					}
					urls = append(urls, ref)
				}
				setIfNotEmpty(span, "gen_ai.image.revised_prompt", scrub(d.RevisedPrompt))
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
			setIfNotEmpty(span, "gen_ai.audio.transcript", scrub(resp.Text))
		}

	case "translation":
		if resp := rc.TranslationResp; resp != nil {
			setIfNotEmpty(span, "gen_ai.audio.transcript", scrub(resp.Text))
		}

	case "speech", "speech_stream":
		if req := rc.SpeechRequest; req != nil {
			// The text being spoken is the prompt for a TTS call.
			setIfNotEmpty(span, "gen_ai.audio.transcript", scrub(req.Input))
			setIfNotEmpty(span, "gen_ai.audio.mime_type", rc.Metadata["response_content_type"])
		}

	case "embedding", "genai_embed":
		if req := rc.EmbeddingRequest; req != nil && req.Dimensions != nil {
			span.SetAttribute("gen_ai.embeddings.dimension.count", *req.Dimensions)
		}

	case "rerank":
		if req := rc.RerankRequest; req != nil {
			setIfNotEmpty(span, "gen_ai.reranker.query", scrub(req.Query))
			setIfNotEmpty(span, "gen_ai.reranker.model", req.Model)
			if req.TopN != nil {
				span.SetAttribute("gen_ai.reranker.top_n", *req.TopN)
			}
		}

	case "search":
		if req := rc.SearchRequest; req != nil {
			// QueryRaw is a JSON string or array, not already-decoded text.
			setIfNotEmpty(span, "gen_ai.retrieval.query", redactJSON(string(req.QueryRaw), r, mode))
			if req.MaxResults > 0 {
				span.SetAttribute("gen_ai.retrieval.top_k", req.MaxResults)
			}
		}

	case "decision":
		// The shape of the call, never its content: the state and the
		// questions' instructions are the customer's evaluation data.
		if req := rc.DecisionRequest; req != nil {
			setIfNotEmpty(span, "gen_ai.decision.format", req.Format)
			span.SetAttribute("gen_ai.decision.question_count", len(req.QuestionIDs))
			setIfNotEmpty(span, "gen_ai.decision.question_types", strings.Join(req.QuestionTypes, ","))
		}
	}
}

func setIfNotEmpty(span *otelpkg.Span, key, value string) {
	if value != "" {
		span.SetAttribute(key, value)
	}
}
