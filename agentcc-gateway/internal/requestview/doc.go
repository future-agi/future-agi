package requestview

import (
	"bytes"
	"context"
	"encoding/json"
	"math"
	"sort"
	"unicode/utf8"
)

// MaxDepth is how deep the readers read a body: an object or a list inside
// MaxDepth others is not read. Where a reader walks a value on its own (an
// item, part or block of a type it does not know, a Gemini function
// response, a block in a block), what it holds deeper is not read, and the
// view says so (ChatRequest's reason): a check that would block or rewrite
// the request refuses it (request_unavailable), and a check that only logs
// reads the rest. It is the depth the checks read a tool call's arguments to
// (guardrails.MaxArgumentsDepth).
const MaxDepth = 64

// doc is one read of a body. Before the readers decode anything, one pass
// over the body finds where each of its objects and lists ends (index), so
// a reader decodes an object or a list by its own members only, jumping over
// each object or list it holds, and gets each member as a slice of the body,
// not a copy: each object and list is decoded once, and the whole read is
// linear in the body, however deeply it is nested.
//
// A doc also carries the request's context, which the read looks at as it
// goes (it stops when the context ends), and where the readers met a value
// nested more than MaxDepth deep.
type doc struct {
	body []byte
	// valid: the body is one JSON value (json.Valid) and the index covers
	// it. A doc of anything else decodes nothing, as json.Unmarshal would.
	valid  bool
	starts []int32 // where each object and list starts, in the body's order
	ends   []int32 // where each ends: just after its closing bracket
	depths []uint8 // how many objects and lists hold each (at most 255)

	ctx   context.Context
	steps int   // decodes since the read last looked at ctx
	err   error // ctx ended: the read stopped there

	// deep is where the readers met a value nested more than MaxDepth deep
	// ("" when they did not): the path of the value a reader walked, or
	// "the body".
	deep string

	// scanned is how many bytes the decoders stepped over: an object's or a
	// list's own bytes, not those of the objects and lists it holds, which
	// they jump over, once to count its members and once to decode them
	// (TestDoc_EachByteIsScannedOnce).
	scanned int
}

// ctxCheckEvery is how many decodes the read makes between two looks at
// its context, and indexChunk how many bytes the index pass reads.
const (
	ctxCheckEvery = 64
	indexChunk    = 1 << 20
)

// newDoc is a read of body under ctx: the body checked to be one JSON value
// and indexed, unless ctx has ended.
func newDoc(ctx context.Context, body []byte) *doc {
	d := &doc{body: body, ctx: ctx}
	if ctx != nil && ctx.Err() != nil {
		d.err = ctx.Err()
		return d
	}
	if len(body) >= math.MaxInt32 || !json.Valid(body) {
		return d
	}
	d.index()
	return d
}

// index finds, in one pass over the body, where each object and list
// starts and ends and how deep it is, after a pass that counts them, so
// each table is allocated once. The body is valid JSON: every string is
// closed and every bracket matched.
func (d *doc) index() {
	b := d.body
	n := 0
	for i := 0; i < len(b); i++ {
		switch b[i] {
		case '"':
			i = stringEnd(b, i) - 1
		case '{', '[':
			n++
		}
	}
	d.starts, d.ends, d.depths = make([]int32, 0, n), make([]int32, 0, n), make([]uint8, 0, n)
	var open []int32 // the objects and lists the pass is in, by number
	next := indexChunk
	for i := 0; i < len(b); i++ {
		if i >= next {
			next = i + indexChunk
			if d.ctx != nil && d.ctx.Err() != nil {
				d.err = d.ctx.Err()
				return
			}
		}
		switch b[i] {
		case '"':
			i = stringEnd(b, i) - 1
		case '{', '[':
			depth := len(open)
			if depth > math.MaxUint8 {
				depth = math.MaxUint8
			}
			d.starts = append(d.starts, int32(i))
			d.ends = append(d.ends, 0)
			d.depths = append(d.depths, uint8(depth))
			open = append(open, int32(len(d.starts)-1))
		case '}', ']':
			last := len(open) - 1
			d.ends[open[last]] = int32(i + 1)
			open = open[:last]
		}
	}
	d.valid = true
}

// stopped says whether the read has stopped because its context ended,
// looking at the context every ctxCheckEvery calls.
func (d *doc) stopped() bool {
	if d.err != nil {
		return true
	}
	if d.ctx == nil {
		return false
	}
	if d.steps++; d.steps < ctxCheckEvery {
		return false
	}
	d.steps = 0
	if err := d.ctx.Err(); err != nil {
		d.err = err
		return true
	}
	return false
}

// tooDeep records that the readers met a value nested more than MaxDepth
// deep in where, a value of the body ("" for the body itself).
func (d *doc) tooDeep(where path) {
	if d.deep != "" {
		return
	}
	d.deep = "the body"
	if len(where) > 0 {
		d.deep = where.String()
	}
}

