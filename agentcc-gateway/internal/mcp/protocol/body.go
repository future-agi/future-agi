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
