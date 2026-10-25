// Package credentials names what carries a credential: the headers whose
// values the gateway never keeps in a request log or exports to a trace, and
// the request-metadata keys whose values it never sends out of the process
// to anyone but the party the credential is for.
//
// Two rules decide it. headerNames is the named list: every header the
// gateway reads its key from, every header a provider or guardrail adapter
// sends a key in, and the common credential headers callers send.
// LooksLikeCredential covers the rest: a name that reads as a credential
// (a token, a secret, a password, an API key, a cookie, a vendor's key or
// auth header). The match is deliberately broad. Over-matching costs a
// masked header or a metadata entry left out; under-matching costs a key in
// clear. "token" is read in every word of a name
// (x-access-token, X-Access-Token-V2, slack_token_prod); only a count of
// tokens (prompt_tokens, X-Token-Count, token_budget) and a tokenizer are
// left out. "key" and "auth" are read by the name's last words, so a lookup
// key (Idempotency-Key, cache_key) and auth_type are not taken for a
// credential.
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

	// Session and webhook credential headers.
	"x-auth-token":     {},
	"x-webhook-secret": {},
}

// credentialWords are the parts of a name that make it read as a
// credential, in a lower-cased name. A word with a hyphen is matched with an
// underscore too, so the match never rewrites the name (a metadata key is
// checked on every guardrail input). "token" is not here: namesAToken reads
// it, leaving out a count of tokens and a tokenizer. "key" and "auth" are not
// here either: lastWordsNameACredential reads them, leaving out a lookup key
// and auth_type.
var credentialWords = withUnderscores(
	"authorization", "authorisation", "authenticat", "bearer",
	"secret", "password", "passwd", "passphrase", "credential",
	"cookie", "signature",
	"api-key", "apikey", "private-key", "access-key", "auth-key",
	"subscription-key",
)

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
// is on the named list, or its name reads as one. Case-insensitive.
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

func looksLikeCredential(lower string) bool {
	for _, word := range credentialWords {
		if strings.Contains(lower, word) {
			return true
		}
	}
	// Portkey's headers carry its key, a virtual key or a config that can
	// hold a provider's key inline.
	if isPortkeyHeader(lower) {
		return true
	}
	return namesAToken(lower) || lastWordsNameACredential(lower)
}

// tokenCountWords are the words that, right after "token", make a name a
// count of tokens rather than a token: X-Token-Count, token_budget,
// X-Token-Limit, token_usage.
var tokenCountWords = map[string]struct{}{
	"count":     {},
	"budget":    {},
	"limit":     {},
	"usage":     {},
	"used":      {},
	"remaining": {},
	"total":     {},
	"max":       {},
	"min":       {},
}

// namesAToken reports whether a lower-cased name names a token: "token"
// anywhere in it, in any word (x-access-token, X-Access-Token-V2,
// slack_token_prod, X-CSRFToken, X-TokenValue).
// It leaves out only
//   - a count of tokens: "token" followed by "s" (prompt_tokens,
//     x-ratelimit-remaining-tokens), or followed by a separator and a word in
//     tokenCountWords (X-Token-Count, token_budget, token-limit-remaining);
//   - a tokenizer: "token" followed by "iz" (Tokenizer, tokenize,
//     detokenization).
//
// It slices the name and never allocates.
func namesAToken(n string) bool {
	const word = "token"
	for start := 0; ; {
		i := strings.Index(n[start:], word)
		if i < 0 {
			return false
		}
		start += i + len(word)
		rest := n[start:]
		switch {
		case strings.HasPrefix(rest, "s"), strings.HasPrefix(rest, "iz"):
			continue
		case rest != "" && isSeparator(rest[0]):
			next := rest[1:]
			if j := strings.IndexAny(next, "-_"); j >= 0 {
				next = next[:j]
			}
			if _, count := tokenCountWords[next]; count {
				continue
			}
		}
		return true
	}
}

// isPortkeyHeader reports whether a lower-cased name starts with x-portkey-
// (either separator, as Django reads it with every "_" made a "-").
func isPortkeyHeader(n string) bool {
	const word = "portkey"
	return len(n) > len("x-portkey-") && n[0] == 'x' && isSeparator(n[1]) &&
		n[2:2+len(word)] == word && isSeparator(n[2+len(word)])
}

func isSeparator(c byte) bool { return c == '-' || c == '_' }

// plainKeyQualifiers are the words before a final "key" that name a lookup
// key rather than a credential: x-agentcc-cache-key, Idempotency-Key.
var plainKeyQualifiers = map[string]struct{}{
	"cache":         {},
	"idempotency":   {},
	"dedup":         {},
	"dedupe":        {},
	"deduplication": {},
	"partition":     {},
	"sort":          {},
	"routing":       {},
	"lookup":        {},
	"primary":       {},
	"foreign":       {},
	"public":        {},
}

// lastWordsNameACredential reads a lower-cased name by its last two words,
// split at "-" and "_" ("token" is namesAToken's):
//   - a last word "auth" (X-Auth, Helicone-Auth), but not auth_type;
//   - a last word "key" after another word (X-OpenAI-Key, x-key,
//     X-Functions-Key), unless that word names a lookup key
//     (plainKeyQualifiers). A bare "key" is a caller's dimension.
//
// It slices the name and never allocates.
func lastWordsNameACredential(n string) bool {
	last, prev, hasPrev := n, "", false
	if i := strings.LastIndexAny(n, "-_"); i >= 0 {
		last, hasPrev = n[i+1:], true
		rest := n[:i]
		prev = rest[strings.LastIndexAny(rest, "-_")+1:]
	}
	switch {
	case last == "auth":
		return true
	case last == "key" && hasPrev:
		_, plain := plainKeyQualifiers[prev]
		return !plain
	}
	return false
}

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
