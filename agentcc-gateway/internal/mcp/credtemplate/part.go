package credtemplate

import (
	"regexp"
	"strings"
	"unicode/utf8"
)

// In is where a credential part goes (connector-manifest/v1 credentialIn).
type In string

const (
	// InHeader is the request header named by the part's name.
	InHeader In = "header"
	// InQuery is the query parameter named by the part's name.
	InQuery In = "query"
	// InURLVariable is the {name} variable in the path or query of the
	// server's URL.
	InURLVariable In = "url_variable"
)

// Limits on a credential.
const (
	// MaxParts is the most parts a credential has (connector-manifest/v1
	// auth.credentialParts; MCPCredentialSecret carries its own part plus
	// at most 7 more).
	MaxParts = 8
	// MaxValueChars bounds one part's value, as Django bounds a stored
	// secret (MAX_SECRET_CHARS).
	MaxValueChars = 8192
)

// Part is one part of a credential and where it goes.
type Part struct {
	In    In
	Name  string
	Value string
	// Prefix goes before Value in a header, such as "Bearer "; header
	// parts only.
	Prefix string
	// Constant marks a non-secret part the catalog declares with its value
	// (AM-C20: Close's Close-Scope). It is placed like any other part, but
	// it is not a secret, so Scrub leaves it.
	Constant bool
}

// The name patterns: an HTTP token of at most 64 characters (the
// MCPCredentialSecret contract and connector-manifest/v1 headerName), and
// connector-manifest/v1's queryName and varName. TestPatternsMatchTheContracts
// keeps them equal to the schemas'.
var (
	headerNamePattern = regexp.MustCompile("^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")
	queryNamePattern  = regexp.MustCompile(`^[A-Za-z0-9._~-]{1,64}$`)
	varNamePattern    = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,31}$`)
	prefixPattern     = regexp.MustCompile(`^[!-~]{1,31} ?$`)
)

// reservedHeaders are headers the gateway's HTTP or MCP transport sets, or
// that frame the message, so a credential part never names one (compared
// case-insensitively). It is the union of the probe's and Django's lists
// (mcp/probe.go, services/mcp_credentials.py) and connector-manifest/v1's
// transport headers, plus the headers Go's transport and the gateway's
// tracing set themselves.
var reservedHeaders = map[string]bool{
	"accept": true, "accept-encoding": true, "connection": true, "content-encoding": true,
	"content-length": true, "content-type": true, "expect": true, "host": true,
	"keep-alive": true, "last-event-id": true, "proxy-authorization": true,
	"proxy-connection": true, "te": true, "trailer": true, "transfer-encoding": true,
	"upgrade": true, "traceparent": true, "tracestate": true,
}

// reservedHeaderPrefixes are header families the gateway owns: every MCP
// header (Mcp-Session-Id, MCP-Protocol-Version, Mcp-Method, Mcp-Name, the
// mirrored Mcp-Param-* and any later one) and the gateway's own namespaces.
var reservedHeaderPrefixes = []string{"mcp-", "x-agentcc-", "x-omega-"}

// ReservedHeader reports whether a credential part may never use the
// header name, because the gateway's transport sets it or owns its family.
func ReservedHeader(name string) bool {
	n := strings.ToLower(name)
	if reservedHeaders[n] {
		return true
	}
	for _, p := range reservedHeaderPrefixes {
		if strings.HasPrefix(n, p) {
			return true
		}
	}
	return false
}

// checkPart checks one part's own fields and returns the field at fault
// and why, or "" when the part is well formed. The reason never holds the
// value.
func checkPart(p Part) (field, reason string) {
	switch p.In {
	case InHeader:
		if !headerNamePattern.MatchString(p.Name) {
			return "name", "is not an HTTP header name of at most 64 characters"
		}
		if ReservedHeader(p.Name) {
			return "name", "is a header the gateway sets itself, so a credential never uses it"
		}
		if p.Prefix != "" && !prefixPattern.MatchString(p.Prefix) {
			return "prefix", "is not 1 to 31 printable characters, then at most one space"
		}
	case InQuery:
		if !queryNamePattern.MatchString(p.Name) {
			return "name", "is not a query parameter name (^[A-Za-z0-9._~-]{1,64}$)"
		}
	case InURLVariable:
		if !varNamePattern.MatchString(p.Name) {
			return "name", "is not a URL variable name (^[A-Za-z_][A-Za-z0-9_]{0,31}$)"
		}
	default:
		return "in", "is not header, query or url_variable"
	}
	if p.Prefix != "" && p.In != InHeader {
		return "prefix", "only a header part has a prefix"
	}
	if reason := checkValue(p.Value); reason != "" {
		return "value", reason
	}
	return "", ""
}

// checkValue applies Django's rules for a stored secret to every part's
// value: present, at most MaxValueChars characters, no control character
// and no surrounding space.
func checkValue(v string) string {
	switch {
	case v == "":
		return "is empty"
	case !utf8.ValidString(v):
		return "is not UTF-8"
	case utf8.RuneCountInString(v) > MaxValueChars:
		return "is longer than 8192 characters"
	case strings.TrimSpace(v) != v:
		return "has surrounding spaces"
	}
	for _, r := range v {
		if r < 0x20 || r == 0x7f {
			return "has a control character"
		}
	}
	return ""
}

// key identifies a part for duplicate checks: header names compare
// case-insensitively, query and variable names exactly.
func (p Part) key() string {
	if p.In == InHeader {
		return string(p.In) + " " + strings.ToLower(p.Name)
	}
	return string(p.In) + " " + p.Name
}
