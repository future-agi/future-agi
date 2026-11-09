package telemetry

import (
	"net"
	"net/url"
	"strings"
)

// scrubURL is a parser only. Userinfo is never exported; query names are decoded
// before classification and sanitized values are escaped by net/url, not pasted.
func scrubURL(text string, depth int) string {
	if depth >= maxContentDepth {
		return redactedContent
	}
	u, err := url.Parse(text)
	if err != nil || u.Host == "" {
		return redactedContent
	}
	// Host/authority is user-controlled too; do not export a credential-shaped
	// DNS label. Leave ordinary hostnames, ports and IPv6 syntax untouched.
	host := u.Hostname()
	if ip, zone, hasZone := strings.Cut(host, "%"); hasZone && net.ParseIP(ip) != nil {
		// net/url has already decoded the IPv6 zone delimiter, not an escape.
		if scrubText(zone, depth+1) != zone {
			return redactedContent
		}
		host = ip
	}
	if scrubText(host, depth+1) != host {
		return redactedContent
	}
	changed := u.User != nil
	u.User = nil
	query, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		// Partial query parsing must not preserve unexamined credential material.
		return redactedContent
	}
	cleanQuery := make(url.Values, len(query))
	for name, values := range query {
		credential := credentialQueryName(name)
		cleanName := scrubText(name, depth+1)
		changed = changed || cleanName != name
		for _, value := range values {
			clean := redactedContent
			if !credential {
				clean = scrubText(value, depth+1)
			}
			changed = changed || clean != value
			cleanQuery.Add(cleanName, clean)
		}
	}
	if changed {
		u.RawQuery = cleanQuery.Encode()
	}
	if path := scrubText(u.Path, depth+1); path != u.Path {
		u.Path, u.RawPath, changed = path, "", true
	}
	if fragment := scrubText(u.Fragment, depth+1); fragment != u.Fragment {
		u.Fragment, u.RawFragment, changed = fragment, "", true
	}
	if !changed {
		return text
	}
	return u.String()
}

func credentialQueryName(name string) bool {
	// ParseQuery already unescapes once. Also handle a bounded number of nested
	// encodings; malformed or still-encoded names fail closed at the bound.
	for i := 0; i < 4; i++ {
		if isCredentialField(name) {
			return true
		}
		if !strings.Contains(name, "%") {
			return false
		}
		decoded, err := url.QueryUnescape(name)
		if err != nil {
			return true
		}
		name = decoded
	}
	return true
}
