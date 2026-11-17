package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"
)

// Ruling 733: Django answers a retired key presented with its own secret with
// a typed refusal, APIKeyRetired (futureagi/accounts/api_key_policy.py:16-19,
// 104-110). OTLP/HTTP gives the same answer. This changes only how a refusal
// reads: the key has already failed authentication, and nothing is cached.

// retiredKeyTypes is RETIRED_KEY_TYPES (futureagi/accounts/api_key_constants.py:4).
var retiredKeyTypes = map[string]bool{"system": true, "mcp": true, "ingest": true}

// maxCredentialLength is MAX_SECRET_LENGTH (futureagi/accounts/api_secrets.py:38):
// Django refuses a longer key or secret generically, before any lookup.
const maxCredentialLength = 255

// retirementLookup reads the row an api key names whatever its other state
// (enabled, deleted, owner, membership), as authenticate_api_key does before
// admission. Every value is empty when no row names the key.
type retirementLookup interface {
	LookupRetirement(ctx context.Context, apiKey string) (keyType, retiredAt, retiredReason, secretKeyHash string, err error)
}

var _ retirementLookup = (*PGResolver)(nil)

// isRetiredKey is is_retired_key (futureagi/accounts/api_key_policy.py:22-27).
func isRetiredKey(keyType, retiredAt, retiredReason string) bool {
	return retiredKeyTypes[keyType] || retiredAt != "" || retiredReason != ""
}

// retiredPair reports whether a refused personal pair names a retired key whose
// stored hash the presented secret proves. As in Django, retirement is never
// revealed without that proof, nor from a legacy plaintext column. A failed
// lookup keeps the generic refusal.
func (a *Authenticator) retiredPair(ctx context.Context, apiKey, secretKey string) bool {
	if a == nil || a.cfg.Peppers == nil || (a.keys == nil && a.pg == nil) ||
		strings.HasPrefix(apiKey, "fit_") ||
		len(apiKey) > maxCredentialLength || len(secretKey) > maxCredentialLength {
		return false
	}
	lookup, ok := a.keyResolver().(retirementLookup)
	if !ok {
		return false
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	keyType, retiredAt, retiredReason, storedHash, err := lookup.LookupRetirement(ctx, apiKey)
	if err != nil {
		a.log.Warn("retired key lookup failed", "err", err)
		return false
	}
	// One HMAC on every path, retired or not, as verify_secret does.
	proven := a.cfg.Peppers.VerifySecret(storedHash, "", secretKey)
	return proven && storedHash != "" && isRetiredKey(keyType, retiredAt, retiredReason)
}

// apiKeyRetiredEnvelope is custom_exception_handler's body for APIKeyRetired
// (futureagi/accounts/authentication.py:1105-1174), in its field order:
// build_error_envelope for a 401 AuthenticationFailed whose data is
// {"detail": ...} (futureagi/tfc/utils/api_errors.py:195-244), then create_url.
type apiKeyRetiredEnvelope struct {
	Status    bool                `json:"status"`
	Type      string              `json:"type"`
	Code      string              `json:"code"`
	Detail    string              `json:"detail"`
	Message   string              `json:"message"`
	Error     string              `json:"error"`
	Result    string              `json:"result"`
	Details   map[string][]string `json:"details"`
	CreateURL string              `json:"create_url"`
}

// apiKeyRetiredBody is that envelope as DRF's compact JSONRenderer writes it.
var apiKeyRetiredBody = func() []byte {
	const detail = "Organization keys were retired. Create a personal key."
	body, err := json.Marshal(apiKeyRetiredEnvelope{
		Type: "authentication_error", Code: "api_key_retired",
		Detail: detail, Message: detail, Error: detail, Result: detail,
		Details:   map[string][]string{"detail": {detail}},
		CreateURL: "/dashboard/keys", // PERSONAL_KEY_CREATE_URL, api_key_constants.py:8
	})
	if err != nil {
		panic(err)
	}
	return body
}()

// writeAPIKeyRetired answers as Django does: 401 with the ApiKey challenge of
// its default authenticator (authentication.py:202-203). The body is constant
// and carries no key material or tenant identity.
func writeAPIKeyRetired(w http.ResponseWriter) {
	h := w.Header()
	h.Set("Content-Type", "application/json")
	h.Set("X-Content-Type-Options", "nosniff")
	h.Set("WWW-Authenticate", "ApiKey")
	w.WriteHeader(http.StatusUnauthorized)
	_, _ = w.Write(apiKeyRetiredBody)
}
