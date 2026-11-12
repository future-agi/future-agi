package vendors

import (
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// K2 currently uses org OAuth clients. This is not an availability default:
// neither a provider's clientGroup nor its adapter label implies a source.
// A future platform-client path needs its own authoritative source binding.
const k2ClientSource = "org"

// providerClientSource requires an explicit, wholly understood declaration
// on this exact provider record. Unknown entries invalidate the declaration,
// including when accompanied by an otherwise supported source.
func providerClientSource(p *manifest.Provider, source string) bool {
	if p == nil || p.Auth.OAuth2 == nil || (source != "org" && source != "platform") {
		return false
	}
	found := false
	for _, declared := range p.Auth.OAuth2.ClientSources {
		if declared != "org" && declared != "platform" {
			return false
		}
		if declared == source {
			found = true
		}
	}
	return found
}
