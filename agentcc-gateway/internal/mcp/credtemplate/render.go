package credtemplate

import (
	"encoding/json"
	"net/url"
	"regexp"
	"slices"
	"strconv"
	"strings"
)

// redactedMark replaces a secret in text the template scrubs.
const redactedMark = "[redacted]"

// render places the template's URL parts on u, a copy the caller owns:
// each {name} of a url_variable part in the path (path-escaped, so it stays
// one segment) and in the query (query-escaped, so it stays one value);
// then each query part, after removing the parameter wherever u already
// has it (an HTTP+SSE endpoint or a redirect may name it).
func (t *Template) render(u *url.URL) {
	if len(t.vars) > 0 {
		if escaped := u.EscapedPath(); pathVarPattern.MatchString(escaped) {
			out := t.renderPath(escaped)
			if path, err := url.PathUnescape(out); err == nil && out != escaped {
				u.Path, u.RawPath = path, out
			}
		}
		u.RawQuery = queryVarPattern.ReplaceAllStringFunc(u.RawQuery, func(m string) string {
			if v, ok := t.variable(queryVarPattern.FindStringSubmatch(m)[1]); ok {
				return url.QueryEscape(v.Value)
			}
			return m
		})
	}
	if len(t.queries) == 0 {
		return
	}
	var kept []string
	for _, seg := range querySegments(u.RawQuery) {
		if !t.isQueryName(queryKey(seg)) {
			kept = append(kept, seg)
		}
	}
	for _, q := range t.queries {
		kept = append(kept, q.Name+"="+url.QueryEscape(q.Value))
	}
	u.RawQuery = strings.Join(kept, "&")
}

// renderPath fills the url_variable placeholders of an escaped path.
func (t *Template) renderPath(escaped string) string {
	return pathVarPattern.ReplaceAllStringFunc(escaped, func(m string) string {
		if v, ok := t.variable(pathVarPattern.FindStringSubmatch(m)[1]); ok {
			return url.PathEscape(v.Value)
		}
		return m
	})
}

// untemplate is the template's form of a URL an upstream sent back (a
// Location, an HTTP+SSE endpoint): a url_variable's value in the path or
// a query value becomes its {name} again, a query part's parameter and any
// parameter whose value is a secret are dropped, and whatever spelling of
// a secret is left is redacted. The client keeps only this form, and the
// template places the parts again on the request that follows it.
func (t *Template) untemplate(raw string) string {
	rest, fragment, hasFragment := strings.Cut(raw, "#")
	base, query, hasQuery := strings.Cut(rest, "?")
	for _, v := range t.vars {
		for _, sp := range spellings(v.Value) {
			base = strings.ReplaceAll(base, sp, "{"+v.Name+"}")
		}
	}
	out := base
	if hasQuery {
		if q := t.untemplateQuery(query); q != "" {
			out += "?" + q
		}
	}
	if hasFragment {
		out += "#" + fragment
	}
	return t.Scrub(out)
}

func (t *Template) untemplateQuery(query string) string {
	var kept []string
	for _, seg := range querySegments(query) {
		key, value, _ := strings.Cut(seg, "=")
		plain := unescapeQuery(value)
		if t.isQueryName(queryKey(seg)) {
			continue
		}
		if v, ok := t.variableWithValue(plain); ok {
			kept = append(kept, key+"={"+v.Name+"}")
			continue
		}
		if slices.Contains(t.values, plain) {
			continue
		}
		kept = append(kept, seg)
	}
	return strings.Join(kept, "&")
}

func (t *Template) isQueryName(name string) bool {
	for _, q := range t.queries {
		if q.Name == name {
			return true
		}
	}
	return false
}

func (t *Template) variableWithValue(value string) (Part, bool) {
	for _, v := range t.vars {
		if v.Value == value {
			return v, true
		}
	}
	return Part{}, false
}

// querySegments are a raw query's &-separated parameters, as written.
func querySegments(raw string) []string {
	if raw == "" {
		return nil
	}
	return strings.Split(raw, "&")
}

// queryKey is a raw parameter's name, unescaped.
func queryKey(seg string) string {
	key, _, _ := strings.Cut(seg, "=")
	return unescapeQuery(key)
}

func unescapeQuery(s string) string {
	if u, err := url.QueryUnescape(s); err == nil {
		return u
	}
	return s
}

// hexEscape is one %XX escape.
var hexEscape = regexp.MustCompile(`%[0-9A-F]{2}`)

// spellings are the forms a value takes in text that can reach a log or a
// caller: as written, query-escaped, path-escaped, inside a JSON string,
// inside a Go-quoted string, and the escaped forms in lower-case hex.
func spellings(v string) []string {
	if v == "" {
		return nil
	}
	j, _ := json.Marshal(v)
	q := strconv.Quote(v)
	forms := []string{v, url.QueryEscape(v), url.PathEscape(v), string(j[1 : len(j)-1]), q[1 : len(q)-1]}
	for _, f := range forms[1:3] {
		forms = append(forms, hexEscape.ReplaceAllStringFunc(f, strings.ToLower))
	}
	return forms
}

// longestFirst dedupes forms and orders them longest first, so a longer
// spelling is replaced whole before a shorter one inside it.
func longestFirst(forms []string) []string {
	out := slices.Clone(forms)
	slices.SortFunc(out, func(a, b string) int {
		if len(a) != len(b) {
			return len(b) - len(a)
		}
		return strings.Compare(a, b)
	})
	return slices.Compact(out)
}
