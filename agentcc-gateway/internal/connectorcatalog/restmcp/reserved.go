package restmcp

import (
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"unicode"
)

// Discovery's {+name} is intentionally narrower than arbitrary URI templates:
// only a declared, anchored resource-name pattern can introduce slash segments.
func compileResourcePattern(in *input) (*regexp.Regexp, error) {
	p := in.pattern
	if len(in.types) != 1 || in.types[0] != "string" || len(p) < 2 || len(p) > 512 || !strings.HasPrefix(p, "^") || !strings.HasSuffix(p, "$") || strings.Contains(p, "(?") || strings.Contains(p, `\`) || controlCharacter(p) {
		return nil, fmt.Errorf("reserved expansion needs a portable anchored string pattern")
	}
	compiled, err := regexp.Compile(`\A(?:` + p + `)\z`)
	if err != nil {
		return nil, fmt.Errorf("reserved expansion has an invalid pattern")
	}
	return compiled, nil
}

func controlCharacter(s string) bool {
	for _, r := range s {
		if unicode.IsControl(r) {
			return true
		}
	}
	return false
}

func resourcePath(name string, raw json.RawMessage, pattern *regexp.Regexp) (string, error) {
	var value string
	if json.Unmarshal(raw, &value) != nil || len(value) > 2048 || !pattern.MatchString(value) || strings.ContainsAny(value, "%?#\\") || controlCharacter(value) {
		return "", argErrorf("argument %q must be a safe resource name matching its declared pattern", name)
	}
	segments := strings.Split(value, "/")
	for i, segment := range segments {
		if segment == "" || segment == "." || segment == ".." {
			return "", argErrorf("argument %q contains an empty or dot segment", name)
		}
		// Escape all non-unreserved characters, including ':' (Discovery actions).
		// '%' is refused above, so encoded and double-encoded delimiters cannot hide.
		segments[i] = strings.ReplaceAll(url.QueryEscape(segment), "+", "%20")
	}
	return strings.Join(segments, "/"), nil
}
