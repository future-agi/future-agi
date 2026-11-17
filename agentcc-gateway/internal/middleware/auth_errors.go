package middleware

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/anthropicfmt"
	"github.com/futureagi/agentcc-gateway/internal/httppath"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Why a key authenticated nothing (metadata.reason of the 401
// invalid_api_key). Each is told from the key's format alone: whether a key
// of the gateway's own format exists is never revealed, so an unknown, a
// revoked, an expired and a deleted gateway key all get the same answer.
const (
	ReasonMissingAPIKey   = "missing_api_key"
	ReasonFutureAGIAPIKey = "future_agi_api_key"
	ReasonNotAGatewayKey  = "not_a_gateway_key"
	ReasonUnknownAPIKey   = "unknown_api_key"
)

// gatewayKeyPrefix starts every API key the gateway creates
// (auth.generateKey).
const gatewayKeyPrefix = "sk-agentcc-"

// Messages of the 401 invalid_api_key, by reason.
const (
	msgMissingAPIKey = `No API key was sent. Send your gateway API key as "Authorization: Bearer sk-agentcc-..." ` +
		`(or in the x-api-key header); create one on the Gateway API Keys page.`
	// The native Google API (GenAIRoot) reads its key as GenAIRequestAPIKey
	// does, never from x-api-key.
	msgMissingGenAIKey = `No API key was sent. Send your gateway API key as "Authorization: Bearer sk-agentcc-...", ` +
		`in the x-goog-api-key header or in the key query parameter; create one on the Gateway API Keys page.`
	msgFutureAGIAPIKey = "This looks like a Future AGI API key (from the Build Keys page), which the gateway does not accept. " +
		"Use a gateway API key instead: gateway keys start with sk-agentcc- and are created on the Gateway API Keys page."
	msgNotAGatewayKey = "This API key is not recognized, and it is not a gateway key: gateway keys start with sk-agentcc- " +
		"and are created on the Gateway API Keys page. A key a model vendor issued is added on the Gateway Providers " +
		"page, not sent to the gateway."
	msgUnknownAPIKey = "This gateway API key is not recognized: it may be mistyped, revoked or deleted. " +
		"Check it on the Gateway API Keys page, or create a new one there."
)

// ErrUnauthorizedKey is the 401 invalid_api_key for a request whose key,
// rawKey ("" when none was sent), authenticated nothing: no key, a Future AGI
// API key sent as a gateway key, a key of another format, and a gateway key
// the gateway does not accept get different messages, with the reason in
// metadata.reason.
func ErrUnauthorizedKey(rawKey string) *models.APIError {
	reason, msg := ReasonUnknownAPIKey, msgUnknownAPIKey
	switch key := strings.TrimSpace(rawKey); {
	case key == "":
		reason, msg = ReasonMissingAPIKey, msgMissingAPIKey
	case strings.HasPrefix(key, gatewayKeyPrefix):
		// A gateway key the gateway does not accept: unknown or revoked alike.
	case looksLikeFutureAGIKey(key):
		reason, msg = ReasonFutureAGIAPIKey, msgFutureAGIAPIKey
	default:
		reason, msg = ReasonNotAGatewayKey, msgNotAGatewayKey
	}
	apiErr := models.ErrUnauthorized(msg)
	apiErr.Metadata = map[string]string{"reason": reason}
	return apiErr
}

// ErrUnauthorizedKeyFor is ErrUnauthorizedKey for a request to path p. On
// the native Google API (GenAIRoot) a request without a key is told the key
// forms that API reads (GenAIRequestAPIKey: the key query parameter,
// x-goog-api-key or a Bearer Authorization header), not x-api-key, which it
// ignores. Every other answer is ErrUnauthorizedKey's.
func ErrUnauthorizedKeyFor(p, rawKey string) *models.APIError {
	apiErr := ErrUnauthorizedKey(rawKey)
	if strings.TrimSpace(rawKey) == "" && isGenAIPath(p) {
		apiErr.Message = msgMissingGenAIKey
	}
	return apiErr
}

// looksLikeFutureAGIKey reports whether key has the format of a Future AGI
// API key or secret key: 32 lowercase hex digits (a UUID4's hex).
func looksLikeFutureAGIKey(key string) bool {
	if len(key) != 32 {
		return false
	}
	for i := 0; i < len(key); i++ {
		c := key[i]
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return false
		}
	}
	return true
}

// isMessagesPath reports whether a request path is the Anthropic Messages
// API (/v1/messages and its count_tokens), judged on its canonical form.
func isMessagesPath(p string) bool {
	c := httppath.Canonical(p)
	return c == "/v1/messages" || strings.HasPrefix(c, "/v1/messages/")
}

// writeGateError writes a refusal of the key gate: in the Anthropic error
// shape on the Messages API, whose clients read only that shape, else the
// OpenAI shape. A 401 carries the gateway's Bearer challenge either way.
func writeGateError(w http.ResponseWriter, r *http.Request, apiErr *models.APIError) {
	if !isMessagesPath(r.URL.Path) {
		models.WriteError(w, apiErr)
		return
	}
	if apiErr.RetryAfterSec > 0 {
		w.Header().Set("Retry-After", strconv.Itoa(apiErr.RetryAfterSec))
	}
	if apiErr.Status == http.StatusUnauthorized && w.Header().Get("WWW-Authenticate") == "" {
		w.Header().Set("WWW-Authenticate", models.BearerChallenge)
	}
	anthropicfmt.WriteAPIError(w, apiErr)
}
