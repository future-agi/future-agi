package capability

import (
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// BYOKState is whether an org holds its own credential for a vendor.
type BYOKState int

const (
	// BYOKNone: the org never connected the vendor.
	BYOKNone BYOKState = iota
	// BYOKLapsed: the org connected the vendor, but the entry is disabled or
	// carries no credential. A lapsed credential never moves a call to
	// managed unless mcp.managed_tools.fallback_to_managed is set.
	BYOKLapsed
	// BYOKUsable: the org's own credential is there and enabled.
	BYOKUsable
)

// Credentials resolves the credential a vendor call uses.
type Credentials interface {
	// BYOK is the org's own credential for vendor (the org's
	// providers.<vendor> entry, through the BYOK provider-key push).
	BYOK(org *tenant.OrgConfig, vendor string) (Credential, BYOKState)
	// Managed is the operator's managed credential for vendor, the one
	// FutureAGI-supplied calls use.
	Managed(vendor string) (Credential, bool)
}

// ConfigCredentials reads both from config: the org's providers.<vendor>
// entry for BYOK, and the operator's providers.<vendor> entry marked
// managed: true (config.yaml) for managed. The vendor's endpoint is the
// operator entry's base_url; an org entry's own base_url (a customer-hosted
// endpoint) is not dialled here: it needs gw/netguard's private-address
// guard, which the adapter that accepts one applies.
type ConfigCredentials struct {
	Operator map[string]config.ProviderConfig
}

// BYOK implements Credentials.
func (c ConfigCredentials) BYOK(org *tenant.OrgConfig, vendor string) (Credential, BYOKState) {
	if org == nil {
		return Credential{}, BYOKNone
	}
	pc := org.Providers[vendor]
	switch {
	case pc == nil:
		return Credential{}, BYOKNone
	case !pc.Enabled || pc.APIKey == "":
		return Credential{}, BYOKLapsed
	}
	return Credential{Type: models.CredentialBYOK, APIKey: pc.APIKey, BaseURL: c.Operator[vendor].BaseURL}, BYOKUsable
}

// Managed implements Credentials.
func (c ConfigCredentials) Managed(vendor string) (Credential, bool) {
	pc, ok := c.Operator[vendor]
	if !ok || !pc.Managed || pc.APIKey == "" {
		return Credential{}, false
	}
	return Credential{Type: models.CredentialManaged, APIKey: pc.APIKey, BaseURL: pc.BaseURL}, true
}
