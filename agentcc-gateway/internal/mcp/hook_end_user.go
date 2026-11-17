package mcp

import (
	"context"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors/enduser"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const hookResolveEndUser = "resolveEndUser"
const CodeEndUserIdentityRequired = "end_user_identity_required"

type endUserContextKey struct{}
type EndUserIdentity = enduser.Identity

// VendorIdentityPolicy is optional. Without it only authenticated member
// identities are accepted. Django checks its authoritative binding again.
type VendorIdentityPolicy interface{ AllowAsserted(*CallContext) bool }

func EndUserOf(cc *CallContext) (EndUserIdentity, bool) {
	identity, ok := cc.Context().Value(endUserContextKey{}).(EndUserIdentity)
	return identity, ok
}

func vendorCredential(cc *CallContext) *tenant.MCPServerCredential {
	if cc.OrgConfig == nil || cc.OrgConfig.MCP == nil {
		return nil
	}
	sc := cc.OrgConfig.MCP.Servers[cc.Upstream.ServerID]
	if sc == nil || sc.Credential == nil || sc.Credential.Mode != "vendor_end_user" {
		return nil
	}
	return sc.Credential
}

// resolveEndUser binds a vendor call to a principal, never to the key's
// creator. No dependency is installed by default. The pool/URL/cache integration
// remains gated until the accepted lifetime and invalidation implementation lands.
func resolveEndUser(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolveEndUser)
	if vendorCredential(cc) == nil {
		return nil
	}
	deps := s.Deps()
	if _, off := deps.VendorCredentials.(noVendorCredentials); off || deps.VendorCredentials == nil {
		return CredentialUnavailable()
	}
	allow := false
	if policy, ok := deps.VendorCredentials.(VendorIdentityPolicy); ok {
		allow = policy.AllowAsserted(cc)
	}
	identity, err := enduser.Resolve(enduser.Principal{Kind: string(cc.Principal.Kind), OrgID: cc.Principal.OrgID, UserID: cc.Principal.UserID}, cc.Agent.EndUser, enduser.Policy{AllowAsserted: allow})
	if err != nil {
		return &Denial{Type: "auth_required", Code: err.Error(), Message: "An authorized end-user identity is required for this connector."}
	}
	cc.ctx = context.WithValue(cc.Context(), endUserContextKey{}, identity)
	cc.SetEvidence("io.futureagi/end_user", map[string]string{"hash": identity.Hash, "source": identity.Source})
	// A legacy shared pool must never execute this mode, even when an adapter
	// is injected. Stage 2 makes vendor servers explicitly per-end-user.
	pool := TenantPoolOf(cc)
	if pool == nil {
		return CredentialUnavailable()
	}
	ps, ok := pool.Server(cc.Upstream.ServerID)
	if !ok || !ps.PerUser {
		return CredentialUnavailable()
	}
	// Even a test-injected per-user pool cannot fall through step 13's legacy
	// resolver. Stage 2 removes this barrier together with vendor dispatch and
	// the accepted grant-lifetime fence, never as an independent flag flip.
	return CredentialUnavailable()
}
