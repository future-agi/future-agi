package cassette

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"sort"
	"strconv"
	"strings"
)

// Replay keys. Replay computes the key of a recorded exchange and of a live
// request with the same Keyer, so the two meet exactly when the requests
// are the same request. A credential location is blinded to one fixed
// marker on both sides: the cassette holds a placeholder there, the live
// request a real credential, and neither may decide the match.

const blinded = "<blinded>"

// Keyer computes replay keys under one set of credential names: the
// built-in ones plus every name the cassettes it was made for add (their
// Rules). A nil *Keyer uses the built-in names alone.
type Keyer struct {
	names *nameRules
}

// NewKeyer is the Keyer for a set of cassettes served together.
func NewKeyer(cs ...*Cassette) *Keyer {
	rs := make([]*Rules, 0, len(cs))
	for _, c := range cs {
		if c != nil {
			rs = append(rs, c.Rules)
		}
	}
	return &Keyer{names: compileRules(rs...)}
}

func (k *Keyer) rules() *nameRules {
	if k == nil {
		return nil
	}
	return k.names
}

// MCPKey is an MCP request's replay key: tools/call and prompts/get by name
// and canonical arguments, resources/read by uri, a list by its cursor,
// initialize and server/discover by method alone, anything else by its
// canonical params. _meta never counts.
func (k *Keyer) MCPKey(method string, params json.RawMessage) (string, error) {
	var p struct {
		Name      string          `json:"name"`
		URI       string          `json:"uri"`
		Cursor    string          `json:"cursor"`
		Arguments json.RawMessage `json:"arguments"`
	}
	if len(params) > 0 && !bytes.Equal(params, []byte("null")) {
		if err := json.Unmarshal(params, &p); err != nil {
			return "", fmt.Errorf("%s params: %w", method, err)
		}
	}
	switch method {
	case "tools/call", "prompts/get":
		args := p.Arguments
		if len(args) == 0 || bytes.Equal(args, []byte("null")) {
			args = json.RawMessage(`{}`)
		}
		canon, err := canonicalJSON(args, k.rules())
		if err != nil {
			return "", fmt.Errorf("%s arguments: %w", method, err)
		}
		return method + "\n" + p.Name + "\n" + string(canon), nil
	case "resources/read":
		return method + "\n" + p.URI, nil
	case "tools/list", "resources/list", "prompts/list", "resources/templates/list":
		return method + "\n" + p.Cursor, nil
	case "initialize", "server/discover":
		return method, nil
	}
	rest := map[string]json.RawMessage{}
	if len(params) > 0 && !bytes.Equal(params, []byte("null")) {
		if err := json.Unmarshal(params, &rest); err != nil {
			return "", fmt.Errorf("%s params: %w", method, err)
		}
	}
	delete(rest, "_meta")
	data, _ := json.Marshal(rest)
	canon, err := canonicalJSON(data, k.rules())
	return method + "\n" + string(canon), err
}

// HTTPKey is an HTTP request's replay key: the method, the path escaped
// canonically, the query with credential parameters blinded and pairs
// sorted, and the sha256 of the body in canonical form (JSON and form
// bodies normalized and blinded; anything else as bytes).
func (k *Keyer) HTTPKey(method, escapedPath, rawQuery, contentType string, body []byte) string {
	return strings.ToUpper(method) + "\n" + canonicalPath(escapedPath) + "\n" + canonicalQuery(rawQuery, k.rules()) + "\n" +
		bodyDigest(contentType, body, k.rules())
}

func canonicalPath(p string) string {
	d, err := url.PathUnescape(p)
	if err != nil {
		return p
	}
	return (&url.URL{Path: d}).EscapedPath()
}

// canonicalQuery sorts a query's pairs and blinds its credential
// parameters. A name or value that is not valid percent-encoding is taken
// as written, as the scrubber takes it.
func canonicalQuery(raw string, n *nameRules) string {
	oauth := oauthQuery(raw)
	vals := url.Values{}
	for _, part := range strings.Split(raw, "&") {
		if part == "" {
			continue
		}
		rawName, rawValue, _ := strings.Cut(part, "=")
		name, value := unescapeQuery(rawName), unescapeQuery(rawValue)
		if n.secretParam(name, oauth) {
			value = blinded
		}
		vals.Add(name, value)
	}
	return vals.Encode()
}

