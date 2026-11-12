package protocol

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"unicode"
)

// Reading a request body the way every JSON parser reads it.
//
// A mirrored header is only worth checking against the value that runs. Go's
// encoding/json, which the method handlers decode params with, matches a
// member name exactly first and else case-insensitively, and keeps the last
// match, so {"name":"a","NAME":"b"} names tool b to the handlers and tool a
// to an exact-name reader. Other parsers keep the first of two exact
// duplicates, and the case-insensitive ones fold letters differently. The
// header check therefore reads values with the handlers' rule, and before
// that refuses any body where the rules can disagree about a member a header
// mirrors: such a member must be sent once, spelled exactly. A client that
// follows the spec does both.

// errNotObject is a JSON value that is not an object where one is required.
var errNotObject = errors.New("not a JSON object")

// member is one member of a JSON object, in document order.
type member struct {
	name  string // decoded as encoding/json decodes it
	value json.RawMessage
}

// objectMembers returns every member of a JSON object in document order,
// each duplicate included. It fails on a value that is not an object.
func objectMembers(raw json.RawMessage) ([]member, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	if d, ok := tok.(json.Delim); !ok || d != '{' {
		return nil, errNotObject
	}
	var out []member
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return nil, err
		}
		name, _ := tok.(string)
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			return nil, err
		}
		out = append(out, member{name: name, value: value})
	}
	return out, nil
}

// foldName is a member name as a case-insensitive parser compares it, the
// widest such rule: Go's encoding/json (Unicode simple folding, under which
// k, K and the Kelvin sign are one letter, as are s, S and ſ), widened by
// the dotted and dotless i that .NET's and Java's upper-casing read as I.
func foldName(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		if r == 'ı' || r == 'İ' {
			r = 'i'
		}
		b.WriteRune(foldRune(r))
	}
	return b.String()
}

// foldRune is the smallest rune of r's simple fold set, as encoding/json
// folds a name.
func foldRune(r rune) rune {
	for {
		r2 := unicode.SimpleFold(r)
		if r2 <= r {
			return r2
		}
		r = r2
	}
}

// oneSpelling refuses an object in which a member a header mirrors (one of
// names) can be read two ways: sent more than once, exact duplicates
// included, or spelled with other letter case. where names the object in the
// message. It names only what the client sent.
func oneSpelling(ms []member, where string, names ...string) *Error {
	for _, want := range names {
		folded := foldName(want)
		seen := 0
		for _, m := range ms {
			if foldName(m.name) != folded {
				continue
			}
			if m.name != want {
				return HeaderMismatch(fmt.Sprintf("%s carries %s, a header-mirrored member spelled with other letter case; send it spelled exactly",
					where, strconvQuote(m.name)))
			}
			if seen++; seen > 1 {
				return HeaderMismatch(fmt.Sprintf("%s carries %s more than once", where, strconvQuote(m.name)))
			}
		}
	}
	return nil
}

// RepeatsMirrored reports whether the first member that read sends twice
// under one decoded name is one a header of a 2026-07-28 request for method
// mirrors. read is the start of a request body that was refused at that
// member (the content bounds refuse a repeated name), up to and past it. The
// body was never read whole, so method is the one its Mcp-Method header names.
//
// The members a header mirrors are the ones CheckHeaders reads: method
// (Mcp-Method); params, and within it _meta and _meta's protocolVersion
// (MCP-Protocol-Version); name on tools/call and prompts/get, uri on
// resources/read (Mcp-Name); and on tools/call arguments and every member
// within it reached through objects only (Mcp-Param-*). Which arguments a
// tool mirrors is in its inputSchema, resolved through the caller's view of a
// request the gateway read (SchemaLookup), so for a body it could not read
// every such member counts. A marked parameter is never inside an array:
// markedParams follows properties only.
func RepeatsMirrored(read []byte, method string) bool {
	// frame is one open object or array: an object's keys are the member
	// names read so far, key the one whose value is being read, and wantKey
	// whether its next token is a member name or its end.
	type frame struct {
		object, wantKey bool
		key             string
		keys            map[string]bool
	}
	var stack []*frame
	dec := json.NewDecoder(bytes.NewReader(read))
	// A number the bounds accept need not fit a float64.
	dec.UseNumber()
	for {
		tok, err := dec.Token()
		if err != nil {
			return false
		}
		var top *frame
		if len(stack) > 0 {
			top = stack[len(stack)-1]
		}
		delim, isDelim := tok.(json.Delim)
		if top != nil && top.object && top.wantKey {
			if isDelim {
				stack = stack[:len(stack)-1]
				continue
			}
			name, _ := tok.(string)
			if top.keys[name] {
				path := make([]string, 0, len(stack))
				for _, f := range stack[:len(stack)-1] {
					if !f.object {
						return false
					}
					path = append(path, f.key)
				}
				return mirroredMember(append(path, name), method)
			}
			top.keys[name] = true
			top.key, top.wantKey = name, false
			continue
		}
		// tok is a value, or the end of an array. A value ends its member, so
		// the object holding it reads a name next.
		if top != nil && top.object {
			top.wantKey = true
		}
		switch {
		case isDelim && delim == '{':
			stack = append(stack, &frame{object: true, wantKey: true, keys: map[string]bool{}})
		case isDelim && delim == '[':
			stack = append(stack, &frame{})
		case isDelim && delim == ']':
			stack = stack[:len(stack)-1]
		}
	}
}

// mirroredMember reports whether a header of a request for method mirrors
// the member at path, the member names that lead to it from the root.
func mirroredMember(path []string, method string) bool {
	if len(path) == 1 {
		return path[0] == "method" || path[0] == "params"
	}
	if path[0] != "params" {
		return false
	}
	switch path[1] {
	case "_meta":
		return len(path) == 2 || (len(path) == 3 && path[2] == MetaProtocolVersion)
	case "arguments":
		return method == "tools/call"
	}
	member, named := namedMethods[method]
	return named && len(path) == 2 && path[1] == member
}

// lookupMember returns the value of the member spelled exactly name.
func lookupMember(ms []member, name string) (json.RawMessage, bool) {
	for _, m := range ms {
		if m.name == name {
			return m.value, true
		}
	}
	return nil, false
}

// isNull reports whether a JSON value is null.
func isNull(raw json.RawMessage) bool {
	return string(bytes.TrimSpace(raw)) == "null"
}
