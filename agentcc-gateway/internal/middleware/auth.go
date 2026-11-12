package middleware

import (
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// KeyAuth returns middleware that validates API keys from the Authorization
// header before any handler runs. Only explicitly public health/discovery
// routes and the separately authenticated admin namespace bypass API-key auth.
//
// If keyStore is nil, auth is considered disabled and all requests pass through.
func KeyAuth(keyStore *auth.KeyStore, enabled ...bool) func(http.Handler) http.Handler {
	authEnabled := keyStore != nil
	if len(enabled) > 0 {
		authEnabled = enabled[0]
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if isPublicOrSeparatelyAuthenticatedPath(r.URL.Path) {
				next.ServeHTTP(w, r)
				return
			}

			if IsLicenseAuthorized(r.Context()) {
				next.ServeHTTP(w, r)
				return
			}

			// If auth is disabled, pass through.
			if !authEnabled || keyStore == nil {
				next.ServeHTTP(w, r)
				return
			}

			rawKey := extractRequestAPIKey(r)
			if rawKey == "" {
				models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
				return
			}

			if keyStore.Authenticate(rawKey) == nil {
				models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}

func isPublicOrSeparatelyAuthenticatedPath(path string) bool {
	switch path {
	case "/healthz", "/readyz", "/livez", "/.well-known/agent.json":
		return true
	default:
		return strings.HasPrefix(path, "/-/")
	}
}

// extractRequestAPIKey reads the gateway key from every place a supported
// client sends it. Handlers re-authenticate with their own extraction, so a
// form accepted here never grants access on a route that does not take it.
// The "Token " scheme and api_key query parameter are the Deepgram and
// Cartesia SDK forms the speech relays accept.
func extractRequestAPIKey(r *http.Request) string {
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		return strings.TrimPrefix(authHeader, "Bearer ")
	} else if key, ok := strings.CutPrefix(authHeader, "Token "); ok {
		return strings.TrimSpace(key)
	}
	if apiKey := r.Header.Get("x-api-key"); apiKey != "" {
		return apiKey
	}
	if apiKey := r.Header.Get("x-goog-api-key"); apiKey != "" {
		return apiKey
	}
	query := r.URL.Query()
	if apiKey := query.Get("key"); apiKey != "" {
		return apiKey
	}
	if apiKey := query.Get("api_key"); apiKey != "" {
		return apiKey
	}
	return ""
}
