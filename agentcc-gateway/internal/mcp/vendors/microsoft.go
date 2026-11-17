package vendors

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// microsoftProvider reads the existing delegated Entra manifest contract;
// it owns no OAuth endpoints, tenant registry, scope table or app credentials.
// The bound control-plane admission must additionally assure Delegated.
func microsoftProvider(p *manifest.Provider) bool {
	if !nativeProvider(p, "microsoft") {
		return false
	}
	for _, scope := range p.Auth.OAuth2.Scopes {
		if strings.HasSuffix(scope, "/.default") {
			return false
		}
	}
	tenant, ok := p.Auth.OAuth2.URLVariables["tenant"]
	return ok && tenant.Scope == "org" && len(p.Auth.OAuth2.Scopes) > 0
}
