package models

import (
	"net/url"
	"strings"
)

// redacted replaces a credential wherever one is found.
const redacted = "REDACTED"

// sensitiveQueryParams are query parameter names whose value is a credential.
var sensitiveQueryParams = map[string]bool{
	"key":              true,
	"api_key":          true,
	"apikey":           true,
	"access_token":     true,
	"token":            true,
	"password":         true,
	"secret":           true,
	"sig":              true,
	"signature":        true,
	"x-goog-api-key":   true,
	"subscription-key": true,
}

// trailingPunctuation ends a sentence rather than a URL.
const trailingPunctuation = `.,;:)]}>"'`

// RedactCredentials removes credentials from any URL in a message.
//
// An error from an HTTP call is a *url.Error carrying the whole URL it tried,
// and that text reaches three places a credential must never reach: the error
// body the caller reads, the request log the dashboard shows, and the
// operator's own logs. A provider that puts its key in the query string, or a
// base_url written with a password in it, therefore publishes it on the first
// network failure.
//
// Only URLs are touched. Redacting anything that merely looks like a secret
// would hide the upstream's own error text, which is what the message is for.
func RedactCredentials(message string) string {
	if !strings.Contains(message, "://") {
		return message
	}

	var out strings.Builder
	rest := message
	for {
		i := strings.Index(rest, "://")
		if i < 0 {
			out.WriteString(rest)
			return out.String()
		}
		// Walk back to the start of the scheme, and forward to the end of the URL.
		start := i
		for start > 0 && isSchemeByte(rest[start-1]) {
			start--
		}
		end := i + len("://")
		for end < len(rest) && !isURLTerminator(rest[end]) {
			end++
		}
		out.WriteString(rest[:start])
		out.WriteString(redactURL(rest[start:end]))
		rest = rest[end:]
	}
}

func isSchemeByte(b byte) bool {
	return b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' || b >= '0' && b <= '9' || b == '+' || b == '-' || b == '.'
}

func isURLTerminator(b byte) bool {
	return b == ' ' || b == '\t' || b == '\n' || b == '\r' || b == '"' || b == '\'' || b == '<' || b == '>' || b == '\\' || b == ','
}

// redactURL rewrites one URL with its credentials removed.
func redactURL(raw string) string {
	// Find the boundary first, then slice once. Growing a string a byte at a
	// time is quadratic, and the input is a caller's: a model name of
	// "https://host/" followed by a hundred thousand brackets reaches here
	// through an unknown-model error.
	end := len(raw)
	for end > 0 && strings.IndexByte(trailingPunctuation, raw[end-1]) >= 0 {
		end--
	}
	raw, trailing := raw[:end], raw[end:]

	u, err := url.Parse(raw)
	if err != nil {
		// Unreadable, so there is no telling which part is the credential.
		if i := strings.IndexByte(raw, '?'); i >= 0 {
			return raw[:i+1] + redacted + trailing
		}
		return raw + trailing
	}

	if u.User != nil {
		if _, set := u.User.Password(); set {
			u.User = url.UserPassword(u.User.Username(), redacted)
		}
	}

	if q := u.Query(); len(q) > 0 {
		changed := false
		for name, values := range q {
			if !sensitiveQueryParams[strings.ToLower(name)] {
				continue
			}
			for i := range values {
				values[i] = redacted
			}
			changed = true
		}
		if changed {
			u.RawQuery = q.Encode()
		}
	}

	return u.String() + trailing
}
