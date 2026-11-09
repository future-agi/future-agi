package tenancy

import "github.com/futureagi/agentcc-gateway/internal/tenant"

// A lifecycle/config-state change is not a new grant. In particular, a stale
// expiry, header placement or active flag cannot reauthorize evicted material.
// A replacement vault identity or secret value is a distinct grant.
func pushedGrantIdentity(id string, secret *tenant.MCPCredentialSecret, legacy *tenant.MCPAuthOrgConfig) string {
	if secret != nil && secret.Value != "" {
		return fingerprint([]string{id, secret.Value})
	}
	if legacy != nil && (legacy.Token != "" || legacy.Key != "") {
		return fingerprint([]string{id, legacy.Token, legacy.Key})
	}
	return ""
}
