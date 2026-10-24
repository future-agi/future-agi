package eligibility

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

// anyGlob reports whether any pattern matches s.
func anyGlob(patterns []string, s string) bool {
	for _, p := range patterns {
		if globMatch(p, s) {
			return true
		}
	}
	return false
}

// CSVContains reports whether the comma list holds item exactly, ignoring
// spaces around entries, without allocating. The key ACL is recorded on the
// request as comma lists (auth_allowed_providers, auth_allowed_models).
func CSVContains(list, item string) bool {
	for list != "" {
		var entry string
		entry, list, _ = strings.Cut(list, ",")
		if strings.TrimSpace(entry) == item {
			return true
		}
	}
	return false
}

// vendorOf is the part of a model id before the first "/", or "".
func vendorOf(model string) string {
	if i := strings.IndexByte(model, '/'); i > 0 {
		return model[:i]
	}
	return ""
}

// equalFoldAny reports whether s equals any of list, case-insensitively.
func equalFoldAny(list []string, s string) bool {
	for _, x := range list {
		if strings.EqualFold(strings.TrimSpace(x), s) {
			return true
		}
	}
	return false
}
