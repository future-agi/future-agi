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
	"github.com/futureagi/agentcc-gateway/internal/providers/speech"
)

// KeyAuth returns middleware that validates API keys from the Authorization
// header before any handler runs. It gates every "/v1/" route, and every path
// at or under one of gatedRoots: the API surfaces served outside /v1, such as
// the native Google API (GenAIRoot, whose key it reads as that API's handler
// does: GenAIRequestAPIKey), the speech vendors' relayed APIs
// (SpeechRelayRoots, key read as SpeechRequestAPIKey), the MCP endpoint and
// /a2a; each of them answers only a request that carries a valid key.
// Every other route is passed through without authentication (health checks,
// admin routes, etc.).
//
// The decision is made on the canonical path (httppath.Canonical), not the raw
// one: "//v1/videos/x" or "/./v1/videos/x" must be gated exactly like
// "/v1/videos/x", and "//mcp" like "/mcp". The router rejects every
// non-canonical path, so the path a handler runs on always equals the path
// this gate decided on.
//
// On every gated path it also applies the IP rules the request pipeline
// applies, from the same client IP (ClientIP): the key's allowed_ips, and,
// through ipACL as server keyGateIPACL builds it, the gateway's ip_acl and
// the ip_acl of the key's org where this gateway enforces them.
// Many gated routes never run the pipeline, so this gate applies the same
// IP rules on every gated path. A
// request with no key to check (a license token, or auth off) still meets the
// gateway's ip_acl.
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

			rawKey := gatedAPIKey(r)
			if rawKey == "" {
				models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
				return
			}

			apiKey := keyStore.Authenticate(rawKey)
			if apiKey == nil {
				models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
				return
			}

			// Then the key's org's ip_acl and the key's allowed_ips.
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
// request, as it was at that moment. ok is false when KeyAuth authenticated
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

// GenAIRoot is the root of the native Google GenAI API (/v1beta/models/...).
// server.go names it among KeyAuth's gated roots, and LicenseAuth gates it as
// it gates /v1. Its key is read as the GenAI handler reads it
// (GenAIRequestAPIKey), so a request is admitted at the gate exactly when the
// pipeline's auth plugin would admit it.
const GenAIRoot = "/v1beta"

// isGenAIPath reports whether a request path is at or under GenAIRoot, judged
// on its canonical form like isV1Path.
func isGenAIPath(p string) bool {
	c := httppath.Canonical(p)
	return c == GenAIRoot || strings.HasPrefix(c, GenAIRoot+"/")
}

// GenAIRequestAPIKey is the raw API key of a native Google GenAI request, in
// the order the GenAI handler reads it: the key query parameter, then
// x-goog-api-key, then a Bearer Authorization header (the handler passes any
// other Authorization value to the auth plugin, which reads only Bearer).
// x-api-key is not read on this API.
func GenAIRequestAPIKey(r *http.Request) string {
	if key := r.URL.Query().Get("key"); key != "" {
		return key
	}
	if key := r.Header.Get("x-goog-api-key"); key != "" {
		return key
	}
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		return strings.TrimPrefix(authHeader, "Bearer ")
	}
	return ""
}

// SpeechRelayRoots are the roots of the speech vendors' relayed WebSocket
// APIs, one per vendor ("/deepgram", "/cartesia"; speech.Endpoints serves
// each vendor's API under its name). server.go names them among KeyAuth's
// gated roots. Their key is read as the relay handler reads it
// (SpeechRequestAPIKey), so a session is admitted at the gate exactly when the
// pipeline's auth plugin would admit it, and a caller without a key learns
// nothing about which vendors are configured.
func SpeechRelayRoots() []string {
	return append([]string(nil), speechRelayRoots...)
}

var speechRelayRoots = func() []string {
	var roots []string
	for _, ep := range speech.Endpoints() {
		root := "/" + ep.Vendor
		if len(roots) == 0 || roots[len(roots)-1] != root {
			roots = append(roots, root) // Endpoints is sorted by vendor
		}
	}
	return roots
}()

// isSpeechRelayPath reports whether a request path is at or under one of
// SpeechRelayRoots, judged on its canonical form like isV1Path.
func isSpeechRelayPath(p string) bool {
	c := httppath.Canonical(p)
	for _, root := range speechRelayRoots {
		if c == root || strings.HasPrefix(c, root+"/") {
			return true
		}
	}
	return false
}

// SpeechRequestAPIKey is the raw gateway key of a relayed speech session, read
// wherever the vendor's own SDK puts its key: Deepgram's "Authorization:
// Token", a Bearer token, Cartesia's X-API-Key header or its api_key query
// parameter. The relay handler reads the key with it too.
func SpeechRequestAPIKey(r *http.Request) string {
	if authHeader := r.Header.Get("Authorization"); authHeader != "" {
		for _, scheme := range []string{"Token ", "Bearer "} {
			if key, ok := strings.CutPrefix(authHeader, scheme); ok {
				return strings.TrimSpace(key)
			}
		}
	}
	if key := r.Header.Get("X-API-Key"); key != "" {
		return key
	}
	return r.URL.Query().Get("api_key")
}

// gatedAPIKey is the key KeyAuth authenticates on a gated path: on the native
// Google API and the speech relays the one their handlers read, everywhere
// else RequestAPIKey.
func gatedAPIKey(r *http.Request) string {
	if isGenAIPath(r.URL.Path) {
		return GenAIRequestAPIKey(r)
	}
	if isSpeechRelayPath(r.URL.Path) {
		return SpeechRequestAPIKey(r)
	}
	return RequestAPIKey(r)
}

// RequestAPIKey is the raw API key KeyAuth authenticates: a Bearer
// Authorization header, otherwise x-api-key. Handlers that need the caller's
// key use it so they read the same key KeyAuth checked. The native Google API
// reads its key differently (GenAIRequestAPIKey).
func RequestAPIKey(r *http.Request) string {
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		return strings.TrimPrefix(authHeader, "Bearer ")
	}
	if apiKey := r.Header.Get("x-api-key"); apiKey != "" {
		return apiKey
	}
	return ""
}
