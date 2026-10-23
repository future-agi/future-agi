package middleware

import (
	"context"
	"net"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/httppath"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
)

// KeyAuth returns middleware that validates API keys from the Authorization
// header before any handler runs. It gates every "/v1/" route, and every path
// at or under one of gatedRoots (the gateway passes the MCP endpoint and
// /a2a). Every other route runs its own checks: the other API routes
// authenticate the key in the request pipeline, the admin routes require the
// admin token, and the health probes and the A2A agent card are public.
//
// The decision is made on the canonical path (httppath.Canonical). The router
// rejects every non-canonical path, so the path a handler runs on always
// equals the path this gate decided on.
//
// On every gated path it also applies the IP rules the request pipeline
// applies, from the same client IP (ClientIP): the key's allowed_ips, and
// with ip_acl on, the gateway's ip_acl and the ip_acl of the key's org.
// KeyAuth applies them itself, so every gated route meets them. A request
// with no key to check (a license token, or auth off) still meets the
// gateway's ip_acl when it is on.
//
// On success it records the key's identity (KeyIdentity) in the request
// context; handlers that scope data to the caller's org read it from there
// instead of looking the key up again.
//
// If keyStore is nil, auth is considered disabled and all requests pass through.
func KeyAuth(keyStore *auth.KeyStore, enabled bool, ipACL *ipacl.Plugin, gatedRoots ...string) func(http.Handler) http.Handler {
	authEnabled := enabled
	gate := newKeyGate(gatedRoots)

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Skip auth for non-API routes (health, admin, etc.).
			if !gate.covers(r.URL.Path) {
				next.ServeHTTP(w, r)
				return
			}

			clientIP := net.ParseIP(ClientIP(r))
			requestID := models.GetRequestID(r.Context())

			// The gateway's ip_acl comes first, as in the pipeline (ipacl runs
			// before auth): a denied address learns nothing about a key, and a
			// request with no key to check (a license token, or auth off)
			// still meets it.
			if err := ipACL.CheckClient(clientIP, "", nil, requestID); err != nil {
				models.WriteError(w, err)
				return
			}
			if IsLicenseAuthorized(r.Context()) || !authEnabled || keyStore == nil {
				next.ServeHTTP(w, r)
				return
			}

			rawKey := RequestAPIKey(r)
			if rawKey == "" {
				models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
				return
			}

			apiKey := keyStore.Authenticate(rawKey)
			if apiKey == nil {
				models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
				return
			}

			// Then the rules CheckClient applies to this key and its org.
			identity := keyStore.Identity(apiKey)
			if err := ipACL.CheckClient(clientIP, identity.OrgID, apiKey.AllowedIPs, requestID); err != nil {
				models.WriteError(w, err)
				return
			}

			ctx := context.WithValue(r.Context(), keyIdentityContextKey{}, identity)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

type keyIdentityContextKey struct{}

// KeyIdentity is the identity of the API key KeyAuth authenticated for this
// request, as of that moment. ok is false when KeyAuth authenticated
// no key: auth is off, a license token authorized the request, or the path is
// not key-gated.
func KeyIdentity(ctx context.Context) (auth.Identity, bool) {
	id, ok := ctx.Value(keyIdentityContextKey{}).(auth.Identity)
	return id, ok
}

// WithAuthorizationOf returns ctx carrying the authorization KeyAuth and
// LicenseAuth recorded on from: the key identity, or the license flag and
// claims. Work that outlives its request, such as an async job, runs under
// the authorization its request was admitted with and nothing else from that
// request's context.
func WithAuthorizationOf(ctx, from context.Context) context.Context {
	if id, ok := from.Value(keyIdentityContextKey{}).(auth.Identity); ok {
		ctx = context.WithValue(ctx, keyIdentityContextKey{}, id)
	}
	if IsLicenseAuthorized(from) {
		ctx = context.WithValue(ctx, licenseAuthContextKey{}, true)
		if claims, ok := from.Value(licenseClaimsContextKey{}).(*licenseClaims); ok && claims != nil {
			ctx = context.WithValue(ctx, licenseClaimsContextKey{}, claims)
		}
	}
	return ctx
}

// isV1Path reports whether a request path belongs to the /v1 API, judged on
// its canonical form so no spelling of a /v1 path escapes a /v1 gate.
func isV1Path(p string) bool {
	return strings.HasPrefix(httppath.Canonical(p), "/v1/")
}

// RequestAPIKey is the raw API key KeyAuth authenticates: a Bearer
// Authorization header, otherwise x-api-key. Handlers that need the caller's
// key use it so they read the same key KeyAuth checked.
func RequestAPIKey(r *http.Request) string {
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		return strings.TrimPrefix(authHeader, "Bearer ")
	}
	if apiKey := r.Header.Get("x-api-key"); apiKey != "" {
		return apiKey
	}
	return ""
}
