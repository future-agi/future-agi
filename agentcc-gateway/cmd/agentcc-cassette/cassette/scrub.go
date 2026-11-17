package cassette

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
)

type pass int

const (
	collectPass pass = iota
	replacePass
)

// scrubber walks a cassette twice over the same locations: the collect pass
// finds every sensitive value, then the replace pass rewrites each location,
// structurally where a rule names it (any length) and by substitution
// elsewhere.
type scrubber struct {
	pass     pass
	credHdrs map[string]bool
	idKeys   map[string]bool
	rules    *nameRules // credential parameters and members: built-in plus c.Rules
	ph       *placeholders
}

// scrub rewrites c in place and returns the placeholder table. The
// credential names it honours beyond the built-in ones are c.Rules (Save
// merges the ScrubConfig's into them first, so replay sees the same).
func scrub(c *Cassette, cfg ScrubConfig) (*placeholders, error) {
	s := &scrubber{credHdrs: map[string]bool{}, idKeys: setOf(cfg.IDKeys...), rules: compileRules(c.Rules), ph: newPlaceholders()}
	for _, h := range cfg.CredentialHeaders {
		s.credHdrs[strings.ToLower(h)] = true
	}
	for _, v := range cfg.KnownSecrets {
		s.ph.add(classToken, v)
	}
	for _, v := range cfg.Literals {
		s.ph.add(classLiteral, v)
	}
	clone, err := cloneCassette(c)
	if err != nil {
		return nil, err
	}
	if err := s.cassette(clone); err != nil {
		return nil, err
	}
	s.ph.assign()
	s.pass = replacePass
	if err := s.cassette(c); err != nil {
		return nil, err
	}
	c.Scrubbed = s.ph.counts()
	return s.ph, nil
}

func cloneCassette(c *Cassette) (*Cassette, error) {
	data, err := json.Marshal(c)
	if err != nil {
		return nil, err
	}
	var out Cassette
	return &out, json.Unmarshal(data, &out)
}

func (s *scrubber) cassette(c *Cassette) error {
	if c.MCP != nil {
		for i := range c.MCP.Exchanges {
			x := &c.MCP.Exchanges[i]
			for _, f := range []*json.RawMessage{&x.Params, &x.Result, &x.Error} {
				if len(*f) == 0 {
					continue
				}
				var schema func(string) bool
				if x.Method == "tools/list" && f == &x.Result {
					schema = isToolSchema
				}
				out, err := s.jsonDoc(*f, 0, schema)
				if err != nil {
					return fmt.Errorf("mcp exchange %d (%s): %w", i, x.Method, err)
				}
				*f = out
			}
		}
	}
	if c.HTTP != nil {
		for i := range c.HTTP.Exchanges {
			if err := s.httpExchange(&c.HTTP.Exchanges[i]); err != nil {
				return fmt.Errorf("http exchange %d: %w", i, err)
			}
		}
	}
	return nil
}

func (s *scrubber) httpExchange(x *HTTPExchange) error {
	q, r := &x.Request, &x.Response
	q.Headers = s.headers(q.Headers)
	q.Path = s.path(q.Path)
	q.Query = s.form(q.Query)
	var err error
	if q.Body, err = s.body(q.Body, HeaderValue(q.Headers, "Content-Type")); err != nil {
		return err
	}
	r.Headers = s.headers(r.Headers)
	r.Body, err = s.body(r.Body, HeaderValue(r.Headers, "Content-Type"))
	return err
}

func (s *scrubber) headers(h http.Header) http.Header {
	if len(h) == 0 {
		return h
	}
	names := make([]string, 0, len(h))
	for name := range h {
		names = append(names, name)
	}
	sort.Strings(names)
	out := make(http.Header, len(h))
	for _, name := range names {
		canon := http.CanonicalHeaderKey(name)
		for _, v := range h[name] {
			out[canon] = append(out[canon], s.headerValue(canon, v))
		}
	}
	return out
}

func (s *scrubber) headerValue(name, v string) string {
	n := strings.ToLower(name)
	switch {
	case v == "":
		return v
	case n == "cookie":
		return s.cookies(v)
	case n == "set-cookie":
		value, attrs, _ := strings.Cut(v, ";")
		out := s.cookies(value)
		if attrs != "" {
			out += ";" + s.text(attrs, textMode{}, 0)
		}
		return out
	case isCredentialHeader(n, s.credHdrs):
		return s.credential(v)
	case n == "location" || n == "content-location":
		return s.plain(s.urlString(v, true), false)
	case isIDKey(name, s.idKeys) && isIDValue(v):
		return s.id(v)
	}
	return s.text(v, textMode{}, 0)
}

// authSchemes are kept in front of a scrubbed credential, for readability.
var authSchemes = setOf("bearer", "basic", "token", "bot", "digest", "negotiate", "ntlm", "dpop",
	"mac", "oauth", "aws4-hmac-sha256", "hoba", "mutual", "apikey")

func (s *scrubber) credential(v string) string {
	if scheme, rest, ok := strings.Cut(v, " "); ok && authSchemes[strings.ToLower(scheme)] && strings.TrimSpace(rest) != "" {
		return scheme + " " + s.secret(strings.TrimSpace(rest))
	}
	return s.secret(v)
}

// cookies scrubs every value of a Cookie header (or a Set-Cookie's first
// pair), keeping the cookie names.
func (s *scrubber) cookies(v string) string {
	pairs := strings.Split(v, ";")
	for i, pair := range pairs {
		name, value, ok := strings.Cut(pair, "=")
		if ok && strings.TrimSpace(value) != "" {
			pairs[i] = name + "=" + s.secret(strings.TrimSpace(value))
		}
	}
	return strings.Join(pairs, ";")
}

