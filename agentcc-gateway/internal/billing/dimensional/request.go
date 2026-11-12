package dimensional

import (
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// AdmitFlatInputChat builds a conservative envelope from a normalized request
// for a QUALIFIED flat-input-image, text-output contract. inputTokenLimit must
// be the provider's enforced context maximum, not a tokenizer heuristic. The
// caller must dispatch this exact request without later transforms, and the
// contract must forbid implicit server tools. This helper does not qualify a
// bundled Gemini card, nor does it invent image-output/song/clip bounds.
func AdmitFlatInputChat(card Card, req *models.ChatCompletionRequest, inputTokenLimit int64) (*Plan, error) {
	if req == nil || req.Model != card.Identity.Model || inputTokenLimit <= 0 || inputTokenLimit > 1_000_000_000 {
		return nil, fmt.Errorf("request identity or enforced context bound is missing")
	}
	if req.N != nil && *req.N != 1 {
		return nil, fmt.Errorf("multiple candidate request is not qualified")
	}
	if req.Audio != nil {
		return nil, fmt.Errorf("audio generation has no qualified bound")
	}
	if req.ServiceTier != "" {
		return nil, fmt.Errorf("service-tier override has no qualified quote")
	}
	for _, modality := range req.Modalities {
		if modality != "text" {
			return nil, fmt.Errorf("output modality has no qualified bound")
		}
	}
	for _, tool := range req.Tools {
		if tool.Type != "function" || len(tool.Raw) != 0 {
			return nil, fmt.Errorf("server-tool work has no qualified bound")
		}
	}
	images, err := InputImageOccurrences(req)
	if err != nil {
		return nil, err
	}
	out := int64(0)
	for _, limit := range []*int{req.MaxTokens, req.MaxCompletionTokens} {
		if limit != nil {
			if *limit <= 0 {
				return nil, fmt.Errorf("invalid output token limit")
			}
			out = max(out, int64(*limit))
		}
	}
	if out == 0 {
		return nil, fmt.Errorf("explicit enforced output token limit is required")
	}
	caps := make(map[Unit]int64, len(card.Rates))
	for unit := range card.Rates {
		switch unit {
		case InputToken, CacheRead, CacheWrite:
			// The exact cache partition is not yet known. Bounding each part
			// independently over-reserves but cannot discard a higher cache rate.
			caps[unit] = inputTokenLimit
		case OutputToken, Reasoning:
			caps[unit] = out
		case InputImage:
			caps[unit] = images
		case Request:
			caps[unit] = 1
		case Search:
			caps[unit] = 0 // the qualified contract forbids implicit tools
		default:
			return nil, fmt.Errorf("unit %s has no request-derived bound", unit)
		}
	}
	if images > 0 {
		if _, quoted := card.Rates[InputImage]; !quoted {
			return nil, fmt.Errorf("input image has no explicit quote")
		}
	}
	return Admit(card, BoundRequest{Identity: card.Identity, Caps: caps})
}
