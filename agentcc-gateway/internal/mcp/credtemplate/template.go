package credtemplate

import (
	"fmt"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
)

// Template is a credential compiled for one upstream server's URL: its
// parts and the origin they go to. It is immutable, so one template serves
// any number of requests at once.
type Template struct {
	origin  string // scheme://host:port, the host lower-cased and the port spelled
	parts   []Part
	headers []Part
	queries []Part
	vars    []Part
	// secrets are every spelling of every secret part's value, longest
	// first (Scrub); values are the values themselves.
	secrets []string
	values  []string
}

// The {name} placeholders a URL holds: escaped in a path (Go's EscapedPath
// escapes the braces), raw or escaped in a query, and raw in an unescaped
// path, which TestSecretURLVariableRendered compares against the escaped
// count so that no spelling is missed.
var (
	pathVarPattern  = regexp.MustCompile(`%7[Bb]([A-Za-z_][A-Za-z0-9_]{0,31})%7[Dd]`)
	rawVarPattern   = regexp.MustCompile(`\{([A-Za-z_][A-Za-z0-9_]{0,31})\}`)
	queryVarPattern = regexp.MustCompile(`(?:\{|%7[Bb])([A-Za-z_][A-Za-z0-9_]{0,31})(?:\}|%7[Dd])`)
)

// ForSecret compiles a server's pushed secret (Parts) for its URL, with no
// catalog: the parts are the pushed ones. Errors name the secret's field.
func ForSecret(rawURL string, sec Secret) (*Template, error) {
	parts, err := Parts(sec)
	if err != nil {
		return nil, err
	}
	return compile(rawURL, parts, pushedFields(sec, len(parts)))
}

// Check is ForSecret without the template: whether the gateway can place
// sec for rawURL exactly as stated. The admin PUT and the bulk load use it;
// the rules that need the catalog are applied where the template is built.
func Check(rawURL string, sec Secret) error {
	_, err := ForSecret(rawURL, sec)
	return err
}

// Compile builds a template from parts for rawURL. A URL or part the
// template could not place exactly is refused, naming the part
// ("parts[i].name") or "url", never a value:
//   - 1 to MaxParts parts, each well formed (an HTTP token not set by the
//     gateway, a queryName or a varName; a value with no control character
//     or surrounding space; a prefix on a header part only);
//   - no two parts in the same header (any case), parameter or variable;
//   - an http or https URL with a host, no user info and no fragment;
//   - every {name} in the URL's path or query filled by a url_variable part,
//     and every url_variable part filling one; never in the host, the port
//     or the user info, which do not parse, so a secret never sets the
//     origin; a value never makes a path segment "." or "..";
//   - a query part never names a parameter the URL already sets.
func Compile(rawURL string, parts ...Part) (*Template, error) {
	fields := make([]fieldNames, len(parts))
	for i := range parts {
		fields[i] = fieldNames{prefix: fmt.Sprintf("parts[%d].", i)}
	}
	return compile(rawURL, parts, fields)
}

// fieldNames names one part's fields in errors: under prefix, with name
// standing for the field that holds the part's name ("name" when empty);
// fixed, when set, names every field of the part (a catalog constant is
// the server's catalog_ref).
type fieldNames struct {
	prefix, name, fixed string
}

func (f fieldNames) of(sub string) string {
	if f.fixed != "" {
		return f.fixed
	}
	if sub == "name" && f.name != "" {
		return f.prefix + f.name
	}
	return f.prefix + sub
}

// pushedFields names each pushed part's fields: the secret's own for the
// first (whose name is its header field when it states no placement), and
// parts[i] for the rest.
func pushedFields(sec Secret, n int) []fieldNames {
	fields := make([]fieldNames, n)
	if sec.In == "" {
		fields[0].name = "header"
	}
	for i := 1; i < n; i++ {
		fields[i] = fieldNames{prefix: fmt.Sprintf("parts[%d].", i-1)}
	}
	return fields
}

