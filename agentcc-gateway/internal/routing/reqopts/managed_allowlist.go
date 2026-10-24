package reqopts

import (
	"encoding/json"
	"fmt"
	"sort"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// managedChatAllowlist is every chat-completions parameter a managed request
// may send. A managed request runs on FutureAGI's upstream
// account and is reserved before dispatch from its tokens and max_tokens,
// so a parameter that changes what the upstream charges for, or for what,
// is not allowed through until it is priced. Each entry says why it is safe
// to bill on the reserve estimate. Adding a parameter needs such a pricing
// note.
var managedChatAllowlist = map[string]string{
	"model":                 "selects the priced model",
	"messages":              "the prompt, counted in prompt tokens",
	"tools":                 "function tool schemas only, counted in prompt tokens; every other tool type is refused",
	"tool_choice":           "steers output only; no separate charge",
	"parallel_tool_calls":   "steers output only; no separate charge",
	"response_format":       "constrains output; billed as completion tokens",
	"temperature":           "sampling only",
	"top_p":                 "sampling only",
	"top_k":                 "sampling only",
	"max_tokens":            "caps completion tokens; the reserve reads it",
	"max_completion_tokens": "caps completion tokens; the reserve reads it",
	"stop":                  "ends output early; never adds tokens",
	"seed":                  "sampling only",
	"presence_penalty":      "sampling only",
	"frequency_penalty":     "sampling only",
	"repetition_penalty":    "sampling only",
	"logit_bias":            "sampling only",
	"logprobs":              "returns logprobs of billed tokens; no separate charge",
	"top_logprobs":          "returns logprobs of billed tokens; no separate charge",
	"user":                  "replaced by the hashed org id before dispatch",
	"stream":                "delivery only",
	"stream_options":        "delivery only; the gateway asks for usage",
	"reasoning":             "reasoning tokens are reported and billed as completion tokens",
	"reasoning_effort":      "reasoning tokens are reported and billed as completion tokens",
	"metadata":              "caller tags; no upstream charge",
	"n":                     "allowed only as 1: the reserve covers one completion",
	"usage":                 "allowed only as {include: true}: no price effect, the outbound body always carries the gateway-built usage: {include: true}",
}

// usageIncludeOnly reports whether raw is exactly usage: {include: true},
// the one usage value a managed request may send: the gateway's own outbound
// value, so accepting it changes nothing; every other usage value is
// refused.
func usageIncludeOnly(raw json.RawMessage) bool {
	var u map[string]json.RawMessage
	if json.Unmarshal(raw, &u) != nil || len(u) != 1 {
		return false
	}
	var include bool
	return json.Unmarshal(u["include"], &include) == nil && include
}

// CheckManaged refuses what a managed chat request may not send: a
// non-empty plugins, route or transforms (400 unsupported_routing_field),
// then n other than 1, a provider-run tool, or any outbound key outside the
// allowlist (400 unsupported_field_for_managed). It runs after Parse, so the
// routing fields are already out of req.Extra and in opts.
func CheckManaged(req *models.ChatCompletionRequest, opts *models.RoutingOptions) *models.APIError {
	switch {
	case nonEmptyArray(opts.Plugins):
		return fieldError(CodeUnsupportedRoutingField, keyPlugins, "plugins are not available on managed models")
	case opts.Route != "":
		return fieldError(CodeUnsupportedRoutingField, keyRoute, "route is not available on managed models")
	case nonEmptyArray(opts.Transforms):
		return fieldError(CodeUnsupportedRoutingField, keyTransforms, "transforms are not available on managed models")
	}
	if req == nil {
		return nil
	}
	if req.N != nil && *req.N != 1 {
		return fieldError(CodeUnsupportedFieldForManaged, "n", "n must be 1 on managed models")
	}
	if raw, ok := req.Extra["usage"]; ok && !usageIncludeOnly(raw) {
		return fieldError(CodeUnsupportedFieldForManaged, "usage",
			"only usage: {include: true} is available on managed models; the gateway always asks for usage")
	}
	if i := providerRunTool(req.Tools); i >= 0 {
		return fieldError(CodeUnsupportedFieldForManaged, fmt.Sprintf("tools[%d].type", i),
			fmt.Sprintf("only function tools are available on managed models; %q tools are not priced", req.Tools[i].Type))
	}
	keys, err := outboundKeys(req)
	if err != nil {
		return models.ErrInternal("reading the request: " + err.Error())
	}
	for _, k := range keys {
		if _, ok := managedChatAllowlist[k]; !ok {
			return fieldError(CodeUnsupportedFieldForManaged, k, k+" is not available on managed models")
		}
	}
	return nil
}

// StripForManaged removes from req everything CheckManaged would refuse, so
// a managed attempt that CheckManaged never saw (a fallback hop that became
// managed) still sends nothing outside the allowlist. A provider-run tool is
// dropped (named tools[<i>]); when no tool is left, tool_choice and
// parallel_tool_calls go too, since they are invalid without tools. It
// returns the keys it removed, sorted.
func StripForManaged(req *models.ChatCompletionRequest) []string {
	var removed []string
	if providerRunTool(req.Tools) >= 0 {
		kept := req.Tools[:0:0]
		for i, t := range req.Tools {
			if len(t.Raw) > 0 {
				removed = append(removed, fmt.Sprintf("tools[%d]", i))
				continue
			}
			kept = append(kept, t)
		}
		req.Tools = kept
		if len(kept) == 0 {
			req.Tools = nil
			if req.ToolChoice != nil {
				req.ToolChoice = nil
				removed = append(removed, "tool_choice")
			}
			if _, ok := req.Extra["parallel_tool_calls"]; ok {
				delete(req.Extra, "parallel_tool_calls")
				removed = append(removed, "parallel_tool_calls")
			}
		}
	}
	for k, raw := range req.Extra {
		if _, ok := managedChatAllowlist[k]; !ok || (k == "usage" && !usageIncludeOnly(raw)) {
			delete(req.Extra, k)
			removed = append(removed, k)
		}
	}
	if req.N != nil && *req.N != 1 {
		req.N = nil
		removed = append(removed, "n")
	}
	// The typed parameters outside the allowlist.
	if req.ServiceTier != "" {
		req.ServiceTier = ""
		removed = append(removed, "service_tier")
	}
	if req.Modalities != nil {
		req.Modalities = nil
		removed = append(removed, "modalities")
	}
	if req.Audio != nil {
		req.Audio = nil
		removed = append(removed, "audio")
	}
	sort.Strings(removed)
	return removed
}

// providerRunTool is the index of the first tool that is not a function, or
// -1 when every tool is one. models.Tool keeps such a tool's bytes in Raw and
// forwards them unchanged; most are run and billed per use by the provider
// (Anthropic server tools such as web_search_20250305), and none is priced
// (OpenAI custom tools included), so a managed request may not send them.
func providerRunTool(tools []models.Tool) int {
	for i, t := range tools {
		if len(t.Raw) > 0 {
			return i
		}
	}
	return -1
}

// outboundKeys is every JSON key req would send upstream, typed or Extra,
// sorted.
func outboundKeys(req *models.ChatCompletionRequest) ([]string, error) {
	b, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(b, &obj); err != nil {
		return nil, err
	}
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys, nil
}

func nonEmptyArray(raw json.RawMessage) bool {
	var a []json.RawMessage
	if json.Unmarshal(raw, &a) != nil {
		return len(raw) > 0 && !isNull(raw)
	}
	return len(a) > 0
}
