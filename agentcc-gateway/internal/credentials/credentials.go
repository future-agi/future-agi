// Package credentials names what carries a credential: the headers whose
// values the gateway never keeps in a request log or exports to a trace.
//
// Two rules decide it. headerNames is the named list: every header the
// gateway reads its key from, every header a provider or guardrail adapter
// sends a key in, and the common credential headers callers send.
// LooksLikeCredential covers the rest: a name that reads as a credential.
// Over-matching costs only a masked header, so the rule leans broad, but a
// name that only counts or describes tokens (prompt_tokens, Tokenizer,
// X-Token-Count, token_budget) is not a credential.
//
// Django's request-log ingestion keeps its own copy of both rules
// (futureagi/agentcc/services/credential_headers.py), and
// api_contracts/gateway/credential-headers.json is the case table both sides
// test against, so they cannot drift apart unnoticed.
package credentials

import "strings"

// headerNames are the credential headers by name, lower-cased: the one list
// the request log masks and the trace export never exports.
var headerNames = map[string]struct{}{
	// Read as the gateway key: Authorization (and its Bearer value),
	// x-api-key (copied into Authorization), x-goog-api-key on /v1beta.
	"authorization":  {},
	"x-api-key":      {},
	"x-goog-api-key": {},

	// HTTP authentication and sessions.
	"proxy-authorization": {},
	"www-authenticate":    {},
	"proxy-authenticate":  {},
	"cookie":              {},
	"set-cookie":          {},

	// Sent with a provider's or a guardrail service's key: Azure (api-key,
	// Ocp-Apim-Subscription-Key), Bedrock's session token, Vault, the
	// FutureAGI evaluator (X-Secret-Key), Aporia, Lasso, Gray Swan, Enkrypt
	// (apikey).
	"api-key":                   {},
	"ocp-apim-subscription-key": {},
	"x-amz-security-token":      {},
	"x-vault-token":             {},
	"x-secret-key":              {},
	"x-aporia-api-key":          {},
	"lasso-api-key":             {},
	"grayswan-api-key":          {},
	"apikey":                    {},

	// Further credential headers.
	"x-auth-token":     {},
	"x-webhook-secret": {},
}

// credentialWords are the parts of a name that make it read as a
// credential wherever they appear, in a lower-cased name. A word with a
// hyphen is matched with an underscore too, so the match never rewrites the
// name. Tokens, keys and "auth" are matched by their position instead
// (looksLikeCredential), so that a count of tokens or a cache key is not one.
var credentialWords = withUnderscores(
	"authorization", "authorisation", "authenticat", "bearer",
	"secret", "password", "passwd", "passphrase", "credential",
	"cookie", "signature",
	"api-key", "apikey", "private-key", "access-key", "auth-key",
	"subscription-key",
)

// credentialPrefixes are header families whose every member may carry a
// credential: Portkey's forwarding headers carry a Portkey key, a virtual
// key, provider keys and configs holding them (x-portkey-api-key,
// x-portkey-virtual-key, x-portkey-config, ...).
var credentialPrefixes = []string{"x-portkey-"}

// plainKeyNames end in "-key" and carry no credential: the gateway's cache
// key (a header and a request-metadata entry the cache plugin writes), and
// the idempotency key SDKs send.
var plainKeyNames = []string{
	"cache-key", "x-cache-key", "x-agentcc-cache-key",
	"idempotency-key", "x-idempotency-key",
}

func withUnderscores(words ...string) []string {
	out := append([]string(nil), words...)
	for _, w := range words {
		if strings.Contains(w, "-") {
			out = append(out, strings.ReplaceAll(w, "-", "_"))
		}
	}
	return out
}

// IsHeader reports whether the header named name carries a credential: it
// is on the named list, or its name reads as one. Case-insensitive; an
// underscore counts as a hyphen.
func IsHeader(name string) bool {
	lower := strings.ToLower(name)
	if _, ok := headerNames[lower]; ok {
		return true
	}
	return looksLikeCredential(lower)
}

// LooksLikeCredential reports whether name reads as the name of a
// credential. Case-insensitive; an underscore counts as a hyphen.
func LooksLikeCredential(name string) bool {
	return looksLikeCredential(strings.ToLower(name))
}

// looksLikeCredential is the name rule on a lower-cased name. Besides the
// credentialWords anywhere and the credentialPrefixes, it reads the name's
// last part (what follows its last hyphen, underscore or dot):
//
//   - a last part that ends in "token" names a token (token, x-vault-token,
//     session_token, X-CSRFToken); "tokens", "tokenizer" and a token word
//     followed by another part (X-Token-Count, token_budget) do not;
//   - a last part "key" after another part names a key (X-OpenAI-Key, x-key,
//     X-Functions-Key, X-License-Key), except plainKeyNames; a bare "key"
//     does not;
//   - a last part that ends in "auth" after another part names an
//     authenticator (X-Auth, Helicone-Auth); auth_type does not.
//
// It never allocates.
func looksLikeCredential(lower string) bool {
	for _, word := range credentialWords {
		if strings.Contains(lower, word) {
			return true
		}
	}
	for _, prefix := range credentialPrefixes {
		if hasPrefixSep(lower, prefix) {
			return true
		}
	}
	cut := strings.LastIndexAny(lower, "-_.")
	last := lower[cut+1:]
	if strings.HasSuffix(last, "token") {
		return true
	}
	if cut < 0 {
		return false
	}
	if last == "key" {
		for _, plain := range plainKeyNames {
			if sameName(lower, plain) {
				return false
			}
		}
		return true
	}
	return strings.HasSuffix(last, "auth")
}

// sameName reports whether a and b are the same name, an underscore counting
// as a hyphen.
func sameName(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := 0; i < len(a); i++ {
		if a[i] != b[i] && !(isSep(a[i]) && isSep(b[i])) {
			return false
		}
	}
	return true
}

// hasPrefixSep reports whether name starts with prefix, an underscore
// counting as a hyphen.
func hasPrefixSep(name, prefix string) bool {
	return len(name) >= len(prefix) && sameName(name[:len(prefix)], prefix)
}

func isSep(c byte) bool { return c == '-' || c == '_' }

// gatewayMetadataKeys are request-metadata keys the gateway writes whose
// names read as credentials and that carry none: the presented key's id,
// name and owner (the auth plugin), and the validation plugin's warning
// about the input's token count.
var gatewayMetadataKeys = map[string]struct{}{
	"auth_key_id":         {},
	"auth_key_name":       {},
	"auth_key_owner":      {},
	"input_token_warning": {},
}

// IsMetadataKey reports whether the request-metadata entry named key carries
// a credential: "authorization", the caller's Authorization header (or its
// x-api-key or x-goog-api-key as a bearer token), which the handlers keep
// for the auth plugin; and any other entry whose name reads as a
// credential, such as a caller's own dimension or a key's label, other than
// the gateway's own entries that only name one (gatewayMetadataKeys).
func IsMetadataKey(key string) bool {
	if _, ok := gatewayMetadataKeys[key]; ok {
		return false
	}
	return IsHeader(key)
}

// WithoutCredentials is a copy of the request metadata m without the
// entries IsMetadataKey names: what a guardrail check's input and an async
// job keep. m itself is not changed; nil stays nil.
func WithoutCredentials(m map[string]string) map[string]string {
	if m == nil {
		return nil
	}
	out := make(map[string]string, len(m))
	for k, v := range m {
		if !IsMetadataKey(k) {
			out[k] = v
		}
	}
	return out
}