// bodyDigest hashes a body in canonical form: JSON normalized, a form (or
// form-shaped text, as the scrubber reads it) as its canonical query.
func bodyDigest(contentType string, body []byte, n *nameRules) string {
	canon := body
	switch {
	case len(body) == 0:
	case isJSONType(contentType) || contentType == "" && json.Valid(body):
		if c, err := canonicalJSON(body, n); err == nil {
			canon = c
		}
	case formBody(contentType, string(body), n.secretParam):
		canon = []byte(canonicalQuery(string(body), n))
	}
	sum := sha256.Sum256(canon)
	return hex.EncodeToString(sum[:])
}

// canonicalJSON re-encodes a JSON value with sorted keys, no insignificant
// whitespace, one spelling per number (1, 1.0 and 1e0 are 1), and every
// string or number under a credential member blinded (code and state too,
// in an object with an OAuth companion member).
func canonicalJSON(data []byte, n *nameRules) ([]byte, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(normalize(v, false, n)); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

func normalize(v any, secret bool, n *nameRules) any {
	switch x := v.(type) {
	case map[string]any:
		oauth := false
		for k := range x {
			oauth = oauth || oauthJSONCompanions[normName(k)]
		}
		for k, val := range x {
			x[k] = normalize(val, secret || n.secretMember(k, oauth), n)
		}
	case []any:
		for i := range x {
			x[i] = normalize(x[i], secret, n)
		}
	case string:
		if secret {
			return blinded
		}
	case json.Number:
		if secret {
			return blinded
		}
		return canonicalNumber(x)
	}
	return v
}

func canonicalNumber(n json.Number) json.Number {
	s := string(n)
	if !strings.ContainsAny(s, ".eE") {
		if s == "-0" {
			return "0"
		}
		return n
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return n
	}
	if f == math.Trunc(f) && math.Abs(f) < 1e15 {
		return json.Number(strconv.FormatInt(int64(f), 10))
	}
	return json.Number(strconv.FormatFloat(f, 'g', -1, 64))
}

// DescribeMCP names an MCP request for a miss report without its values:
// the method, the tool, prompt or resource, the argument names (masked
// when one looks like data) and a hash.
func (k *Keyer) DescribeMCP(method string, params json.RawMessage) string {
	var p struct {
		Name      string                     `json:"name"`
		URI       string                     `json:"uri"`
		Arguments map[string]json.RawMessage `json:"arguments"`
	}
	_ = json.Unmarshal(params, &p)
	key, _ := k.MCPKey(method, params)
	names := make([]string, 0, len(p.Arguments))
	for n := range p.Arguments {
		names = append(names, maskName(n))
	}
	sort.Strings(names)
	target := p.Name
	if p.URI != "" {
		scheme, _, _ := strings.Cut(p.URI, ":")
		target = "<" + maskSegment(scheme) + " uri " + digest(p.URI) + ">" // a uri is data, like an argument
	}
	return fmt.Sprintf("%s %s args(%s) key sha256:%s", method, target, strings.Join(names, ","), digest(key))
}

// DescribeHTTP names an HTTP request for a miss report without query or
// body values: the method, the path (long or credential-shaped segments
// hashed), the parameter names (masked likewise) and the body's hash.
func (k *Keyer) DescribeHTTP(method, escapedPath, rawQuery, contentType string, body []byte) string {
	segs := strings.Split(canonicalPath(escapedPath), "/")
	for i, s := range segs {
		segs[i] = maskSegment(s)
	}
	seen := map[string]bool{}
	var names []string
	for _, part := range strings.Split(rawQuery, "&") {
		rawName, _, _ := strings.Cut(part, "=")
		if n := maskName(unescapeQuery(rawName)); part != "" && !seen[n] {
			seen[n] = true
			names = append(names, n)
		}
	}
	sort.Strings(names)
	return fmt.Sprintf("%s %s query(%s) body sha256:%s", strings.ToUpper(method), strings.Join(segs, "/"),
		strings.Join(names, ","), bodyDigest(contentType, body, k.rules())[:12])
}

// maskName hides an argument or parameter name that looks like data (an
// email, a uuid, a credential shape) or is too long to be a name.
func maskName(s string) string {
	if len(s) > 64 || len(FindSecretShapes(s)) > 0 || emailRE.MatchString(s) || uuidRE.MatchString(s) {
		return "<" + digest(s) + ">"
	}
	return s
}

func maskSegment(s string) string {
	if len(s) > 24 || len(FindSecretShapes(s)) > 0 || emailRE.MatchString(s) {
		return "<" + digest(s) + ">"
	}
	return s
}

func digest(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:6])
}
