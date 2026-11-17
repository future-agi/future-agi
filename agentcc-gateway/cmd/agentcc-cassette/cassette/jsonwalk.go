package cassette

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strconv"
)

// jsonLeaf is one member name, string or number met while walking a JSON
// document.
type jsonLeaf struct {
	key    string      // the nearest member name ("" at the top); array elements inherit their array's
	name   bool        // the leaf is a member name itself (str holds it), not a value
	secret bool        // a value under a credential member, at any depth
	path   string      // where it is, for findings: $.a.b[2]; a hidden name shows as <member N>
	str    *string     // the decoded string, for a member name or a string
	num    json.Number // the literal, for a number
}

// walkOptions configure walkJSON.
type walkOptions struct {
	// secret reports a member whose value (everything under it) is a
	// credential; oauthObject says the member's object also holds an OAuth
	// companion member. nil: no member is.
	secret func(name string, oauthObject bool) bool
	// hide reports a member name a path must not show, given the path of
	// the object it is in. nil: all show.
	hide func(name, parent string) bool
	// schema reports the path of an object that is a JSON Schema, read as
	// structure (schema.go). nil: none is.
	schema func(path string) bool
	// root is the document's own path; "$" when empty.
	root string
}

// walkFrame is one open object or array.
type walkFrame struct {
	isObj   bool
	wantKey bool   // object: the next string is a member name
	key     string // object: the current member's name; array: the key it sits under
	keyPath string // object: the current member's path
	secret  bool
	oauth   bool // object: holds an OAuth companion member
	path    string
	index   int        // array: the current element; object: the members read so far
	role    schemaRole // what it is in a JSON Schema, if it lies in one
	cred    bool       // it lies in the schema of a credential
}

// walkJSON visits every member name, string and number of data in document
// order. leaf returns a replacement JSON literal for the leaf, or nil to
// keep its original bytes. Everything else (punctuation, whitespace, escapes
// in leaves left alone) is copied byte for byte, so member order and
// formatting survive scrubbing. It returns data itself when nothing changed.
func walkJSON(data []byte, o walkOptions, leaf func(jsonLeaf) []byte) ([]byte, error) {
	if !json.Valid(data) {
		return nil, errors.New("invalid JSON")
	}
	var oauthObjects map[int]bool
	if o.secret != nil {
		var err error
		if oauthObjects, err = findOAuthObjects(data); err != nil {
			return nil, err
		}
	}
	if o.root == "" {
		o.root = "$"
	}
	var out bytes.Buffer
	copied, changed, objects := 0, false, 0
	var stack []*walkFrame
	replace := func(start, end int, repl []byte) {
		if repl != nil {
			out.Write(data[copied:start])
			out.Write(repl)
			copied, changed = end, true
		}
	}
	err := eachToken(data, func(tok json.Token, start, end int) {
		var top *walkFrame
		if len(stack) > 0 {
			top = stack[len(stack)-1]
		}
		if s, ok := tok.(string); ok && top != nil && top.isObj && top.wantKey {
			top.index++
			top.key, top.wantKey = s, false
			top.keyPath = top.path + o.memberPath(s, top.path, top.index)
			replace(start, end, leaf(jsonLeaf{key: s, name: true, path: top.keyPath, str: &s}))
			return
		}
		key, secret, path := o.leafContext(top)
		role, cred := notSchema, false
		if d, isDelim := tok.(json.Delim); !isDelim || d == '{' || d == '[' {
			if !isDelim {
				d = 0
			}
			if r, c, sec, ok := o.inSchema(top, path, d); ok {
				role, cred, secret = r, c, sec
			}
		}
		switch v := tok.(type) {
		case json.Delim:
			if v == '{' || v == '[' {
				f := &walkFrame{isObj: v == '{', wantKey: v == '{', key: key, secret: secret, path: path, role: role, cred: cred}
				if v == '{' {
					f.oauth = oauthObjects[objects]
					objects++
				}
				stack = append(stack, f)
				return
			}
			stack = stack[:len(stack)-1]
		case string:
			replace(start, end, leaf(jsonLeaf{key: key, secret: secret, path: path, str: &v}))
		case json.Number:
			replace(start, end, leaf(jsonLeaf{key: key, secret: secret, path: path, num: v}))
		}
		if len(stack) > 0 {
			if parent := stack[len(stack)-1]; parent.isObj {
				parent.wantKey = true
			} else {
				parent.index++
			}
		}
	})
	if err != nil {
		return nil, err
	}
	if !changed {
		return data, nil
	}
	out.Write(data[copied:])
	return out.Bytes(), nil
}

