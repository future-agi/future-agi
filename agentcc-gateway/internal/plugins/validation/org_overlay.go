package validation

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/modeldb/orgdb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// SetTenantStore gives the plugin the org configs whose Model Database
// overrides it applies: each org's token limits and capability
// switches for a model, on every call of that org (BYOK and managed). Call it
// before the gateway serves. Without it only the gateway's model database is
// checked.
func (p *Plugin) SetTenantStore(s *tenant.Store) { p.tenantStore = s }

// orgOverride is the request's org's Model Database override for the model
// it is routed to, or nil. It reads nothing more than one map entry unless
// the org configured overrides.
func (p *Plugin) orgOverride(rc *models.RequestContext) *tenant.ModelOverrideOrgConfig {
	if p.tenantStore == nil {
		return nil
	}
	orgID := rc.Metadata[tenant.MetadataKeyOrgID]
	if orgID == "" {
		return nil
	}
	cfg := p.tenantStore.Get(orgID)
	if cfg == nil || cfg.ModelDatabase == nil || len(cfg.ModelDatabase.Overrides) == 0 {
		return nil
	}
	return orgdb.Override(cfg, rc.Provider, rc.Model)
}

// checkOrgOverride applies ov to a chat request:
//
//   - A capability switch the org turned off refuses a request that uses the
//     capability with 400 model_capability_error naming the switch:
//     function_calling (tools), parallel_tool_calls (parallel_tool_calls:
//     true), vision (an image part), audio_input (an input_audio part),
//     audio_output (modalities with "audio", or an audio block), pdf_input (a
//     file part), streaming (stream: true), response_schema (a json_schema
//     response format), system_messages (a system or developer message) and
//     reasoning (reasoning_effort, reasoning or thinking). prompt_caching has
//     no request-side signal and is not checked.
//   - max_input_tokens refuses with 413 input_too_large, naming the limit,
//     when the gateway's estimate of the input (estimateTokens: about four
//     characters per token, a fixed allowance per image, audio or file part)
//     is above it.
//   - max_output_tokens refuses with 400 max_tokens_exceeds_limit, naming the
//     limit, when max_tokens or max_completion_tokens asks for more; a request
//     that sets neither is sent with max_tokens set to the limit.
//
// A switch turned on is not checked here: it lets through what the model
// database would refuse (ProcessRequest merges it before that check).
func checkOrgOverride(rc *models.RequestContext, ov *tenant.ModelOverrideOrgConfig) *models.APIError {
	req := rc.Request
	if c := ov.Capabilities; c != nil {
		for _, sw := range capabilitySwitches {
			if v := sw.flag(c); v != nil && !*v && sw.used(req) {
				return &models.APIError{
					Status: http.StatusBadRequest,
					Type:   models.ErrTypeInvalidRequest,
					Code:   "model_capability_error",
					Message: fmt.Sprintf("%s: the organization's model database turns off %s for model %q",
						sw.usage, sw.name, rc.Model),
				}
			}
		}
	}
	if limit := orgdb.MaxInputTokens(ov); limit > 0 {
		if estimated := estimateTokens(req); estimated > limit {
			return &models.APIError{
				Status: http.StatusRequestEntityTooLarge,
				Type:   models.ErrTypeInvalidRequest,
				Code:   "input_too_large",
				Message: fmt.Sprintf("estimated input of about %d tokens exceeds max_input_tokens %d that the organization's model database sets for model %q",
					estimated, limit, rc.Model),
			}
		}
	}
	if limit := orgdb.MaxOutputTokens(ov); limit > 0 {
		asked, field := 0, ""
		if req.MaxCompletionTokens != nil && *req.MaxCompletionTokens > asked {
			asked, field = *req.MaxCompletionTokens, "max_completion_tokens"
		}
		if req.MaxTokens != nil && *req.MaxTokens > asked {
			asked, field = *req.MaxTokens, "max_tokens"
		}
		switch {
		case asked > limit:
			return &models.APIError{
				Status: http.StatusBadRequest,
				Type:   models.ErrTypeInvalidRequest,
				Code:   "max_tokens_exceeds_limit",
				Param:  &field,
				Message: fmt.Sprintf("%s %d exceeds max_output_tokens %d that the organization's model database sets for model %q",
					field, asked, limit, rc.Model),
			}
		case req.MaxTokens == nil && req.MaxCompletionTokens == nil:
			req.MaxTokens = &limit
		}
	}
	return nil
}

