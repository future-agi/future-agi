package server

import (
	"encoding/json"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	anthropictrans "github.com/futureagi/agentcc-gateway/internal/translation/anthropic"
)

// messagesRoutingChain reports whether native pass-through would skip a chain
// that chat runs. These requests use canonical attempts and Messages rendering;
// a single native call keeps its lossless pass-through (including beta fields).
func (h *Handlers) messagesRoutingChain(rc *models.RequestContext, orgCfg *tenant.OrgConfig) bool {
	if rc.Metadata["auto_model"] != "" || len(h.effectiveFallbackPlan(rc, rc.FallbackRoute())) > 0 ||
		h.effectiveFailover(orgCfg).IsEnabled() ||
		(orgCfg != nil && orgCfg.Routing != nil && customerRoutingOn(orgCfg.Routing)) {
		return true
	}
	return false
}

func messagesRoutingTranslationUnsupported() *models.APIError {
	return models.ErrBadRequest("messages_routing_translation_unsupported",
		"This Messages request contains native-only fields that cannot be preserved by the configured routing chain. Use a canonical-compatible Messages request or a route without automatic routing, retries or fallbacks.")
}

// messagesToolResultTranslationError applies the translator's text-only nested
// tool-result boundary to ALL canonical routes, not only native providers with
// routing enabled. Keep this separate from the native-field allowlist below:
// canonical routes also support gateway metadata such as session_id.
func messagesToolResultTranslationError(body []byte) *models.APIError {
	var req anthropictrans.MessagesRequest
	if json.Unmarshal(body, &req) != nil {
		return models.ErrBadRequest("invalid_request", "Invalid Messages request")
	}
	for _, turn := range req.Messages {
		if messagesString(turn.Content) {
			continue
		}
		var blocks []anthropictrans.ContentBlock
		if json.Unmarshal(turn.Content, &blocks) != nil {
			return models.ErrBadRequest("invalid_request", "Invalid Messages content")
		}
		for _, block := range blocks {
			if block.Type == "tool_result" && block.Content != nil && !messagesRoutableContent(block.Content, true) {
				return messagesRoutingTranslationUnsupported()
			}
		}
	}
	return nil
}

// messagesRoutedTranslationError refuses native-only semantics before sending
// a native request through the cross-provider routing chain. The canonical
// translator intentionally supports a subset of Messages; it cannot preserve
// thinking signatures, server tools, beta fields or every content-block type.
// Without a chain those bytes still take the native pass-through. Do not turn
// a configured chain off to accommodate them, or silently discard them.
func messagesRoutedTranslationError(body []byte) *models.APIError {
	var top map[string]json.RawMessage
	if json.Unmarshal(body, &top) != nil {
		return models.ErrBadRequest("invalid_request", "Invalid Messages request")
	}
	refuse := messagesRoutingTranslationUnsupported
	if !messagesOnlyFields(top, "model messages max_tokens stream system temperature top_p stop_sequences tools tool_choice") {
		return refuse()
	}
	// System arrays carry cache-control or other native annotations which the
	// outbound adapters cannot all preserve. Plain strings are lossless.
	if raw := top["system"]; raw != nil && !messagesString(raw) {
		return refuse()
	}
	var turns []map[string]json.RawMessage
	if json.Unmarshal(top["messages"], &turns) != nil {
		return refuse()
	}
	for _, turn := range turns {
		if !messagesOnlyFields(turn, "role content") || !messagesRoutableContent(turn["content"], false) {
			return refuse()
		}
	}
	if raw := top["tools"]; raw != nil {
		var tools []map[string]json.RawMessage
		if json.Unmarshal(raw, &tools) != nil {
			return refuse()
		}
		for _, tool := range tools {
			if !messagesOnlyFields(tool, "name description input_schema") {
				return refuse()
			}
		}
	}
	if raw := top["tool_choice"]; raw != nil {
		var choice map[string]json.RawMessage
		if json.Unmarshal(raw, &choice) != nil || !messagesOnlyFields(choice, "type name") {
			return refuse()
		}
		var kind string
		_ = json.Unmarshal(choice["type"], &kind)
		if kind != "auto" && kind != "any" && kind != "none" && kind != "tool" {
			return refuse()
		}
	}
	return nil
}

func messagesOnlyFields(object map[string]json.RawMessage, allowed string) bool {
	fields := strings.Fields(allowed)
	for key := range object {
		if !slices.Contains(fields, key) {
			return false
		}
	}
	return true
}

func messagesString(raw json.RawMessage) bool {
	var text string
	return len(raw) > 0 && raw[0] == '"' && json.Unmarshal(raw, &text) == nil
}

// toolResult restricts nested blocks to the text that toolResultContent in
// the canonical translator preserves. Top-level images remain routable.
func messagesRoutableContent(raw json.RawMessage, toolResult bool) bool {
	if messagesString(raw) {
		return true
	}
	var blocks []map[string]json.RawMessage
	if json.Unmarshal(raw, &blocks) != nil {
		return false
	}
	for _, block := range blocks {
		var kind string
		_ = json.Unmarshal(block["type"], &kind)
		if toolResult && kind != "text" {
			return false
		}
		switch kind {
		case "text":
			if !messagesOnlyFields(block, "type text") {
				return false
			}
		case "image":
			var source map[string]json.RawMessage
			if !messagesOnlyFields(block, "type source") || json.Unmarshal(block["source"], &source) != nil ||
				!messagesOnlyFields(source, "type media_type data url") {
				return false
			}
		case "tool_use":
			if !messagesOnlyFields(block, "type id name input") {
				return false
			}
		case "tool_result":
			if !messagesOnlyFields(block, "type tool_use_id content") || !messagesRoutableContent(block["content"], true) {
				return false
			}
		default:
			return false
		}
	}
	return true
}
