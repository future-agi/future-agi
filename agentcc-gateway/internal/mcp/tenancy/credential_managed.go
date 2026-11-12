package tenancy

import (
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func managedCredential(cfg *tenant.OrgConfig, id string) *tenant.MCPManagedToolCredential {
	if cfg == nil || cfg.MCP == nil || cfg.MCP.ManagedTools == nil {
		return nil
	}
	return cfg.MCP.ManagedTools.Credentials[id]
}

// A customer credential on a supplied server has the same lifetime as an org
// server's. A lapsed customer credential never silently selects operator auth.
func bindManagedCredential(sp spec, cfg *tenant.OrgConfig) (spec, error) {
	c := managedCredential(cfg, sp.id)
	sp.credentialFP = fingerprint(c)
	if c == nil {
		return sp, nil
	}
	sp.credentialRequired = true
	sp.credentialGrant = pushedGrantIdentity(c.CredentialID, c.Secret, nil)
	sp.credentialRevision = c.Revision
	sp.credentialState = orDefault(c.State, "active")
	sp.credentialExpiry = c.ExpiresAt
	sp.perUser = c.Mode == "per_user"
	sp.client.Auth = mcp.AuthConfig{}
	if !sp.perUser && sp.credentialState == "active" {
		if c.Secret == nil || c.Secret.Value == "" {
			sp.credentialState = "needs_reauth"
		} else {
			auth, err := sharedAuth(tenant.MCPServerSettings{Credential: tenant.MCPServerCredential{Secret: c.Secret}}, nil)
			if err != nil {
				return sp, err
			}
			sp.client.Auth = auth
		}
	}
	sp.fp = fingerprint([]string{sp.fp, sp.credentialFP})
	return sp, nil
}