// query scrubs a raw query string (or form body or URL fragment) parameter
// by parameter: a secret one structurally, an id one as an id, any other as
// text (in the query's OAuth context, for a URL inside it). An untouched
// parameter keeps its original encoding; one that is not valid
// percent-encoding is taken as written, never skipped. form adds the pass
// that reads a URL split across parameters whole.
func (s *scrubber) query(raw string, oauth bool) string {
	if raw == "" {
		return raw
	}
	parts := strings.Split(raw, "&")
	for i, part := range parts {
		rawName, rawValue, hasValue := strings.Cut(part, "=")
		if !hasValue || rawValue == "" {
			continue
		}
		name, value := unescapeQuery(rawName), unescapeQuery(rawValue)
		var out string
		switch {
		case s.rules.secretParam(name, oauth):
			out = s.secret(value)
		case isIDKey(name, s.idKeys) && isIDValue(value):
			out = s.id(value)
		default:
			out = s.text(value, textMode{oauth: oauth}, 0)
		}
		if out != value {
			parts[i] = rawName + "=" + url.QueryEscape(out)
		}
	}
	return strings.Join(parts, "&")
}

// path scrubs an escaped URL path; a changed path is escaped canonically.
func (s *scrubber) path(escaped string) string {
	decoded, err := url.PathUnescape(escaped)
	if err != nil {
		return s.text(escaped, textMode{}, 0)
	}
	out := s.text(decoded, textMode{}, 0)
	if out == decoded {
		return escaped
	}
	return (&url.URL{Path: out}).EscapedPath()
}

func (s *scrubber) body(b *Body, contentType string) (*Body, error) {
	switch {
	case b == nil:
		return nil, nil
	case len(b.JSON) > 0:
		out, err := s.jsonDoc(b.JSON, 0, nil)
		return &Body{JSON: out}, err
	case b.Text != "" && formBody(contentType, b.Text, s.formName):
		core, tail := splitTrailingSpace(b.Text)
		return &Body{Text: s.form(core) + tail}, nil
	case b.Text != "":
		return &Body{Text: s.text(b.Text, textMode{nested: true}, 0)}, nil
	}
	raw, err := base64.StdEncoding.DecodeString(b.Base64)
	if err != nil {
		return nil, err
	}
	if s.pass == replacePass {
		raw = []byte(s.ph.substitute(string(raw), false))
	}
	return &Body{Base64: base64.StdEncoding.EncodeToString(raw)}, nil
}

// formName reports a parameter that makes form-shaped text a form body to
// scrub as one: a credential or an id.
func (s *scrubber) formName(name string, oauth bool) bool {
	return s.rules.secretParam(name, oauth) || isIDKey(name, s.idKeys)
}

// jsonDoc scrubs a JSON document leaf by leaf. A member name is scrubbed as
// text (an email, uuid, literal or collected value in it is replaced like
// anywhere else, a literal only where it stands alone); whether a value is
// a credential or an id is decided on its member's original name, and in
// the JSON Schemas schema names (a tools/list result's), on schema.go's
// rules.
func (s *scrubber) jsonDoc(data []byte, depth int, schema func(string) bool) ([]byte, error) {
	return walkJSON(data, walkOptions{secret: s.rules.secretMember, schema: schema}, func(l jsonLeaf) []byte {
		switch {
		case l.name:
			return s.replaced(*l.str, s.text(*l.str, textMode{nested: true, name: true}, depth))
		case l.str == nil:
			return s.number(l)
		}
		v := *l.str
		out := v
		switch {
		case v == "" || isPlaceholder(v):
		case l.secret:
			out = s.secret(v)
		case isIDKey(l.key, s.idKeys) && isIDValue(v):
			out = s.id(v)
		default:
			out = s.text(v, textMode{nested: true}, depth)
		}
		return s.replaced(v, out)
	})
}

// replaced is out as a JSON string in the replace pass, when it differs
// from v; nil keeps v's bytes.
func (s *scrubber) replaced(v, out string) []byte {
	if s.pass == replacePass && out != v {
		return jsonString(out)
	}
	return nil
}

// number scrubs a JSON number. Under a credential member it is a
// credential, replaced at any length by a placeholder string (the type
// gives way, the value goes); under an id key of minID digits or more it
// is an id; anywhere else it is replaced only when it is a value collected
// elsewhere.
func (s *scrubber) number(l jsonLeaf) []byte {
	lit := string(l.num)
	if l.secret {
		if s.pass == collectPass {
			s.ph.add(classToken, lit)
			return nil
		}
		return jsonString(s.ph.get(lit))
	}
	if !digitsRE.MatchString(lit) {
		return nil
	}
	if s.pass == collectPass {
		if isIDKey(l.key, s.idKeys) && len(lit) >= minID {
			s.ph.add(classID, lit)
		}
		return nil
	}
	if !s.ph.global(lit) {
		return nil
	}
	if ph := s.ph.get(lit); digitsRE.MatchString(ph) {
		return []byte(ph)
	} else {
		return jsonString(ph) // a digit-only credential: the type gives way, the value goes
	}
}

// secret is a credential found where a rule names it: collected, then
// replaced at any length.
func (s *scrubber) secret(v string) string {
	if s.pass == collectPass {
		s.ph.add(classToken, v)
		return v
	}
	return s.ph.get(v)
}

func (s *scrubber) id(v string) string {
	if s.pass == collectPass {
		s.ph.add(classID, v)
		return v
	}
	return s.ph.get(v)
}

// HeaderValue reads a header whatever the case of its name in the file (a
// hand-edited cassette need not use canonical names).
func HeaderValue(h http.Header, name string) string {
	for k, v := range h {
		if strings.EqualFold(k, name) && len(v) > 0 {
			return v[0]
		}
	}
	return ""
}
