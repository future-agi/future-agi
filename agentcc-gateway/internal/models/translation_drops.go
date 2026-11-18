package models

import (
	"encoding/json"
	"strings"
)

// ResponsesOmittedParams names the chat parameters set on req that the
// Responses route omits: stop, seed, logprobs, top_logprobs, the penalties,
// logit_bias and n > 1. A value that changes nothing (a zero penalty, logprobs
// false, an empty stop, a logit_bias of zeros, n of 1) is not counted. It is
// the one support table for the provider's dispatch warning and for notes a
// response-cache hit reports about the request it answers.
func ResponsesOmittedParams(req *ChatCompletionRequest) []string {
	if req == nil {
		return nil
	}
	var params []string
	if responsesStopSet(req.Stop) {
		params = append(params, "stop")
	}
	if req.Seed != nil {
		params = append(params, "seed")
	}
	if req.Logprobs != nil && *req.Logprobs {
		params = append(params, "logprobs")
	}
	if req.TopLogprobs != nil && *req.TopLogprobs > 0 {
		params = append(params, "top_logprobs")
	}
	if req.FrequencyPenalty != nil && *req.FrequencyPenalty != 0 {
		params = append(params, "frequency_penalty")
	}
	if req.PresencePenalty != nil && *req.PresencePenalty != 0 {
		params = append(params, "presence_penalty")
	}
	for _, bias := range req.LogitBias {
		if bias != 0 {
			params = append(params, "logit_bias")
			break
		}
	}
	if req.N != nil && *req.N > 1 {
		params = append(params, "n")
	}
	return params
}

// ResponsesRouteNotes is how the route a request arrived on reports what the
// Responses route omits from it: <name>_unsupported for a caller's own chat
// request, and the bare names on translated routes, beside the translator's
// own notes.
func ResponsesRouteNotes(req *ChatCompletionRequest) []string {
	params := ResponsesOmittedParams(req)
	if req == nil || req.TranslatedFrom != "" {
		return params
	}
	for i, param := range params {
		params[i] = param + "_unsupported"
	}
	return params
}

// responsesStopSet reports whether a chat stop value names at least one sequence.
func responsesStopSet(raw json.RawMessage) bool {
	var one string
	if json.Unmarshal(raw, &one) == nil {
		return one != ""
	}
	var many []string
	if json.Unmarshal(raw, &many) == nil {
		for _, s := range many {
			if s != "" {
				return true
			}
		}
		return false
	}
	return len(raw) > 0 && string(raw) != "null"
}

// SetResponsesRouteDrops records route as the serving attempt's Responses-route
// notes and rebuilds the request's translation_drops metadata from the
// translator's notes followed by these, deduplicated and without empty names.
// It returns the joined notes, or "" when there are none. Notes are rebuilt
// from these trusted lists only, never from headers or caller metadata.
func (rc *RequestContext) SetResponsesRouteDrops(route []string) string {
	var drops []string
	seen := make(map[string]bool)
	for _, group := range [][]string{rc.TranslationDrops, route} {
		for _, drop := range group {
			if drop != "" && !seen[drop] {
				seen[drop] = true
				drops = append(drops, drop)
			}
		}
	}
	if len(drops) == 0 {
		delete(rc.Metadata, "translation_drops")
		return ""
	}
	if rc.Metadata == nil {
		rc.Metadata = make(map[string]string)
	}
	joined := strings.Join(drops, ",")
	rc.Metadata["translation_drops"] = joined
	return joined
}
