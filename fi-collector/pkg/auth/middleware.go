package auth

import (
	"context"
	"encoding/base64"
	"errors"
	"net/http"
	"strings"
)

// HTTPMiddleware wraps an http.Handler with API key authentication.
// Accepts credentials via X-Api-Key/X-Secret-Key headers or
// Authorization: Basic base64(api_key:secret_key) (Langfuse SDK compat).
func (a *Authenticator) HTTPMiddleware(next http.Handler) http.Handler {
	if a == nil {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		apiKey, secretKey := extractCredentials(r)
		if apiKey == "" || secretKey == "" {
			http.Error(w, `{"error":"missing credentials: provide X-Api-Key/X-Secret-Key or Authorization: Basic"}`, http.StatusUnauthorized)
			return
		}

		result, err := a.Authenticate(r.Context(), apiKey, secretKey)
		if err != nil {
			if errors.Is(err, ErrUnauthenticated) {
				a.log.Warn("http auth failed", "err", err)
				http.Error(w, `{"error":"authentication failed"}`, http.StatusUnauthorized)
				return
			}
			a.log.Error("http auth infrastructure error", "err", err)
			http.Error(w, `{"error":"service temporarily unavailable"}`, http.StatusServiceUnavailable)
			return
		}

		ctx := context.WithValue(r.Context(), contextKey{}, result)
		ctx = context.WithValue(ctx, cacheKeyCtxKey{}, CacheKey(apiKey, secretKey))
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// extractCredentials reads api_key + secret_key from either:
//   - X-Api-Key / X-Secret-Key headers (native)
//   - Authorization: Basic base64(api_key:secret_key) (Langfuse SDK compat)
func extractCredentials(r *http.Request) (apiKey, secretKey string) {
	apiKey = r.Header.Get("X-Api-Key")
	secretKey = r.Header.Get("X-Secret-Key")
	if apiKey != "" && secretKey != "" {
		return
	}

	// Fallback: Basic auth (RFC 7235 — scheme is case-insensitive)
	authHeader := r.Header.Get("Authorization")
	if len(authHeader) < 6 || !strings.EqualFold(authHeader[:6], "basic ") {
		return "", ""
	}
	decoded, err := base64.StdEncoding.DecodeString(authHeader[6:])
	if err != nil {
		return "", ""
	}
	parts := strings.SplitN(string(decoded), ":", 2)
	if len(parts) != 2 {
		return "", ""
	}
	return parts[0], parts[1]
}