// capabilitySwitch is one of the console's capability switches and the
// request feature it gates.
type capabilitySwitch struct {
	name  string // the switch, as the console and the org config name it
	usage string // what the request does that the switch refuses
	flag  func(*tenant.CapabilityOverrideOrg) *bool
	used  func(*models.ChatCompletionRequest) bool
}

var capabilitySwitches = []capabilitySwitch{
	{"function_calling", "the request sends tools",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.FunctionCalling },
		func(r *models.ChatCompletionRequest) bool { return len(r.Tools) > 0 }},
	{"parallel_tool_calls", "the request sets parallel_tool_calls",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.ParallelToolCalls },
		func(r *models.ChatCompletionRequest) bool { return extraTrue(r, "parallel_tool_calls") }},
	{"vision", "the request sends an image",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.Vision },
		func(r *models.ChatCompletionRequest) bool { return hasPart(r, "image_url", "image") }},
	{"audio_input", "the request sends audio",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.AudioInput },
		func(r *models.ChatCompletionRequest) bool { return hasPart(r, "input_audio") }},
	{"audio_output", "the request asks for audio output",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.AudioOutput },
		func(r *models.ChatCompletionRequest) bool { return r.Audio != nil || contains(r.Modalities, "audio") }},
	{"pdf_input", "the request sends a file",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.PDFInput },
		func(r *models.ChatCompletionRequest) bool { return hasPart(r, "file") }},
	{"streaming", "the request asks to stream",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.Streaming },
		func(r *models.ChatCompletionRequest) bool { return r.Stream }},
	{"response_schema", "the request asks for a json_schema response format",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.ResponseSchema },
		func(r *models.ChatCompletionRequest) bool {
			return r.ResponseFormat != nil && r.ResponseFormat.Type == "json_schema"
		}},
	{"system_messages", "the request sends a system message",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.SystemMessages },
		func(r *models.ChatCompletionRequest) bool { return hasRole(r, "system", "developer") }},
	{"reasoning", "the request asks for reasoning",
		func(c *tenant.CapabilityOverrideOrg) *bool { return c.Reasoning },
		func(r *models.ChatCompletionRequest) bool {
			return hasExtra(r, "reasoning_effort") || hasExtra(r, "reasoning") || hasExtra(r, "thinking")
		}},
}

func hasExtra(r *models.ChatCompletionRequest, key string) bool {
	v, ok := r.Extra[key]
	return ok && !bytes.Equal(bytes.TrimSpace(v), []byte("null"))
}

func extraTrue(r *models.ChatCompletionRequest, key string) bool {
	return bytes.Equal(bytes.TrimSpace(r.Extra[key]), []byte("true"))
}

func hasRole(r *models.ChatCompletionRequest, roles ...string) bool {
	for _, m := range r.Messages {
		if contains(roles, m.Role) {
			return true
		}
	}
	return false
}

// hasPart reports whether a message's content is an array with a part of one
// of types.
func hasPart(r *models.ChatCompletionRequest, types ...string) bool {
	for _, m := range r.Messages {
		trimmed := bytes.TrimSpace(m.Content)
		if len(trimmed) == 0 || trimmed[0] != '[' {
			continue
		}
		var parts []struct {
			Type string `json:"type"`
		}
		if json.Unmarshal(trimmed, &parts) != nil {
			continue
		}
		for _, p := range parts {
			if contains(types, p.Type) {
				return true
			}
		}
	}
	return false
}

func contains(list []string, v string) bool {
	for _, s := range list {
		if s == v {
			return true
		}
	}
	return false
}
