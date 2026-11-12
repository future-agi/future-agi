package privacy

import (
	"regexp"
	"strings"
)

// CredentialPattern is one shape of credential that an upstream may echo in
// an error message or a stdio server may print to stderr. It is the one list
// both the attempt error scrubber (routing/errclass.go) and the stdio stderr
// log (mcp/transport_stdio.go) use, so a new shape is added once.
type CredentialPattern struct {
	Name string
	Re   *regexp.Regexp
	// Keep is the ReplaceAllString template of the part of a match that
	// stays readable (a key=value pair's name and separator), placed before
	// the marker; "" replaces the whole match.
	Keep string
}

// credentialPatterns are provider API keys, bearer tokens, cloud access keys
// and key=value pairs, including URL query parameters. The key=value pattern
// keeps the field name, so a log still says which field was removed.
var credentialPatterns = []CredentialPattern{
	{Name: "bearer", Re: regexp.MustCompile(`(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+`)},
	{Name: "sk_key", Re: regexp.MustCompile(`\bsk-[A-Za-z0-9_-]{8,}`)},
	{Name: "google_key", Re: regexp.MustCompile(`\bAIza[0-9A-Za-z_-]{20,}`)},
	{Name: "aws_key", Re: regexp.MustCompile(`\b(?:AKIA|ASIA)[0-9A-Z]{16}\b`)},
	{
		Name: "secret",
		Re:   regexp.MustCompile(`(?i)\b((?:x-)?api[_-]?key|key|token|access[_-]?token|secret|password)(["']?\s*[:=]\s*["']?)[^\s"'&,;)}]+`),
		Keep: "${1}${2}",
	},
}

// CredentialPatterns returns the shared credential patterns, in the order
// RedactCredentials applies them.
func CredentialPatterns() []CredentialPattern {
	return append([]CredentialPattern(nil), credentialPatterns...)
}

// RedactCredentials replaces every credential in s with marker(name), name
// being the pattern's; a key=value pair keeps its name and separator.
func RedactCredentials(s string, marker func(name string) string) string {
	for _, p := range credentialPatterns {
		s = p.Re.ReplaceAllString(s, p.Keep+escapeTemplate(marker(p.Name)))
	}
	return s
}

// escapeTemplate makes a marker literal inside a ReplaceAllString template.
func escapeTemplate(s string) string { return strings.ReplaceAll(s, "$", "$$") }
