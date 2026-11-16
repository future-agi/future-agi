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

			if !anyKeyAuthenticates(keyStore, extractRequestAPIKeys(r)) {
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

// anyKeyAuthenticates reports whether at least one candidate key is valid.
func anyKeyAuthenticates(keyStore *auth.KeyStore, candidates []string) bool {
	for _, key := range candidates {
		if keyStore.Authenticate(key) != nil {
			return true
		}
	}
	return false
}

// extractRequestAPIKeys returns every gateway key the request carries, in
// each form a supported client sends it: an Authorization Bearer or Token
// scheme (matched case-insensitively, like the MCP handler), x-api-key,
// Gemini's x-goog-api-key and ?key=, and the Cartesia SDK's ?api_key=.
// KeyAuth lets a request through when any of them authenticates, because
// handlers prefer different forms (GenAIHandler reads ?key= and
// x-goog-api-key before Authorization). Each handler still authenticates
// with its own extraction, so passing here never grants access by itself.
func extractRequestAPIKeys(r *http.Request) []string {
	var keys []string
	add := func(key string) {
		if key != "" {
			keys = append(keys, key)
		}
	}
	if authHeader := r.Header.Get("Authorization"); authHeader != "" {
		for _, scheme := range []string{"Bearer ", "Token "} {
			if len(authHeader) > len(scheme) && strings.EqualFold(authHeader[:len(scheme)], scheme) {
				add(strings.TrimSpace(authHeader[len(scheme):]))
				break
			}
		}
	}
	add(r.Header.Get("x-api-key"))
	add(r.Header.Get("x-goog-api-key"))
	query := r.URL.Query()
	add(query.Get("key"))
	add(query.Get("api_key"))
	return keys
}