// offset is where raw, a slice of the body, starts in it; ok is false when
// raw is not a slice of the body.
func (d *doc) offset(raw []byte) (int, bool) {
	if len(raw) == 0 || len(d.body) == 0 {
		return 0, false
	}
	off := cap(d.body) - cap(raw)
	if off < 0 || off+len(raw) > len(d.body) || &d.body[off] != &raw[0] {
		return 0, false
	}
	return off, true
}

// container is the number of the object or list that starts at off.
func (d *doc) container(off int) (int, bool) {
	n := sort.Search(len(d.starts), func(k int) bool { return int(d.starts[k]) >= off })
	return n, n < len(d.starts) && int(d.starts[n]) == off
}

// open is where the members of raw are, between start and end (its
// brackets left out), when raw, a value of the body (with white space
// around it), is an object (kind '{') or a list (kind '['). ok is false for
// any other value, after the context ended, and for one inside MaxDepth
// others, which is recorded (tooDeep).
func (d *doc) open(raw json.RawMessage, kind byte) (start, end int, ok bool) {
	if !d.valid || d.stopped() {
		return 0, 0, false
	}
	t := bytes.TrimSpace(raw)
	if len(t) == 0 || t[0] != kind {
		return 0, 0, false
	}
	off, ok := d.offset(t)
	if !ok {
		return 0, 0, false
	}
	n, ok := d.container(off)
	if !ok || int(d.ends[n]) != off+len(t) {
		return 0, 0, false
	}
	if d.depths[n] >= MaxDepth {
		d.tooDeep(nil)
		return 0, 0, false
	}
	return off + 1, off + len(t) - 1, true
}

// valueEnd is where the value that starts at i ends, at most limit: an
// object or a list by the index, a string at its closing quote, any other
// value at the first byte that cannot be in it.
func (d *doc) valueEnd(i, limit int) int {
	b := d.body
	switch b[i] {
	case '{', '[':
		if n, ok := d.container(i); ok {
			return int(d.ends[n])
		}
		return limit
	case '"':
		return stringEnd(b, i)
	}
	j := i + 1
	for j < limit {
		switch b[j] {
		case ',', '}', ']', ' ', '\t', '\n', '\r':
			return j
		}
		j++
	}
	return j
}

// each calls f with each member of the object or list between start and
// end: for an object its key's span, for a list none, and the value. It
// stops, false, when the context ends.
func (d *doc) each(start, end int, object bool, f func(keyStart, keyEnd, valueStart, valueEnd int) bool) bool {
	b := d.body
	scanned := end - start
	for i, n := start, 0; ; n++ {
		if n > 0 && n%(ctxCheckEvery*ctxCheckEvery) == 0 && d.stopped() {
			return false
		}
		for i < end && (b[i] == ' ' || b[i] == '\t' || b[i] == '\n' || b[i] == '\r' || b[i] == ',') {
			i++
		}
		if i >= end {
			break
		}
		keyStart, keyEnd := 0, 0
		if object {
			keyStart, keyEnd = i, stringEnd(b, i)
			i = keyEnd
			for i < end && b[i] != ':' {
				i++
			}
			i++
			for i < end && (b[i] == ' ' || b[i] == '\t' || b[i] == '\n' || b[i] == '\r') {
				i++
			}
		}
		ve := d.valueEnd(i, end)
		if b[i] == '{' || b[i] == '[' {
			scanned -= ve - i
		}
		if !f(keyStart, keyEnd, i, ve) {
			return false
		}
		i = ve
	}
	d.scanned += scanned
	return true
}

// count is how many members the object or list between start and end has.
func (d *doc) count(start, end int, object bool) int {
	n := 0
	d.each(start, end, object, func(int, int, int, int) bool {
		n++
		return true
	})
	return n
}

// members is raw's keys and values, in the body's order (a repeated key
// once for each time it is there), when raw is an object of the body.
func (d *doc) members(raw json.RawMessage) (keys []string, values []json.RawMessage, ok bool) {
	start, end, ok := d.open(raw, '{')
	if !ok {
		return nil, nil, false
	}
	n := d.count(start, end, true)
	keys, values = make([]string, 0, n), make([]json.RawMessage, 0, n)
	ok = d.each(start, end, true, func(ks, ke, vs, ve int) bool {
		key, ok := decodeString(d.body[ks:ke])
		if !ok {
			return false
		}
		keys = append(keys, key)
		values = append(values, d.body[vs:ve])
		return true
	})
	if !ok {
		return nil, nil, false
	}
	return keys, values, true
}

