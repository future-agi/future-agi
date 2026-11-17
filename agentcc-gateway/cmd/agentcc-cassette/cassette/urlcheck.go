package cassette

import "strings"

// The verify backstop for credentials in URLs. The scrubber replaces the
// user info and the credential parameters of every URL it reads in text;
// verify reads every URL in the final strings again, whatever route the
// scrubber took to the text that holds it (a form, a body, a header, a
// member name, Base64), and refuses one whose user info, or a credential
// parameter's value, is not a placeholder. It decides which parameters are
// credentials as urlString and query do, never more strictly, so it
// refuses only what a scrubbing route missed (or a hand edit put back).

// urlFindings names each URL in s that still holds a credential.
func urlFindings(s string, n *nameRules) []string {
	if !strings.Contains(s, "://") {
		return nil
	}
	var out []string
	for _, u := range urlRE.FindAllString(s, -1) {
		if urlHoldsCredential(strings.TrimRight(u, ".,;:!?)]}"), false, n, 0) {
			out = append(out, "url-credential")
		}
	}
	return out
}

// urlHoldsCredential reads raw's user info, its query (code and state are
// credentials when oauth says so or the query holds an OAuth companion)
// and its fragment (where they always are), and the URLs in their values,
// in the query's OAuth context, as urlBase, urlString and query read them.
func urlHoldsCredential(raw string, oauth bool, n *nameRules, depth int) bool {
	rest, frag, hasFrag := strings.Cut(raw, "#")
	base, query, hasQuery := strings.Cut(rest, "?")
	if start, end, ok := userinfoSpan(base); ok && !isPlaceholder(base[start:end]) {
		return true
	}
	if hasQuery && paramsHoldCredential(query, oauth || oauthQuery(query), n, depth) {
		return true
	}
	return hasFrag && strings.Contains(frag, "=") && paramsHoldCredential(frag, true, n, depth)
}

func paramsHoldCredential(raw string, oauth bool, n *nameRules, depth int) bool {
	for _, part := range strings.Split(raw, "&") {
		rawName, rawValue, ok := strings.Cut(part, "=")
		if !ok || rawValue == "" {
			continue
		}
		name, value := unescapeQuery(rawName), unescapeQuery(rawValue)
		switch {
		case n.secretParam(name, oauth):
			if !isPlaceholder(value) {
				return true
			}
		case depth < maxDepth && strings.Contains(value, "://"):
			for _, u := range urlRE.FindAllString(value, -1) {
				if urlHoldsCredential(strings.TrimRight(u, ".,;:!?)]}"), oauth, n, depth+1) {
					return true
				}
			}
		}
	}
	return false
}
