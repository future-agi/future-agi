package common

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ToolPolicySupport describes the controls a translating adapter can carry.
// Unsupported restrictions must fail before any upstream work, not default to auto.
type ToolPolicySupport struct {
	Provider       string
	ParallelCalls  bool
	RequiredChoice bool
	NamedChoice    bool
	NoneChoice     bool
}

// PrepareDirectToolPolicy returns an independent request for adapters whose
// functions can only be called directly. Anthropic caller contexts cannot be
// expressed there, even when an OpenAI-compatible upstream ignores unknown fields.
func PrepareDirectToolPolicy(req *models.ChatCompletionRequest, support ToolPolicySupport) (*models.ChatCompletionRequest, error) {
	out := *req
	out.Tools = append([]models.Tool(nil), req.Tools...)
	for i, tool := range out.Tools {
		if tool.AllowedCallers != nil {
			if len(tool.AllowedCallers) != 1 || tool.AllowedCallers[0] != "direct" {
				return nil, models.ErrBadRequest("unsupported_tool_policy", support.Provider+" cannot enforce allowed_callers")
			}
			out.Tools[i].AllowedCallers = nil
		}
	}
	allowed, err := models.ParseAllowedToolsChoice(req.ToolChoice)
	if err != nil {
		return nil, models.ErrBadRequest("invalid_tool_choice", err.Error())
	}
	if allowed != nil {
		out.Tools, err = allowed.Filter(out.Tools)
		if err != nil {
			return nil, models.ErrBadRequest("invalid_tool_choice", err.Error())
		}
		out.ToolChoice, _ = json.Marshal(allowed.Mode)
	}
	var choice string
	if len(out.ToolChoice) > 0 && string(out.ToolChoice) != "null" {
		if json.Unmarshal(out.ToolChoice, &choice) == nil {
			switch choice {
			case "none":
				// These adapters can omit declarations, unlike Anthropic message history
				// which may depend on earlier tool_use declarations remaining present.
				if !support.NoneChoice {
					out.Tools = nil
					out.ToolChoice = nil
				}
			case "auto":
			case "required":
				if !support.RequiredChoice {
					return nil, models.ErrBadRequest("unsupported_tool_policy", support.Provider+" cannot enforce required tool_choice")
				}
			default:
				return nil, models.ErrBadRequest("invalid_tool_choice", "unsupported tool_choice mode")
			}
		} else if !support.NamedChoice {
			return nil, models.ErrBadRequest("unsupported_tool_policy", support.Provider+" cannot enforce named tool_choice")
		}
	}
	if !support.ParallelCalls && req.ParallelToolCalls != nil && !*req.ParallelToolCalls && len(out.Tools) > 0 {
		return nil, models.ErrBadRequest("unsupported_tool_policy", support.Provider+" cannot enforce parallel_tool_calls=false")
	}
	return &out, nil
}
