package telemetry

import (
	"encoding/base64"
	"encoding/json"
	"regexp"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

const maxTextBytes = 1 << 20
const maxTextTokens = 256

// The earliest representation wins: an object is decoded before its keys, a
// credential assignment before its value, and a URL before its query. Sanitized
// structured fragments are not fed back into regexes that could corrupt them.
var contentToken = regexp.MustCompile(`(?i)(?:[a-z][a-z0-9+.-]*:)?//[^\s<>"']+|["']?([a-z][a-z0-9_. /-]*?)["']?[ \t]*[:=][ \t]*|[\[{\"]`)
var jwtShape = regexp.MustCompile(`[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*`)

func scrubText(text string, depth int) string {
	if depth >= maxContentDepth || len(text) > maxTextBytes {
		return redactedContent
	}
	// Decode an entire textual envelope before tokenization can split padding
	// or delimiters. URLs are handled component-by-component to preserve URI
	// semantics (notably escaped query separators and IPv6 authorities).
	if !strings.Contains(text, "://") && !strings.HasPrefix(text, "//") {
		decoded, ok := canonicalText(text)
		if !ok {
			return redactedContent
		}
		if decoded != text {
			if scrubText(decoded, depth+1) != decoded {
				return redactedContent
			}
			return text
		}
	}
	var out strings.Builder
	pos := 0
	for n := 0; pos < len(text); n++ {
		if n >= maxTextTokens {
			return redactedContent
		}
		match := contentToken.FindStringSubmatchIndex(text[pos:])
		if match == nil {
			out.WriteString(scrubAtoms(text[pos:], depth))
			break
		}
		start, end := pos+match[0], pos+match[1]
		out.WriteString(scrubAtoms(text[pos:start], depth))
		token := text[start:end]
		if match[2] >= 0 {
			name := text[pos+match[2] : pos+match[3]]
			out.WriteString(scrubAtoms(token, depth))
			if isCredentialField(name) {
				end = credentialValueEnd(text, end, name, token)
				out.WriteString(redactedContent)
			}
		} else if len(token) > 1 {
			// Parse the complete URL: trimming punctuation can corrupt safe IPv6
			// authorities and query values. Parsing never dials the destination.
			out.WriteString(scrubURL(token, depth+1))
		} else {
			dec := json.NewDecoder(strings.NewReader(text[start:]))
			dec.UseNumber()
			var value any
			if dec.Decode(&value) == nil {
				clean, err := json.Marshal(scrubValue(value, nil, depth+1))
				if err != nil {
					return redactedContent
				}
				out.Write(clean)
				end = start + int(dec.InputOffset())
			} else {
				out.WriteString(token)
			}
		}
		pos = end
	}
	return out.String()
}

func credentialValueEnd(text string, start int, name, token string) int {
	// A credential header is one logical value, including folded lines and a
	// value that starts on the next line. Only a new, non-folded header ends it.
	if strings.Contains(token, ":") {
		for i := start; i < len(text); i++ {
			if text[i] != '\r' && text[i] != '\n' {
				continue
			}
			end := i
			if text[i] == '\r' && i+1 < len(text) && text[i+1] == '\n' {
				i++
			}
			next := i + 1
			if next < len(text) && headerLineStart.MatchString(text[next:]) {
				return end
			}
		}
		return len(text)
	}
	if start < len(text) {
		switch text[start] {
		case '"', '\'':
			quote := text[start]
			for i := start + 1; i < len(text); i++ {
				if text[i] == '\\' {
					i++
				} else if text[i] == quote {
					return i + 1
				}
			}
			return len(text)
		case '{', '[':
			dec := json.NewDecoder(strings.NewReader(text[start:]))
			if dec.Decode(new(any)) == nil {
				return start + int(dec.InputOffset())
			}
			return len(text)
		}
	}
	if n := strings.IndexAny(text[start:], "\r\n&,;)}]"); n >= 0 {
		return start + n
	}
	return len(text)
}

var headerLineStart = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_.-]*[ \t]*:`)

func scrubAtoms(text string, depth int) string {
	text = scrubEncodedAtoms(text, depth)
	text = jwtShape.ReplaceAllStringFunc(text, func(token string) string {
		header, _, _ := strings.Cut(token, ".")
		raw, err := base64.RawURLEncoding.DecodeString(header)
		// A JSON-object header distinguishes JWT-shaped values from ordinary
		// dotted names/version strings. No signature verification or I/O occurs.
		if err == nil && strings.HasPrefix(strings.TrimSpace(string(raw)), "{") && json.Valid(raw) {
			return redactedContent
		}
		return token
	})
	return privacy.RedactCredentials(text, func(string) string { return redactedContent })
}
