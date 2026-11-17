package cassette

import (
	"encoding/base64"
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

var (
	emailRE = regexp.MustCompile(`[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}`)
	// a URL of any scheme (https, wss, postgres, ...; RFC 3986 3.1, so in
	// any case)
	urlRE = regexp.MustCompile("(?i)\\b[a-z][a-z0-9+.-]*://[^\\s\"'<>\\\\`]+")
)

// maxDepth bounds nesting: JSON in text, Base64 in JSON, and so on.
const maxDepth = 4

// textMode says what a piece of text may hold besides plain words.
type textMode struct {
	nested bool // JSON text and Base64-encoded text inside it are scrubbed too
	oauth  bool // it sits in an OAuth query: code and state of a URL in it are credentials
	name   bool // it is a member name: a literal in it is replaced only where it stands alone
}

// text scrubs one piece of free text: JSON inside it (structurally), text
// Base64-encoded inside it (decoded, scrubbed, encoded again the same way),
// secret parameters of every URL in it, then emails, uuids and every value
// already collected. In the collect pass it only collects.
func (s *scrubber) text(t string, m textMode, depth int) string {
	if t == "" {
		return t
	}
	if m.nested && depth < maxDepth {
		if trimmed := strings.TrimSpace(t); len(trimmed) > 1 && (trimmed[0] == '{' || trimmed[0] == '[') && json.Valid([]byte(trimmed)) {
			if out, err := s.jsonDoc([]byte(t), depth+1, nil); err == nil {
				return string(out)
			}
		}
		if decoded, enc, ok := decodeBase64Text(t); ok {
			inner := s.text(decoded, m, depth+1)
			if inner != decoded {
				return enc.EncodeToString([]byte(inner))
			}
			return t
		}
	}
	t = urlRE.ReplaceAllStringFunc(t, func(u string) string {
		core := strings.TrimRight(u, ".,;:!?)]}")
		return s.urlString(core, m.oauth) + u[len(core):]
	})
	return s.plain(t, m.name)
}

// plain collects the emails and uuids in t, or replaces every collected
// value in it (in a member name, a literal only where it stands alone).
func (s *scrubber) plain(t string, inName bool) string {
	if s.pass == replacePass {
		return s.ph.substitute(t, inName)
	}
	forms := []string{t}
	if u, err := url.PathUnescape(t); err == nil && u != t {
		forms = append(forms, u) // alice%40corp.example is an email too
	}
	for _, form := range forms {
		for _, e := range emailRE.FindAllString(form, -1) {
			s.ph.add(classEmail, e)
		}
		for _, loc := range uuidRE.FindAllStringIndex(form, -1) {
			if standsAlone(form, loc[0], loc[1]) {
				s.ph.add(classID, form[loc[0]:loc[1]])
			}
		}
	}
	return t
}

// urlString scrubs a URL's query and fragment parameters, keeping the rest of
// it byte for byte. Code and state are secret beside an OAuth companion in
// the URL's own query, always in a fragment (where the implicit flow
// returns tokens), and everywhere when oauth says so: in a Location
// header, and in a URL inside the value of an OAuth query (an unencoded
// callback URL loses its state to the outer query when split on &).
func (s *scrubber) urlString(raw string, oauth bool) string {
	rest, frag, hasFrag := strings.Cut(raw, "#")
	base, query, hasQuery := strings.Cut(rest, "?")
	base = s.urlBase(base)
	if hasQuery {
		query = s.query(query, oauth || oauthQuery(query))
		base += "?" + query
	}
	if hasFrag {
		if strings.Contains(frag, "=") {
			frag = s.query(frag, true)
		}
		base += "#" + frag
	}
	return base
}

// urlBase scrubs the user info of scheme://user:password@host as a
// credential, and the path of scheme://host/path, or of a bare path, as a
// request path is scrubbed (decoded, so a partly escaped email is found).
func (s *scrubber) urlBase(base string) string {
	if start, end, ok := userinfoSpan(base); ok {
		base = base[:start] + s.secret(base[start:end]) + base[end:]
	}
	scheme := strings.Index(base, "://")
	if scheme < 0 {
		return s.path(base)
	}
	slash := strings.IndexByte(base[scheme+3:], '/')
	if slash < 0 {
		return base
	}
	cut := scheme + 3 + slash
	return base[:cut] + s.path(base[cut:])
}

// userinfoSpan is where the user info of scheme://user:password@host/...
// lies in base (a URL without its query and fragment); ok is false when it
// has none. The scrubber and verify both ask it.
func userinfoSpan(base string) (start, end int, ok bool) {
	scheme := strings.Index(base, "://")
	if scheme < 0 {
		return 0, 0, false
	}
	start = scheme + 3
	authority := base[start:]
	if slash := strings.IndexByte(authority, '/'); slash >= 0 {
		authority = authority[:slash]
	}
	at := strings.LastIndexByte(authority, '@')
	if at <= 0 {
		return 0, 0, false
	}
	return start, start + at, true
}

// isIDValue reports a value distinctive enough to scrub as an id everywhere.
func isIDValue(v string) bool {
	return len(v) >= minID && len(v) <= 256 && !strings.ContainsAny(v, " \t\r\n") &&
		!isPlaceholder(v) && !emailRE.MatchString(v)
}

// decodeBase64Text decodes t when it is Base64 of readable text and encodes
// back to exactly t, returning the encoding to use again. Binary payloads
// (images, archives) are left to the shape scan.
func decodeBase64Text(t string) (string, *base64.Encoding, bool) {
	dec, enc, ok := decodeBase64(t)
	if !ok || len(dec) < 12 || !utf8.Valid(dec) {
		return "", nil, false
	}
	printable := 0
	for _, r := range string(dec) {
		if unicode.IsPrint(r) || r == '\n' || r == '\r' || r == '\t' {
			printable++
		}
	}
	if printable*100 < utf8.RuneCount(dec)*95 {
		return "", nil, false
	}
	return string(dec), enc, true
}

// decodeBase64 decodes t in whichever Base64 alphabet and padding it uses,
// when re-encoding gives exactly t back.
func decodeBase64(t string) ([]byte, *base64.Encoding, bool) {
	if len(t) < 16 || len(t) > 64<<20 {
		return nil, nil, false
	}
	urlSafe, std := strings.ContainsAny(t, "-_"), strings.ContainsAny(t, "+/")
	if urlSafe && std {
		return nil, nil, false
	}
	for _, r := range strings.TrimRight(t, "=") {
		if !isAlnum(byte(r)) && !strings.ContainsRune("-_+/", r) {
			return nil, nil, false
		}
	}
	padded := strings.HasSuffix(t, "=") || len(t)%4 == 0
	enc := base64.RawStdEncoding
	switch {
	case urlSafe && padded:
		enc = base64.URLEncoding
	case urlSafe:
		enc = base64.RawURLEncoding
	case padded:
		enc = base64.StdEncoding
	}
	dec, err := enc.DecodeString(t)
	if err != nil || enc.EncodeToString(dec) != t {
		return nil, nil, false
	}
	return dec, enc, true
}

// formBody reports a body to read as form parameters: one sent as a form,
// or text shaped like one in which some parameter is named as named says
// (X's OAuth 1.0a token answer comes as text/html; a client may send a form
// with no Content-Type). Form-shaped is name=value pairs joined by &, with
// no whitespace inside, parsing as a query, and not Base64 as a whole (a
// padded Base64 body ends in =). The scrubber and the replay keys both ask
// this, so they read a body the same way.
func formBody(contentType, text string, named func(name string, oauth bool) bool) bool {
	switch {
	case isFormType(contentType):
		return true
	case isJSONType(contentType):
		return false
	}
	core, _ := splitTrailingSpace(text)
	if core == "" || strings.ContainsAny(core, " \t\r\n") {
		return false
	}
	if _, err := url.ParseQuery(core); err != nil {
		return false
	}
	if _, _, ok := decodeBase64(core); ok {
		return false
	}
	oauth, found := oauthQuery(core), false
	for _, part := range strings.Split(core, "&") {
		rawName, _, ok := strings.Cut(part, "=")
		if !ok || rawName == "" {
			return false
		}
		found = found || named(unescapeQuery(rawName), oauth)
	}
	return found
}

// form scrubs a query string or a form body: parameter by parameter, then
// as text, so a URL in it is read whole. Splitting on & glues a URL in
// name position to its first parameter's name (a bare callback URL read as
// a form: https://app/cb?code=...&state=...) and cuts an unencoded URL
// value's later parameters off into the form; the text pass reads each URL
// with its whole query, in the form's OAuth context. Placeholders the
// first pass wrote are inert in the second.
func (s *scrubber) form(raw string) string {
	oauth := oauthQuery(raw)
	return s.text(s.query(raw, oauth), textMode{oauth: oauth}, 0)
}

// splitTrailingSpace is t without its trailing whitespace, and that
// whitespace.
func splitTrailingSpace(t string) (string, string) {
	core := strings.TrimRight(t, " \t\r\n")
	return core, t[len(core):]
}

// unescapeQuery decodes a query name or value, or takes it as written when
// it is not valid percent-encoding.
func unescapeQuery(s string) string {
	if u, err := url.QueryUnescape(s); err == nil {
		return u
	}
	return s
}
