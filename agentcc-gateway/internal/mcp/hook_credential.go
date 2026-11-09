package mcp

import (
	"context"
	"errors"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// hookResolveCredential is step 13 of the tools/call chain (chain.go).
const hookResolveCredential = "resolveCredential"

// auth_required (MC.3) and its reasons.
const (
	CodeAuthRequired         = "auth_required"
	AuthReasonNoToken        = "no_token"
	AuthReasonExpired        = "expired"
	AuthReasonNeedsReauth    = "needs_reauth"
	AuthReasonNoUserIdentity = "no_user_identity"
)

// resolveCredential picks the call's upstream credential: Deps.TokenExchanger
// first, for the exchange and id_jag modes, else Deps.CredentialResolver (M25's
// per-user tokens), either of which may refuse, with auth_required among
// others. A shared server keeps its configured credential. A call on a
// per_user server runs only with the calling user's own credential: the
// call's pool is bound to it for that server, so the upstream client the call
// goes through is that user's, built with that credential (TenantPool.Client),
// and a per_user call with no credential resolved is refused before anything
// is sent.
//
// Owner: M25. Refusal: auth_required, policy_unavailable.
// Keep observeHook as its first statement.
func resolveCredential(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolveCredential)
	deps := s.Deps()
	auth, handled, d := deps.TokenExchanger.Exchange(cc)
	if d != nil {
		return d
	}
	if !handled {
		if auth, d = deps.CredentialResolver.Resolve(cc); d != nil {
			return d
		}
	}
	pool := TenantPoolOf(cc)
	if pool == nil {
		return nil
	}
	ps, ok := pool.Server(cc.Upstream.ServerID)
	if !ok || !ps.PerUser {
		return nil
	}
	user := cc.Principal.UserID
	switch {
	case user == "":
		return AuthRequired(ps.ID, AuthReasonNoUserIdentity, "")
	case auth == nil || len(auth.Header) == 0:
		return CredentialUnavailable()
	}
	resolved := *auth
	resolved.Header = auth.Header.Clone()
	cc.ctx = context.WithValue(cc.Context(), tenantPoolKey{}, &credentialPool{
		TenantPool: pool, serverID: ps.ID, credKey: user, auth: resolved,
	})
	return nil
}

// credentialPool is the call's pool once step 13 resolved the caller's
// credential for the call's server: a client asked for that server and user
// with no credential of its own is built with the caller's. Everything else
// passes through.
type credentialPool struct {
	TenantPool
	serverID string
	credKey  string
	auth     AuthOverride
}

func (p *credentialPool) Client(serverID, credKey string, auth AuthOverride) (UpstreamClient, error) {
	if serverID == p.serverID && credKey == p.credKey && len(auth.Header) == 0 {
		auth = p.auth
		auth.Header = p.auth.Header.Clone()
	}
	return p.TenantPool.Client(serverID, credKey, auth)
}

// AuthRequired is MC.3's auth_required for serverID: the user must connect
// an account (or connect it again) at connectURL, or, for
// no_user_identity, call as a user. connectURL may be "".
func AuthRequired(serverID, reason, connectURL string) *Denial {
	meta := map[string]any{"server_id": serverID, "reason": reason}
	if connectURL != "" {
		meta["connect_url"] = connectURL
	}
	return &Denial{Type: CodeAuthRequired, Code: CodeAuthRequired, Message: authRequiredMessage(reason), Metadata: meta}
}

func authRequiredMessage(reason string) string {
	switch reason {
	case AuthReasonNoUserIdentity:
		return "This server uses each person's own account. Sign in with OAuth, or use a personal key, to use its tools."
	case AuthReasonExpired:
		return "Your connection to this server has expired. Connect your account again to use its tools."
	case AuthReasonNeedsReauth:
		return "Connect your account for this server again to use its tools."
	default:
		return "Connect your account for this server to use its tools."
	}
}

// CredentialUnavailable refuses a per_user call whose credential could not
// be resolved (the credential service is unreachable or not configured): the
// call is not made, and no other credential is used in its place.
func CredentialUnavailable() *Denial {
	return &Denial{
		Type:    models.ErrTypeServer,
		Code:    CodePolicyUnavailable,
		Message: "this server's per-user credential could not be resolved; the call was not made",
	}
}

// CredentialPurger is a CredentialResolver that keeps users' upstream
// tokens: POST /-/mcp/credentials/purge drops serverID's in orgID, userID's
// only, or every user's when userID is "". An id it cannot name a credential
// by is ErrInvalidCredentialKey.
type CredentialPurger interface {
	PurgeCredentials(ctx context.Context, orgID, userID, serverID string) (int, error)
}

// ErrInvalidCredentialKey is an org, user or server id that names no
// credential: it has no valid shape.
var ErrInvalidCredentialKey = errors.New("mcp credentials: org, user or server id has no valid shape")

// configCredential is Deps.CredentialResolver's default: each server keeps
// its configured credential, and a per_user server, which has none, is
// refused at step 13.
type configCredential struct{}

func (configCredential) Resolve(*CallContext) (*AuthOverride, *Denial) { return nil, nil }

// ErrSharedCredentialsOff is noSharedCredentials' answer: this gateway has no
// channel to the control plane for the organization's signed-in account.
var ErrSharedCredentialsOff = errors.New("mcp: this server signs in the organization's account over OAuth, and the gateway has no channel to fetch its token (mcp.django.base_url)")

// noSharedCredentials is Deps.SharedCredentials' default: no request to a
// shared OAuth server is sent.
type noSharedCredentials struct{}

func (noSharedCredentials) SharedAuth(string, string) HeaderSource { return noSharedSource{} }

type noSharedSource struct{}

func (noSharedSource) Header(context.Context) (http.Header, error) {
	return nil, ErrSharedCredentialsOff
}

// noTokenExchange is Deps.TokenExchanger's default: it never applies.
type noTokenExchange struct{}

func (noTokenExchange) Exchange(*CallContext) (*AuthOverride, bool, *Denial) { return nil, false, nil }

// noVendorCredentials is Deps.VendorCredentials' default: the configured
// credential.
type noVendorCredentials struct{}

func (noVendorCredentials) Resolve(*CallContext, string) (*AuthOverride, *Denial) { return nil, nil }
