package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// managedEndpoint reports whether managed models serve rc's endpoint: chat
// completions, and the Anthropic Messages API (/v1/messages), which reaches
// a managed provider only through the canonical chat path, where the
// managed allowlist and the upstream rewrite run and the upstream's
// reported cost is billed (AnthropicMessages). The other endpoints neither
// rewrite the upstream model ID nor bill an upstream-reported cost, so a
// managed request there would be mis-routed or mis-billed;
// /v1/messages/count_tokens ("anthropic_count_tokens") stays with the
// org's own providers.
func managedEndpoint(rc *models.RequestContext) bool {
	switch rc.EndpointType {
	case "", "chat", "anthropic_messages":
		return true
	}
	return false
}
