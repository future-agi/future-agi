package routing

import "strings"

// globMatch matches s against pattern, case-insensitively, where "*" matches
// any run of characters, "/" included ("anthropic/*", "google/gemini-3*").
func globMatch(pattern, s string) bool {
	pattern, s = strings.ToLower(pattern), strings.ToLower(s)
	star := strings.IndexByte(pattern, '*')
	if star < 0 {
		return pattern == s
	}
	if !strings.HasPrefix(s, pattern[:star]) {
		return false
	}
	s, pattern = s[star:], pattern[star+1:]
	for {
		next := strings.IndexByte(pattern, '*')
		if next < 0 {
			return strings.HasSuffix(s, pattern)
		}
		i := strings.Index(s, pattern[:next])
		if i < 0 {
			return false
		}
		s, pattern = s[i+next:], pattern[next+1:]
	}
}

// MatchesLockPattern applies case-insensitive, star-only matching to org policy
// lists. The gateway-wide provider lock retains its exact matching semantics.
func MatchesLockPattern(patterns []string, target string) bool {
	for _, pattern := range patterns {
		if globMatch(pattern, target) {
			return true
		}
	}
	return false
}
