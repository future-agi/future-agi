package cassette

import (
	"regexp"
	"sort"
)

// Finding is one credential-shaped match: the rule and the byte offset. It
// never carries the matched value.
type Finding struct {
	Rule   string
	Offset int
}

// shapeRule is one credential shape. RE2 has no lookaround, so a rule that
// secrets-lint.sh writes with a lookbehind or lookahead checks the neighbouring bytes in
// accept, and a rejected candidate restarts the search one byte later, as
// Python's re.search does.
type shapeRule struct {
	name    string
	re      *regexp.Regexp
	accept  func(s string, start, end int) bool
	extraP6 bool // a rule beyond secrets-lint.sh's list
}

// shapeRules are the secrets-lint.sh rules (scripts/release/secrets-lint.sh),
// ported to RE2, plus two shapes a recorded vendor exchange is likely to carry:
// Stripe secret keys (Stripe is a first-wave connector) and JWTs (the usual OAuth
// token form). A cassette is refused when any of them survives scrubbing.
var shapeRules = []shapeRule{
	{name: "anthropic-key", re: regexp.MustCompile(`sk-ant-[A-Za-z0-9_-]{20,}`)},
	// (?<![A-Za-z0-9])sk-(?!ant-)[A-Za-z0-9_-]{20,}
	{name: "openai-style-key", re: regexp.MustCompile(`sk-[A-Za-z0-9_-]{20,}`), accept: func(s string, start, _ int) bool {
		return !(start > 0 && isAlnum(s[start-1])) && !hasPrefixAt(s, start+3, "ant-")
	}},
	{name: "google-api-key", re: regexp.MustCompile(`AIza[0-9A-Za-z_-]{35}`)},
	{name: "slack-token", re: regexp.MustCompile(`xox[bp]-[0-9A-Za-z-]{10,}`)},
	{name: "github-pat", re: regexp.MustCompile(`ghp_[0-9A-Za-z]{36}`)},
	{name: "github-fine-grained-pat", re: regexp.MustCompile(`github_pat_[0-9A-Za-z_]{22,}`)},
	// (?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])
	{name: "aws-access-key-id", re: regexp.MustCompile(`(?:AKIA|ASIA)[0-9A-Z]{16}`), accept: func(s string, start, end int) bool {
		return !(start > 0 && isUpperOrDigit(s[start-1])) && !(end < len(s) && isUpperOrDigit(s[end]))
	}},
	{name: "private-key-pem", re: regexp.MustCompile(`-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----`)},
	{name: "stripe-secret-key", re: regexp.MustCompile(`[sr]k_(?:live|test)_[0-9A-Za-z]{16,}`), extraP6: true},
	{name: "jwt", re: regexp.MustCompile(`eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}`), extraP6: true},
}

// p6Rules names the rules that are secrets-lint.sh's own, for the parity test.
var p6Rules = func() map[string]bool {
	m := map[string]bool{}
	for _, r := range shapeRules {
		if !r.extraP6 {
			m[r.name] = true
		}
	}
	return m
}()

// FindSecretShapes returns every credential-shaped match in s, at most one
// per rule (as secrets-lint.sh reports one finding per rule per line), ordered by offset.
func FindSecretShapes(s string) []Finding {
	var out []Finding
	for _, r := range shapeRules {
		if at, ok := r.find(s); ok {
			out = append(out, Finding{Rule: r.name, Offset: at})
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Offset < out[j].Offset })
	return out
}

func (r shapeRule) find(s string) (int, bool) {
	for pos := 0; pos <= len(s); {
		loc := r.re.FindStringIndex(s[pos:])
		if loc == nil {
			return 0, false
		}
		start, end := pos+loc[0], pos+loc[1]
		if r.accept == nil || r.accept(s, start, end) {
			return start, true
		}
		pos = start + 1
	}
	return 0, false
}

func hasPrefixAt(s string, at int, prefix string) bool {
	return at <= len(s) && len(s)-at >= len(prefix) && s[at:at+len(prefix)] == prefix
}

func isAlnum(b byte) bool {
	return b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' || b >= '0' && b <= '9'
}

func isUpperOrDigit(b byte) bool { return b >= 'A' && b <= 'Z' || b >= '0' && b <= '9' }
