package protocol

import (
	"bytes"
	"encoding/json"
	"net/http"
	"regexp"
	"strconv"
	"strings"
)

// Mcp-Param-* headers: the tool parameters an inputSchema marks with
// x-mcp-header, mirrored by value (2026-07-28 Streamable HTTP, "Custom
// Headers from Tool Parameters").

// maxMarkDepth bounds the properties chain a marked parameter may sit on, and
// with it the walks of the schema and of the arguments.
const maxMarkDepth = 16

// markedParam is one parameter a tool mirrors as a header:
// Mcp-Param-{header} carries the value at path in the call's arguments.
type markedParam struct {
	header string
	path   []string
}

// markedParams returns the parameters a tool's inputSchema marks with
// x-mcp-header: every property reachable from the root through properties
// keys only (the spec's "statically reachable"; an annotation anywhere else
// makes the tool invalid, and marks nothing here), to maxMarkDepth. Schema
// keywords are matched exactly, as JSON Schema spells them. A property whose
// schema is not an object (true, false) marks nothing and hides no other. Two
// properties that name one header are both kept, and both must match it.
// deep is true when the keyword appears below maxMarkDepth, where the marks
// are not read: the caller refuses the call rather than skip a check.
func markedParams(schema json.RawMessage) (marks []markedParam, deep bool) {
	var out []markedParam
	var walk func(schema json.RawMessage, path []string)
	walk = func(schema json.RawMessage, path []string) {
		var s map[string]json.RawMessage
		if json.Unmarshal(schema, &s) != nil {
			return
		}
		if len(path) >= maxMarkDepth {
			// This node's own mark was read; marks under its properties are not.
			if strs, err := Strings(s["properties"]); err == nil {
				for _, str := range strs {
					deep = deep || str == xMCPHeader
				}
			}
			return
		}
		var props map[string]json.RawMessage
		if json.Unmarshal(s["properties"], &props) != nil {
			return
		}
		for prop, def := range props {
			at := append(append([]string(nil), path...), prop)
			var d map[string]json.RawMessage
			if json.Unmarshal(def, &d) != nil {
				continue // a boolean schema
			}
			var header string
			if json.Unmarshal(d[xMCPHeader], &header) == nil && header != "" {
				out = append(out, markedParam{header: header, path: at})
			}
			walk(def, at)
		}
	}
	walk(schema, nil)
	return out, deep
}

// checkParamHeaders compares each parameter the schema marks with its
// Mcp-Param-{Name} header: a value in the arguments needs the header, equal
// to it once decoded (numbers numerically); no value (absent, or null)
// allows no header. Every level of the arguments on the way to a marked
// parameter must carry that parameter's member once, spelled exactly
// (oneSpelling). The messages name only the headers and members the client
// sent, not the tool's parameters, which a caller who may not use the tool
// must not learn from them.
func checkParamHeaders(h http.Header, schema, arguments json.RawMessage) *Error {
	marks, deep := markedParams(schema)
	if deep {
		return HeaderMismatch("the tool mirrors parameters deeper than the gateway checks, so its calls are refused")
	}
	for _, m := range marks {
		name := HeaderParamPrefix + m.header
		value, present, err := valueAt(arguments, m.path)
		if err != nil {
			return err
		}
		vs := h.Values(name)
		switch {
		case !present && len(vs) > 0:
			return HeaderMismatch("the " + name + " header is sent, but the arguments hold no value it mirrors")
		case !present:
			continue
		case len(vs) == 0:
			return HeaderMismatch("an argument the tool mirrors in an " + HeaderParamPrefix + "* header is sent without that header")
		case len(vs) > 1:
			return HeaderMismatch("the " + name + " header was sent more than once")
		}
		got, perr := headerValue(name, vs[0])
		if perr != nil {
			return perr
		}
		if !sameValue(got, value) {
			return HeaderMismatch("the " + name + " header does not match the arguments")
		}
	}
	return nil
}

// valueAt returns the value at path in the arguments. present is false when a
// level is missing or is not an object, or when the value is null (the spec:
// a null parameter's header is omitted, and the server must not expect it).
func valueAt(arguments json.RawMessage, path []string) (json.RawMessage, bool, *Error) {
	cur := arguments
	for _, key := range path {
		if len(cur) == 0 {
			return nil, false, nil
		}
		ms, err := objectMembers(cur)
		if err != nil {
			return nil, false, nil // not an object: nothing at the path
		}
		if perr := oneSpelling(ms, "the arguments", key); perr != nil {
			return nil, false, perr
		}
		next, ok := lookupMember(ms, key)
		if !ok {
			return nil, false, nil
		}
		cur = next
	}
	if len(cur) == 0 || isNull(cur) {
		return nil, false, nil
	}
	return cur, true, nil
}

// sameValue reports whether a decoded header value mirrors a body value: a
// string as itself; a number numerically, so 42 and 42.0 are one value (the
// spec's SHOULD), from a header that is itself a JSON number; true and false
// as those words; anything else as its compact JSON text.
func sameValue(header string, body json.RawMessage) bool {
	body = bytes.TrimSpace(body)
	var s string
	switch {
	case len(body) > 0 && body[0] == '"':
		return json.Unmarshal(body, &s) == nil && s == header
	case len(body) > 0 && (body[0] == '-' || (body[0] >= '0' && body[0] <= '9')):
		hk, ok1 := numberKey(header)
		bk, ok2 := numberKey(string(body))
		return ok1 && ok2 && hk == bk
	}
	var buf bytes.Buffer
	if json.Compact(&buf, body) != nil {
		return false
	}
	return buf.String() == header
}

// jsonNumber is the JSON number grammar (RFC 8259 §6). A header must match it
// before it is compared as a number, so forms other parsers read differently
// (0x2A, 1_000, Infinity) are refused, never equated.
var jsonNumber = regexp.MustCompile(`^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$`)

// maxExponent bounds a number's exponent, so the comparison stays exact and
// cheap: a mirrored parameter is an integer within JavaScript's safe range.
const maxExponent = 1 << 20

// numberKey returns a JSON number's value as a canonical text, digits without
// leading or trailing zeros and a decimal exponent, so 42, 42.0, 4.2e1 and
// 420e-1 give one key and different values never do. ok is false for text
// that is not a JSON number, or whose exponent is out of bounds.
func numberKey(s string) (string, bool) {
	if !jsonNumber.MatchString(s) {
		return "", false
	}
	neg := strings.HasPrefix(s, "-")
	s = strings.TrimPrefix(s, "-")
	mant, exp := s, "0"
	if i := strings.IndexAny(s, "eE"); i >= 0 {
		mant, exp = s[:i], s[i+1:]
	}
	e, err := strconv.Atoi(exp)
	if err != nil || e > maxExponent || e < -maxExponent {
		return "", false
	}
	whole, frac, _ := strings.Cut(mant, ".")
	digits := strings.TrimLeft(whole+frac, "0")
	e -= len(frac)
	if digits == "" {
		return "0", true // zero, -0 included
	}
	trimmed := strings.TrimRight(digits, "0")
	e += len(digits) - len(trimmed)
	key := trimmed + "e" + strconv.Itoa(e)
	if neg {
		key = "-" + key
	}
	return key, true
}