// eachToken calls each with every token of the one JSON value in data and
// the byte range it spans (from its first byte, past any separator, to its
// end).
func eachToken(data []byte, each func(tok json.Token, start, end int)) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	depth, done := 0, false
	for {
		before := int(dec.InputOffset())
		tok, err := dec.Token()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if done {
			return errors.New("more than one JSON value")
		}
		after := int(dec.InputOffset())
		start := before
		for start < after && isJSONSpaceOrSep(data[start]) {
			start++
		}
		each(tok, start, after)
		if d, ok := tok.(json.Delim); ok {
			if d == '{' || d == '[' {
				depth++
			} else {
				depth--
			}
		}
		done = depth == 0
	}
}

// findOAuthObjects numbers data's objects in document order (by their
// opening brace) and reports those holding an OAuth companion member. A
// streaming walk meets code before a grant_type that follows it, so the
// walk asks this first.
func findOAuthObjects(data []byte) (map[int]bool, error) {
	type obj struct {
		n                         int
		isObj, wantKey, companion bool
	}
	found := map[int]bool{}
	var stack []*obj
	objects := 0
	err := eachToken(data, func(tok json.Token, _, _ int) {
		var top *obj
		if len(stack) > 0 {
			top = stack[len(stack)-1]
		}
		if s, ok := tok.(string); ok && top != nil && top.isObj && top.wantKey {
			top.wantKey = false
			top.companion = top.companion || oauthJSONCompanions[normName(s)]
			return
		}
		if d, ok := tok.(json.Delim); ok {
			if d == '{' || d == '[' {
				o := &obj{isObj: d == '{', wantKey: d == '{'}
				if d == '{' {
					o.n = objects
					objects++
				}
				stack = append(stack, o)
				return
			}
			if top.isObj && top.companion {
				found[top.n] = true
			}
			stack = stack[:len(stack)-1]
		}
		if len(stack) > 0 && stack[len(stack)-1].isObj {
			stack[len(stack)-1].wantKey = true
		}
	})
	return found, err
}

// leafContext is the key, secrecy and path of the value about to be read.
func (o walkOptions) leafContext(top *walkFrame) (string, bool, string) {
	switch {
	case top == nil:
		return "", false, o.root
	case top.isObj:
		return top.key, top.secret || o.secret != nil && o.secret(top.key, top.oauth), top.keyPath
	}
	return top.key, top.secret, fmt.Sprintf("%s[%d]", top.path, top.index)
}

// memberPath is .name for a plain name, ["quoted name"] for another, and
// .<member n> for a name the options hide.
func (o walkOptions) memberPath(key, parent string, n int) string {
	switch {
	case o.hide != nil && o.hide(key, parent):
		return fmt.Sprintf(".<member %d>", n)
	case plainKeyRE.MatchString(key):
		return "." + key
	}
	return "[" + strconv.Quote(key) + "]"
}

var plainKeyRE = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

func isJSONSpaceOrSep(b byte) bool {
	return b == ' ' || b == '\t' || b == '\n' || b == '\r' || b == ',' || b == ':'
}

// jsonString encodes s as a JSON string literal without HTML escaping, so
// <, > and & in vendor text stay as they are.
func jsonString(s string) []byte {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(s)
	return bytes.TrimRight(buf.Bytes(), "\n")
}
