package ext

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

// bearerOf is the caller's API key, read as the mock reads it when it records
// a charge (Authorization: Bearer, else x-api-key), so a key's usage is the
// charges made with it.
func bearerOf(r *http.Request) string {
	if v, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer "); ok {
		return v
	}
	return r.Header.Get("x-api-key")
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// writeError answers in OpenRouter's error shape, {error: {code, message}}.
func writeError(w http.ResponseWriter, status int, format string, args ...any) {
	writeJSON(w, status, map[string]any{"error": map[string]any{"code": status, "message": fmt.Sprintf(format, args...)}})
}

// requireBearer is the caller's key, or "" after a 401 for a call without one.
func requireBearer(w http.ResponseWriter, r *http.Request) string {
	bearer := bearerOf(r)
	if bearer == "" {
		writeError(w, http.StatusUnauthorized, "No auth credentials found")
	}
	return bearer
}

func microsToUSD(micros int64) float64 { return float64(micros) / 1e6 }

func usdToMicros(usd float64) int64 { return int64(math.Round(usd * 1e6)) }

// invalidDirective refuses a directive whose argument the mock cannot use.
func invalidDirective(tok, why string) error {
	return &mock.DirectiveError{Code: "invalid_mock_directive", Message: fmt.Sprintf("%s directive %q: %s", mock.BehaviorHeader, tok, why)}
}
