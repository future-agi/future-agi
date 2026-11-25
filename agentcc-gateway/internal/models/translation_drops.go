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

// ChatRoute identifies a translation whose omitted parameters must also be
// reported on cache hits. The empty route carries no provider-specific notes.
type ChatRoute string

const (
	RouteResponses ChatRoute = "responses"
	RouteGemini    ChatRoute = "gemini"
)

// HasNonDirectCallerPolicy distinguishes an absent or direct-only setting
// from a caller restriction a direct-only provider cannot enforce.
func (t Tool) HasNonDirectCallerPolicy() bool {
	return t.AllowedCallers != nil && (len(t.AllowedCallers) != 1 || t.AllowedCallers[0] != "direct")
}

// GeminiOmittedParams names unenforceable restrictions, never caller values.
func GeminiOmittedParams(req *ChatCompletionRequest) []string {
	if req == nil {
		return nil
	}
	tools := req.Tools
	allowed, err := ParseAllowedToolsChoice(req.ToolChoice)
	if err != nil {
		return nil
	}
	if allowed != nil {
		tools, err = allowed.Filter(tools)
		if err != nil {
			return nil
		}
	}
	var params []string
	if req.ParallelToolCalls != nil && !*req.ParallelToolCalls && len(tools) > 0 {
		var choice string
		_ = json.Unmarshal(req.ToolChoice, &choice)
		// NONE already permits zero calls, satisfying the restriction.
		if choice != "none" {
			params = append(params, "parallel_tool_calls")
		}
	}
	for _, tool := range tools {
		if tool.IsFunction() && tool.HasNonDirectCallerPolicy() {
			params = append(params, "allowed_callers")
			break
		}
	}
	return params
}

// RouteNotes formats fresh provider parameter names for the ingress dialect:
// chat callers get <name>_unsupported; translated callers get the bare name.
func RouteNotes(req *ChatCompletionRequest, params []string) []string {
	if req != nil && req.TranslatedFrom == "" {
		for i := range params {
			params[i] += "_unsupported"
		}
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

// SetRouteDrops records route as the serving attempt's provider-route
// notes and rebuilds the request's translation_drops metadata from the
// translator's notes followed by these, deduplicated and without empty names.
// It returns the joined notes, or "" when there are none. Notes are rebuilt
// from these trusted lists only, never from headers or caller metadata.
func (rc *RequestContext) SetRouteDrops(route []string) string {
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