// object is raw's members by key, of a repeated key the last (as
// json.Unmarshal reads it), when raw is an object of the body. Each value
// is a slice of the body.
func (d *doc) object(raw json.RawMessage) (map[string]json.RawMessage, bool) {
	keys, values, ok := d.members(raw)
	if !ok {
		return nil, false
	}
	o := make(map[string]json.RawMessage, len(keys))
	for i, k := range keys {
		o[k] = values[i]
	}
	return o, true
}

// array is raw's elements, when raw is a list of the body. Each is a slice
// of the body.
func (d *doc) array(raw json.RawMessage) ([]json.RawMessage, bool) {
	start, end, ok := d.open(raw, '[')
	if !ok {
		return nil, false
	}
	elems := make([]json.RawMessage, 0, d.count(start, end, false))
	ok = d.each(start, end, false, func(_, _, vs, ve int) bool {
		elems = append(elems, d.body[vs:ve])
		return true
	})
	if !ok {
		return nil, false
	}
	return elems, true
}

// stringEnd is the end of the JSON string that starts at b[i] (its opening
// quote): the index after its closing quote, or len(b) when it has none.
func stringEnd(b []byte, i int) int {
	for k := i + 1; k < len(b); {
		j := bytes.IndexAny(b[k:], `"\`)
		if j < 0 {
			return len(b)
		}
		if b[k+j] == '"' {
			return k + j + 1
		}
		k += j + 2 // a backslash and the character it escapes
	}
	return len(b)
}

// decodeString is the text of raw, a JSON string with its quotes, as
// json.Unmarshal decodes it: what is between the quotes when that holds no
// escape, quote or control character and is valid UTF-8 (one copy), else
// json.Unmarshal's.
func decodeString(raw []byte) (string, bool) {
	if len(raw) < 2 || raw[0] != '"' || raw[len(raw)-1] != '"' {
		return "", false
	}
	inner := raw[1 : len(raw)-1]
	plain := true
	for _, c := range inner {
		if c == '\\' || c == '"' || c < 0x20 {
			plain = false
			break
		}
	}
	if plain && utf8.Valid(inner) {
		return string(inner), true
	}
	var s string
	if json.Unmarshal(raw, &s) != nil {
		return "", false
	}
	return s, true
}

// lastOf says, for the keys of an object, whether keys[i] is the last of its
// key: the one a parser that keeps the last of a repeated key reads, as Go's
// and the readers' do.
func lastOf(keys []string) func(i int) bool {
	if len(keys) <= 8 {
		return func(i int) bool {
			for _, k := range keys[i+1:] {
				if k == keys[i] {
					return false
				}
			}
			return true
		}
	}
	last := make(map[string]int, len(keys))
	for i, k := range keys {
		last[k] = i
	}
	return func(i int) bool { return last[keys[i]] == i }
}

// appendJSONString appends s to b as a JSON string, byte for byte as
// encoding/json writes one without HTML escaping (marshal): a quote, a
// backslash and a control character escaped, invalid UTF-8 as \ufffd, and
// U+2028 and U+2029 escaped. It writes into b, where marshal goes through a
// buffer of its own, so a long text is copied once
// (TestAppendJSONString_WritesAsMarshalDoes).
func appendJSONString(b []byte, s string) []byte {
	const hex = "0123456789abcdef"
	b = append(b, '"')
	start := 0
	for i := 0; i < len(s); {
		if c := s[i]; c < utf8.RuneSelf {
			if c >= 0x20 && c != '"' && c != '\\' {
				i++
				continue
			}
			b = append(b, s[start:i]...)
			switch c {
			case '\\', '"':
				b = append(b, '\\', c)
			case '\b':
				b = append(b, '\\', 'b')
			case '\f':
				b = append(b, '\\', 'f')
			case '\n':
				b = append(b, '\\', 'n')
			case '\r':
				b = append(b, '\\', 'r')
			case '\t':
				b = append(b, '\\', 't')
			default:
				b = append(b, '\\', 'u', '0', '0', hex[c>>4], hex[c&0xF])
			}
			i++
			start = i
			continue
		}
		r, size := utf8.DecodeRuneInString(s[i:])
		if r == utf8.RuneError && size == 1 {
			b = append(b, s[start:i]...)
			b = append(b, `\ufffd`...)
			i += size
			start = i
			continue
		}
		if r == '\u2028' || r == '\u2029' {
			b = append(b, s[start:i]...)
			b = append(b, '\\', 'u', '2', '0', '2', hex[r&0xF])
			i += size
			start = i
			continue
		}
		i += size
	}
	b = append(b, s[start:]...)
	return append(b, '"')
}

// marshalString is s as a JSON string (marshal), in one allocation of its
// size when it needs no escape.
func marshalString(s string) json.RawMessage {
	return appendJSONString(make([]byte, 0, len(s)+2), s)
}