func compile(rawURL string, parts []Part, fields []fieldNames) (*Template, error) {
	if len(parts) == 0 || len(parts) > MaxParts {
		return nil, refuse("parts", "a credential has 1 to %d parts", MaxParts)
	}
	seen := map[string]bool{}
	for i, p := range parts {
		if f, reason := checkPart(p); f != "" {
			return nil, refuse(fields[i].of(f), "%s", reason)
		}
		if seen[p.key()] {
			return nil, refuse(fields[i].of("name"), "is a second %s part under the same name", p.In)
		}
		seen[p.key()] = true
	}
	u, err := parseServerURL(rawURL)
	if err != nil {
		return nil, err
	}
	t := &Template{origin: origin(u), parts: append([]Part(nil), parts...)}
	for _, p := range t.parts {
		switch p.In {
		case InHeader:
			p.Name = http.CanonicalHeaderKey(p.Name)
			t.headers = append(t.headers, p)
		case InQuery:
			t.queries = append(t.queries, p)
		case InURLVariable:
			t.vars = append(t.vars, p)
		}
		if !p.Constant {
			t.values = append(t.values, p.Value)
			t.secrets = append(t.secrets, spellings(p.Value)...)
		}
	}
	t.secrets = longestFirst(t.secrets)
	if err := t.checkPlaceholders(u, fields); err != nil {
		return nil, err
	}
	if err := t.checkQuery(u, fields); err != nil {
		return nil, err
	}
	return t, nil
}

// parseServerURL is the server URL as the template places parts on it.
// Errors never quote the URL, which may hold a mistyped value.
func parseServerURL(rawURL string) (*url.URL, error) {
	if strings.Contains(rawURL, "#") {
		return nil, refuse("url", "has a fragment, which is never sent")
	}
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, refuse("url", "does not parse as a URL (a {variable} in the host, port or user info never does)")
	}
	switch {
	case u.Scheme != "http" && u.Scheme != "https":
		return nil, refuse("url", "is not an http or https URL")
	case u.Opaque != "" || u.Host == "":
		return nil, refuse("url", "has no host")
	case u.User != nil:
		return nil, refuse("url", "has user info")
	}
	return u, nil
}

// origin is a URL's scheme and host:port, the host lower-cased and the
// default port spelled.
func origin(u *url.URL) string {
	port := u.Port()
	if port == "" {
		port = "80"
		if u.Scheme == "https" {
			port = "443"
		}
	}
	return u.Scheme + "://" + strings.ToLower(net.JoinHostPort(u.Hostname(), port))
}

func (t *Template) checkPlaceholders(u *url.URL, fields []fieldNames) error {
	escaped := u.EscapedPath()
	inPath := pathVarPattern.FindAllStringSubmatch(escaped, -1)
	if len(rawVarPattern.FindAllString(u.Path, -1)) != len(inPath) {
		return refuse("url", "spells a {variable} with escapes inside its name, which the gateway cannot fill exactly")
	}
	used := map[string]bool{}
	for _, m := range append(inPath, queryVarPattern.FindAllStringSubmatch(u.RawQuery, -1)...) {
		if _, ok := t.variable(m[1]); !ok {
			return refuse("url", "has a {%s} placeholder that no credential part fills", m[1])
		}
		used[m[1]] = true
	}
	for i, p := range t.parts {
		if p.In == InURLVariable && !used[p.Name] {
			return refuse(fields[i].of("name"), "names a variable the server's URL does not hold in its path or query")
		}
	}
	for _, seg := range strings.Split(escaped, "/") {
		if !pathVarPattern.MatchString(seg) {
			continue
		}
		rendered, _ := url.PathUnescape(t.renderPath(seg))
		if rendered == "." || rendered == ".." {
			name := pathVarPattern.FindStringSubmatch(seg)[1]
			return refuse(fields[t.partIndex(InURLVariable, name)].of("value"), "would make the path segment . or ..")
		}
	}
	return nil
}

func (t *Template) checkQuery(u *url.URL, fields []fieldNames) error {
	for _, seg := range querySegments(u.RawQuery) {
		key := queryKey(seg)
		for i, p := range t.parts {
			if p.In == InQuery && p.Name == key {
				return refuse(fields[i].of("name"), "is a query parameter the server's URL already sets")
			}
		}
	}
	return nil
}

// variable is the url_variable part named name.
func (t *Template) variable(name string) (Part, bool) {
	for _, v := range t.vars {
		if v.Name == name {
			return v, true
		}
	}
	return Part{}, false
}

func (t *Template) partIndex(in In, name string) int {
	for i, p := range t.parts {
		if p.In == in && p.Name == name {
			return i
		}
	}
	return 0
}

// HeaderNames are the template's header parts, canonical, in part order:
// the headers a client drops on a redirect that leaves the origin.
func (t *Template) HeaderNames() []string {
	names := make([]string, len(t.headers))
	for i, h := range t.headers {
		names[i] = h.Name
	}
	return names
}

// Scrub replaces every spelling of every secret part's value in s (as
// written, query-escaped, path-escaped, JSON- and Go-quoted, and the
// escaped ones in lower-case hex) with "[redacted]", longest first.
// Constant parts are not secrets and are left.
func (t *Template) Scrub(s string) string {
	for _, sp := range t.secrets {
		s = strings.ReplaceAll(s, sp, redactedMark)
	}
	return s
}
