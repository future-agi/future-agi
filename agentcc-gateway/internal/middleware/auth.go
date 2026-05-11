package middleware

import (
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// KeyAuth returns middleware that validates API keys from the Authorization
// header before any handler runs. Routes that do not start with "/v1/" are
// passed through without authentication (health checks, admin routes, etc.).
//
// If keyStore is nil, auth is considered disabled and all requests pass through.
func KeyAuth(keyStore *auth.KeyStore) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Skip auth for non-API routes (health, admin, etc.).
			if !strings.HasPrefix(r.URL.Path, "/v1/") {
				next.ServeHTTP(w, r)
				return
			}

			// If auth is disabled, pass through.
			if keyStore == nil {
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

func extractRequestAPIKey(r *http.Request) string {
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		return strings.TrimPrefix(authHeader, "Bearer ")
	}
	if apiKey := r.Header.Get("x-api-key"); apiKey != "" {
		return apiKey
	}
	return ""
}
