package restmcp

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
)

// userAgent is sent on every vendor request.
const userAgent = "agentcc-connectors/1"

// argError is a tool call whose arguments the runtime cannot send: the
// caller (the model) gets it as an isError result and can correct itself.
type argError struct{ msg string }

func (e *argError) Error() string { return e.msg }

func argErrorf(format string, args ...any) *argError {
	return &argError{msg: fmt.Sprintf(format, args...)}
}

// request is a vendor request before its origin is chosen.
type request struct {
	method string
	path   string // escaped, from operation.path
	query  []queryParam
	body   []byte // nil: no body
}

type queryParam struct{ name, value string }

// buildRequest places the arguments by CN4's request contract: {name} in
// operation.path is that input, one percent-encoded segment; the body input
// is the JSON body; every other input is a query parameter under its
// x-wireName, an array as one parameter per element; a const input is sent
// with its value whether or not the caller gave it. An argument the schema
// does not define, a missing required one, or a value that cannot be placed
// is refused.
func (t *Tool) buildRequest(args map[string]json.RawMessage) (*request, error) {
	for name := range args {
		if t.schema.inputs[name] == nil {
			return nil, argErrorf("unknown argument %q; %s takes %s", name, t.Name, strings.Join(t.schema.names(), ", "))
		}
	}
	values := map[string]json.RawMessage{}
	for name, in := range t.schema.inputs {
		v, given := args[name]
		if given && isNull(v) {
			given = false
		}
		if in.constVal != nil {
			if given && !jsonEqual(v, in.constVal) {
				return nil, argErrorf("argument %q must be %s", name, in.constVal)
			}
			v, given = in.constVal, true
		}
		if given {
			values[name] = v
		}
	}
	for _, name := range t.schema.required {
		if _, ok := values[name]; !ok {
			return nil, argErrorf("missing required argument %q", name)
		}
	}
	req := &request{method: t.method}
	path := t.path
	for _, name := range t.pathParams {
		if pattern := t.reserved[name]; pattern != nil {
			seg, err := resourcePath(name, values[name], pattern)
			if err != nil {
				return nil, err
			}
			path = strings.ReplaceAll(path, "{+"+name+"}", seg)
			// A mixed ordinary/reserved occurrence must remain a single segment.
		}
		seg, err := pathSegment(name, values[name])
		if err != nil {
			return nil, err
		}
		path = strings.ReplaceAll(path, "{"+name+"}", seg)
	}
	req.path = path
	names := make([]string, 0, len(values))
	for name := range values {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		v := values[name]
		switch {
		case contains(t.pathParams, name):
		case name == bodyInput:
			var buf bytes.Buffer
			if err := json.Compact(&buf, v); err != nil {
				return nil, argErrorf("argument %q is not valid JSON", name)
			}
			req.body = buf.Bytes()
		default:
			params, err := queryValues(t.schema.inputs[name].wireName, name, v)
			if err != nil {
				return nil, err
			}
			req.query = append(req.query, params...)
		}
	}
	return req, nil
}

// pathSegment is one path input as a percent-encoded segment. Only strings
// and numbers are placed, and "." and ".." are refused: they would change
// which resource the path names.
func pathSegment(name string, v json.RawMessage) (string, error) {
	text, ok := scalarText(v)
	if !ok {
		return "", argErrorf("argument %q must be a string or a number", name)
	}
	if text == "" || text == "." || text == ".." {
		return "", argErrorf("argument %q must name a resource, not %q", name, text)
	}
	return url.PathEscape(text), nil
}

// queryValues is one query input: a scalar as one parameter, an array of
// scalars as one parameter per element.
func queryValues(wire, name string, v json.RawMessage) ([]queryParam, error) {
	if text, ok := scalarText(v); ok {
		return []queryParam{{wire, text}}, nil
	}
	var items []json.RawMessage
	if json.Unmarshal(v, &items) == nil {
		out := make([]queryParam, 0, len(items))
		for _, it := range items {
			text, ok := scalarText(it)
			if !ok {
				return nil, argErrorf("argument %q: each element must be a string, a number or a boolean", name)
			}
			out = append(out, queryParam{wire, text})
		}
		return out, nil
	}
	return nil, argErrorf("argument %q must be a string, a number, a boolean or a list of them", name)
}

// scalarText is a JSON string's text, or a number's or boolean's literal.
func scalarText(v json.RawMessage) (string, bool) {
	v = bytes.TrimSpace(v)
	if len(v) == 0 {
		return "", false
	}
	switch v[0] {
	case '"':
		var s string
		if json.Unmarshal(v, &s) != nil {
			return "", false
		}
		return s, true
	case 't', 'f':
		var b bool
		if json.Unmarshal(v, &b) != nil {
			return "", false
		}
		return string(v), true
	case '{', '[', 'n':
		return "", false
	}
	var n json.Number
	if json.Unmarshal(v, &n) != nil {
		return "", false
	}
	return n.String(), true
}

func isNull(v json.RawMessage) bool { return string(bytes.TrimSpace(v)) == "null" }

func jsonEqual(a, b json.RawMessage) bool {
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	xa, _ := json.Marshal(x)
	ya, _ := json.Marshal(y)
	return bytes.Equal(xa, ya)
}

// encodeQuery writes the parameters in order. Names keep "$" (an OData
// option such as Graph's $top): RFC 3986 allows it in a query, and some
// vendors read the name only unescaped.
func encodeQuery(params []queryParam) string {
	var b strings.Builder
	for i, p := range params {
		if i > 0 {
			b.WriteByte('&')
		}
		b.WriteString(strings.ReplaceAll(url.QueryEscape(p.name), "%24", "$"))
		b.WriteByte('=')
		b.WriteString(url.QueryEscape(p.value))
	}
	return b.String()
}

// httpRequest is the vendor request at base (the tool's origin, or its
// replay), with the credential placed.
func (r *request) httpRequest(ctx context.Context, base string, cred *credential) (*http.Request, error) {
	params := append([]queryParam(nil), r.query...)
	if cred != nil {
		names := make([]string, 0, len(cred.query))
		for n := range cred.query {
			names = append(names, n)
		}
		sort.Strings(names)
		for _, n := range names {
			for _, v := range cred.query[n] {
				params = append(params, queryParam{n, v})
			}
		}
	}
	target := base + r.path
	if q := encodeQuery(params); q != "" {
		target += "?" + q
	}
	var body *bytes.Reader
	if r.body != nil {
		body = bytes.NewReader(r.body)
	}
	var req *http.Request
	var err error
	if body != nil {
		req, err = http.NewRequestWithContext(ctx, r.method, target, body)
	} else {
		req, err = http.NewRequestWithContext(ctx, r.method, target, nil)
	}
	if err != nil {
		return nil, fmt.Errorf("the request could not be built") // never echo target: it may hold a query credential
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", userAgent)
	if r.body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if cred != nil {
		for k, vs := range cred.header {
			req.Header[k] = append([]string(nil), vs...)
		}
	}
	return req, nil
}
