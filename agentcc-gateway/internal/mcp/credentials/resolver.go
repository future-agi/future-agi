package credentials

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/url"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// TokenSource hands out a user's current upstream token (TokenClient over
// mcp-upstream-token-v1; a stand-in in tests).
type TokenSource interface {
	Fetch(ctx context.Context, k Key) (Token, error)
}

// Resolver is the gateway's per-user credential step (Deps.CredentialResolver,
// step 13 of tools/call). On a per_user server it resolves the caller (CallerOf),
// checks the caller's current vault state with Django just in time, and hands
// its token and lifetime to the call as an Authorization: Bearer override. With
// no user identity, no token, or a credential that needs connecting again it
// answers auth_required with a connect link; a shared server keeps the
// credential the org pushed.
//
// It is also Deps.SharedCredentials: a shared OAuth server's client
// takes the org's signed-in account's token from it as each request is sent
// (SharedAuth), under the org's own shared key, and step 13 answers
// auth_required, with the server's connect page and no person's binding,
// when there is none to use.
type Resolver struct {
	tokens     TokenSource
	cache      *TokenCache
	codec      *mcp.StateCodec // seals connect links
	djangoBase string
	now        func() time.Time
	flights    flightGroup
	users      userLifetimes
}

// ResolverOptions build a Resolver.
type ResolverOptions struct {
	// Tokens fetches tokens from Django. Required.
	Tokens TokenSource
	// Cache keeps tokens sealed between calls; nil fetches on every call.
	Cache *TokenCache
	// Codec seals connect links when ConnectCodec is nil. Required.
	Codec *mcp.StateCodec
	// ConnectCodec seals connect links with the key Django's start page
	// opens them with (ConnectCodec: derived from the secret the gateway
	// shares with Django). nil: Codec seals them.
	ConnectCodec *mcp.StateCodec
	// DjangoBaseURL is where a connect link points when Django names no
	// start page (mcp.django.base_url).
	DjangoBaseURL string
	// Now is the clock that judges a fetched token's expiry (time.Now).
	Now func() time.Time
}

// NewResolver returns the resolver.
func NewResolver(o ResolverOptions) (*Resolver, error) {
	if o.Tokens == nil || o.Codec == nil {
		return nil, errors.New("mcp credentials: a resolver needs a token source and a state codec")
	}
	if o.Now == nil {
		o.Now = time.Now
	}
	links := o.Codec
	if o.ConnectCodec != nil {
		links = o.ConnectCodec
	}
	return &Resolver{tokens: o.Tokens, cache: o.Cache, codec: links, djangoBase: o.DjangoBaseURL, now: o.Now}, nil
}

// Resolve is mcp.CredentialResolver.
func (r *Resolver) Resolve(cc *mcp.CallContext) (*mcp.AuthOverride, *mcp.Denial) {
	pool := mcp.TenantPoolOf(cc)
	if pool == nil {
		return nil, nil
	}
	ps, ok := pool.Server(cc.Upstream.ServerID)
	if ok && ps.SharedOAuth {
		ref, d := r.resolveShared(cc.Context(), pool.OrgID(), ps.ID)
		if d != nil {
			return nil, d
		}
		cc.Credential = ref
		return nil, nil // the server's client takes the token as it sends (SharedAuth)
	}
	if !ok || !ps.PerUser {
		return nil, nil // a shared server keeps the org's pushed credential
	}
	auth, ref, d := r.resolveUser(cc.Context(), cc.Principal, pool.OrgID(), ps.ID)
	if d != nil {
		return nil, d
	}
	cc.Credential = ref
	return auth, nil
}

// resolveUser is Resolve on a per_user server serverID of the pool of org
// poolOrg: the caller's own token as a bearer override, or the refusal.
func (r *Resolver) resolveUser(ctx context.Context, p mcp.Principal, poolOrg, serverID string) (*mcp.AuthOverride, mcp.CredentialRef, *mcp.Denial) {
	caller, ok := CallerOf(p)
	if !ok || caller.OrgID != poolOrg {
		return nil, mcp.CredentialRef{}, mcp.AuthRequired(serverID, mcp.AuthReasonNoUserIdentity, "")
	}
	k := KeyFor(caller, serverID)
	if !k.valid() {
		return nil, mcp.CredentialRef{}, mcp.CredentialUnavailable()
	}
	auth, err := r.userAuth(ctx, k)
	var refusal *Refusal
	switch {
	case errors.As(err, &refusal):
		if d := policyDenial(refusal); d != nil {
			return nil, mcp.CredentialRef{}, d
		}
		reason := authReason(refusal.Reason)
		return nil, mcp.CredentialRef{}, mcp.AuthRequired(serverID, reason, r.connectURL(k, refusal.ConnectHint))
	case err != nil:
		slog.Warn("mcp per-user credential unavailable; the call was not made", "org", k.OrgID, "server", k.ServerID, "error", err)
		return nil, mcp.CredentialRef{}, mcp.CredentialUnavailable()
	}
	ref := mcp.CredentialRef{Source: mcp.CredentialUser, Ref: "user:" + caller.UserID, State: "active"}
	return auth, ref, nil
}

// resolveShared is Resolve on a shared OAuth server serverID of org: whether
// the org's signed-in account has a token to use, or the refusal. The token
// is left in the cache for the server's client.
func (r *Resolver) resolveShared(ctx context.Context, org, serverID string) (mcp.CredentialRef, *mcp.Denial) {
	k := SharedKey(org, serverID)
	if !k.valid() {
		return mcp.CredentialRef{}, mcp.CredentialUnavailable()
	}
	_, err := (sharedSource{r: r, k: k}).Credential(ctx)
	var refusal *Refusal
	switch {
	case errors.As(err, &refusal):
		if d := policyDenial(refusal); d != nil {
			return mcp.CredentialRef{}, d
		}
		return mcp.CredentialRef{}, mcp.AuthRequired(serverID, authReason(refusal.Reason), r.sharedConnectURL(serverID, refusal.ConnectHint))
	case err != nil:
		slog.Warn("mcp shared OAuth credential unavailable; the call was not made", "org", org, "server", serverID, "error", err)
		return mcp.CredentialRef{}, mcp.CredentialUnavailable()
	}
	return mcp.CredentialRef{Source: mcp.CredentialOrgShared, Ref: "shared:oauth", State: "active"}, nil
}

// SharedAuth is mcp.SharedCredentials: the headers of orgID's signed-in
// account on serverID, its token from the cache or fetched just in time.
func (r *Resolver) SharedAuth(orgID, serverID string) mcp.HeaderSource {
	return sharedSource{r: r, k: SharedKey(orgID, serverID)}
}

type sharedSource struct {
	r *Resolver
	k Key
}

func (s sharedSource) Header(ctx context.Context) (http.Header, error) {
	if !s.k.valid() {
		return nil, ErrBadKey
	}
	tok, err := s.r.token(ctx, s.k)
	if err != nil {
		return nil, err // a Refusal or ErrTokenChannel: neither carries the token
	}
	return http.Header{"Authorization": {"Bearer " + tok.AccessToken}}, nil
}

// sharedConnectURL is where an Owner or Admin signs the org's account in on
// serverID: Django's start page (hint), else the server's on the Django
// address. It carries no binding: a binding names a person.
func (r *Resolver) sharedConnectURL(serverID, hint string) string {
	for _, base := range []string{hint, DefaultConnectBase(r.djangoBase, serverID)} {
		if base == "" {
			continue
		}
		if u, err := url.Parse(base); err == nil && (u.Scheme == "http" || u.Scheme == "https") && u.Host != "" && u.User == nil {
			u.RawQuery, u.Fragment = "", ""
			return u.String()
		}
	}
	return ""
}

// connectURL is k's connect link on Django's start page (hint), else on the
// server's start page at the Django address; "" when neither is usable.
func (r *Resolver) connectURL(k Key, hint string) string {
	for _, base := range []string{hint, DefaultConnectBase(r.djangoBase, k.ServerID)} {
		if base == "" {
			continue
		}
		if u, err := ConnectURL(r.codec, base, k); err == nil {
			return u
		}
	}
	return ""
}

// PurgeCredentials is mcp.CredentialPurger: it drops the cached tokens of
// serverID in orgID, userID's only or every user's when userID is "".
func (r *Resolver) PurgeCredentials(ctx context.Context, orgID, userID, serverID string) (int, error) {
	k := Key{OrgID: orgID, UserID: userID, ServerID: serverID, Shared: userID == ""}
	if !k.valid() {
		return 0, mcp.ErrInvalidCredentialKey
	}
	var n int
	var err error
	if r.cache != nil {
		n, err = r.cache.Purge(ctx, orgID, userID, serverID)
	}
	// Compare epochs, not identity alone: a new grant issued during eviction
	// must survive a delayed notification or the purger's local completion.
	if r.cache != nil && err == nil {
		r.cache.checkLifetimes()
	} else {
		// Without a distributed authority (or on failure), fail closed locally.
		r.purgeUserLifetimes(orgID, userID, serverID)
	}
	return n, err
}

// policyDenial is the refusal of a call whose token Django will not hand out
// for a reason connecting does not fix: "unavailable", or Microsoft's hold,
// refused in Django's own sentence (mcp.CredentialHeld). nil for a refusal
// the user remedies by connecting (auth_required).
func policyDenial(r *Refusal) *mcp.Denial {
	switch r.Reason {
	case "unavailable":
		return mcp.CredentialUnavailable()
	case RefusalMicrosoftComingSoon:
		return mcp.CredentialHeld(r.Message)
	}
	return nil
}

// authReason is the in-band auth_required reason for a Django refusal. A revoked
// credential, like one that needs re-authorizing, is connected again; a
// refusal this build does not know is treated the same way.
func authReason(refusal string) string {
	switch refusal {
	case RefusalNoCredential:
		return mcp.AuthReasonNoToken
	case RefusalExpired:
		return mcp.AuthReasonExpired
	default:
		return mcp.AuthReasonNeedsReauth
	}
}

// flightGroup joins fetches only within the same credential epoch.
type tokenFlightKey struct {
	key        Key
	generation uint64
	epoch      string
}
type flightGroup struct {
	mu    sync.Mutex
	calls map[tokenFlightKey]*flight
}

type flight struct {
	done chan struct{}
	tok  Token
	err  error
}

func (g *flightGroup) do(k tokenFlightKey, fn func() (Token, error)) (Token, error) {
	g.mu.Lock()
	if f := g.calls[k]; f != nil {
		g.mu.Unlock()
		<-f.done
		return f.tok, f.err
	}
	if g.calls == nil {
		g.calls = map[tokenFlightKey]*flight{}
	}
	// A fetch that panics leaves the channel error, never an empty token.
	f := &flight{done: make(chan struct{}), err: ErrTokenChannel}
	g.calls[k] = f
	g.mu.Unlock()

	defer func() {
		g.mu.Lock()
		delete(g.calls, k)
		g.mu.Unlock()
		close(f.done)
	}()
	f.tok, f.err = fn()
	return f.tok, f.err
}
