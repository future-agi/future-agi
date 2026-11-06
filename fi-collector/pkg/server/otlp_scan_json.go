package server

import (
	"bytes"
	"unicode/utf16"
	"unicode/utf8"
)

// scanOTLPJSON measures an OTLP/JSON ExportTraceServiceRequest the way
// scanOTLPProto measures protobuf. It follows pdata v1.20.0's JSON decoder:
// keys in either spelling (resourceSpans or resource_spans), matched after
// unescaping as jsoniter does, every array entry decoded (null included),
// repeated keys appended, unknown keys skipped. String lengths are those of
// the decoded strings. Anything malformed that the scan can see past is left
// to the decoder, which refuses it.
func scanOTLPJSON(b []byte, stamps ...resourceStampBudget) (otlpShape, error) {
	j := jsonScan{b: b}
	if len(stamps) != 0 {
		j.s.stamps = stamps[0]
	}
	err := j.request()
	return j.s, err
}

type jsonScan struct {
	b     []byte
	i     int
	depth int
	s     otlpShape
	// key is the last string read with capture, unescaped, when it fits;
	// keyN is its length, or -1 when it did not fit (no key we match is
	// longer than the buffer).
	key  [32]byte
	keyN int
}

func (j *jsonScan) ws() {
	for j.i < len(j.b) {
		switch j.b[j.i] {
		case ' ', '\t', '\n', '\r':
			j.i++
		default:
			return
		}
	}
}

func (j *jsonScan) peek() byte {
	j.ws()
	if j.i >= len(j.b) {
		return 0
	}
	return j.b[j.i]
}

// open consumes the bracket c that opens an object or array, or a null in
// its place (isNull).
func (j *jsonScan) open(c byte) (isNull bool, err error) {
	switch j.peek() {
	case c:
		j.i++
		j.depth++
		if j.depth > maxJSONNesting {
			return false, errJSONTooDeep
		}
		j.s.depth = max(j.s.depth, int64(j.depth))
		return false, nil
	case 'n':
		j.skipScalar()
		return true, nil
	}
	return false, errMalformed("json: expected %q at offset %d", c, j.i)
}

// next moves to the next entry of the object or array being walked: it
// consumes the comma before it, or the closing bracket (more is false).
func (j *jsonScan) next(closing byte, first *bool) (more bool, err error) {
	c := j.peek()
	if c == closing {
		j.i++
		j.depth--
		return false, nil
	}
	if !*first {
		if c != ',' {
			return false, errMalformed("json: expected ',' or %q at offset %d", closing, j.i)
		}
		j.i++
	}
	*first = false
	return true, nil
}

// field reads the next key of an object and the colon after it. The key is
// nil when it is longer than any key the scan matches.
func (j *jsonScan) field() ([]byte, error) {
	info, err := j.str(true)
	if err != nil {
		return nil, err
	}
	j.s.transientBytes += jsonDropped * info.sent
	if j.peek() != ':' {
		return nil, errMalformed("json: expected ':' at offset %d", j.i)
	}
	j.i++
	if j.keyN < 0 {
		return nil, nil
	}
	return j.key[:j.keyN], nil
}

// strInfo is one string: its decoded length and its escaped JSON length.
type strInfo struct {
	decoded, plain, html int64
	sent                 int64 // its length as sent, between the quotes
	// noNonZero is true when the decoded string is empty or contains only
	// zero digits, including escaped zeros: the decoder treats it as a root.
	noNonZero bool
}

// jsonDropped bounds, per byte of a string as sent (between its quotes),
// what pdata's JSON decoder allocates and drops for an object member name
// (known or not, at any depth) and for a string with escapes (unescaped
// through a buffer that grows by appending). TestPriceCoversJSONDecodeAllocations
// measures it.
const jsonDropped = 2

func (j *jsonScan) str(capture bool) (strInfo, error) {
	info := strInfo{noNonZero: true}
	if j.peek() == 'n' && bytes.HasPrefix(j.b[j.i:], []byte("null")) {
		// jsoniter's ReadString reads null as "", so pdata accepts null
		// wherever it reads a string.
		j.i += 4
		j.keyN = 0
		return info, nil
	}
	if j.peek() != '"' {
		return info, errMalformed("json: expected a string at offset %d", j.i)
	}
	j.i++
	j.keyN = 0
	start, fits, escaped := j.i, capture, false
	keep := func(p []byte) {
		if fits && j.keyN+len(p) <= len(j.key) {
			j.keyN += copy(j.key[j.keyN:], p)
		} else {
			fits = false
		}
	}
	for {
		if j.i >= len(j.b) {
			return info, errMalformed("json: unterminated string")
		}
		c := j.b[j.i]
		switch {
		case c == '"':
			j.i++
			if !fits {
				j.keyN = -1
			}
			info.sent = int64(j.i - 1 - start)
			if escaped {
				j.s.transientBytes += jsonDropped * info.sent
			}
			return info, nil
		case c == '\\':
			escaped = true
			runes, err := j.escape()
			if err != nil {
				return info, err
			}
			for _, r := range runes {
				if r < 0 {
					continue
				}
				if r != '0' {
					info.noNonZero = false
				}
				var buf [utf8.UTFMax]byte
				n := utf8.EncodeRune(buf[:], r)
				info.decoded += int64(n)
				if r < utf8.RuneSelf {
					p, h := asciiEscapedLen(byte(r))
					info.plain += p
					info.html += h
				} else {
					e := runeEscapedLen(r, n)
					info.plain += e
					info.html += e
				}
				keep(buf[:n])
			}
		case c < utf8.RuneSelf:
			if c != '0' {
				info.noNonZero = false
			}
			j.i++
			info.decoded++
			p, h := asciiEscapedLen(c)
			info.plain += p
			info.html += h
			keep(j.b[j.i-1 : j.i])
		default:
			info.noNonZero = false
			r, size := utf8.DecodeRune(j.b[j.i:])
			j.i += size
			info.decoded += int64(size)
			e := runeEscapedLen(r, size)
			info.plain += e
			info.html += e
			keep(j.b[j.i-size : j.i])
		}
	}
}

// escape decodes the escape at j.i (a backslash) as jsoniter does, and
// returns the runes it stands for (unused slots are -1). An unpaired
// surrogate decodes to U+FFFD.
func (j *jsonScan) escape() ([2]rune, error) {
	out := [2]rune{-1, -1}
	if j.i+1 >= len(j.b) {
		return out, errMalformed("json: unterminated escape")
	}
	c := j.b[j.i+1]
	j.i += 2
	switch c {
	case '"', '\\', '/':
		out[0] = rune(c)
	case 'b':
		out[0] = '\b'
	case 'f':
		out[0] = '\f'
	case 'n':
		out[0] = '\n'
	case 'r':
		out[0] = '\r'
	case 't':
		out[0] = '\t'
	case 'u':
		r, ok := j.hex4()
		if !ok {
			return out, errMalformed("json: bad \\u escape")
		}
		if !utf16.IsSurrogate(r) {
			out[0] = r
			return out, nil
		}
		if j.i+1 < len(j.b) && j.b[j.i] == '\\' && j.b[j.i+1] == 'u' {
			save := j.i
			j.i += 2
			r2, ok := j.hex4()
			if !ok {
				j.i = save
				out[0] = utf8.RuneError
				return out, nil
			}
			if comb := utf16.DecodeRune(r, r2); comb != utf8.RuneError {
				out[0] = comb
			} else {
				out[0], out[1] = utf8.RuneError, utf8.RuneError
			}
			return out, nil
		}
		out[0] = utf8.RuneError
	default:
		return out, errMalformed("json: bad escape")
	}
	return out, nil
}

func (j *jsonScan) hex4() (rune, bool) {
	if j.i+4 > len(j.b) {
		return 0, false
	}
	var r rune
	for _, h := range j.b[j.i : j.i+4] {
		r <<= 4
		switch {
		case h >= '0' && h <= '9':
			r |= rune(h - '0')
		case h >= 'a' && h <= 'f':
			r |= rune(h-'a') + 10
		case h >= 'A' && h <= 'F':
			r |= rune(h-'A') + 10
		default:
			return 0, false
		}
	}
	j.i += 4
	return r, true
}

// skipScalar skips a number, true, false or null.
func (j *jsonScan) skipScalar() {
	start := j.i
	for j.i < len(j.b) {
		switch j.b[j.i] {
		case ',', '}', ']', ' ', '\t', '\n', '\r':
			if j.i == start {
				j.i++ // never stall on a stray delimiter
			}
			return
		}
		j.i++
	}
}

// skip skips any value, as jsoniter skips an unknown key's value. Strings in
// it are not counted: the decoder does not keep them.
func (j *jsonScan) skip() error {
	switch j.peek() {
	case '"':
		_, err := j.str(false)
		return err
	case '{':
		_, _ = j.open('{')
		if j.depth > maxJSONNesting {
			return errJSONTooDeep
		}
		for first := true; ; {
			more, err := j.next('}', &first)
			if err != nil || !more {
				return err
			}
			if _, err := j.field(); err != nil {
				return err
			}
			if err := j.skip(); err != nil {
				return err
			}
		}
	case '[':
		_, _ = j.open('[')
		if j.depth > maxJSONNesting {
			return errJSONTooDeep
		}
		for first := true; ; {
			more, err := j.next(']', &first)
			if err != nil || !more {
				return err
			}
			if err := j.skip(); err != nil {
				return err
			}
		}
	case 0:
		return errMalformed("json: unexpected end")
	}
	j.skipScalar()
	return nil
}

// string reads a string value the decoder keeps.
func (j *jsonScan) string() (strInfo, error) {
	info, err := j.str(false)
	j.s.strings++
	j.s.stringBytes += info.decoded
	return info, err
}

// array walks an array (or null), calling elem for each entry.
func (j *jsonScan) array(elem func() error) error {
	isNull, err := j.open('[')
	if err != nil || isNull {
		return err
	}
	for first := true; ; {
		more, err := j.next(']', &first)
		if err != nil || !more {
			return err
		}
		if err := elem(); err != nil {
			return err
		}
	}
}

// object walks an object (or null), calling member for each key.
func (j *jsonScan) object(member func(key []byte) error) error {
	isNull, err := j.open('{')
	if err != nil || isNull {
		return err
	}
	for first := true; ; {
		more, err := j.next('}', &first)
		if err != nil || !more {
			return err
		}
		key, err := j.field()
		if err != nil {
			return err
		}
		if err := member(key); err != nil {
			return err
		}
	}
}

func (j *jsonScan) request() error {
	return j.object(func(key []byte) error {
		switch string(key) {
		case "resourceSpans", "resource_spans":
			return j.array(j.resourceSpans)
		}
		return j.skip()
	})
}

func (j *jsonScan) resourceSpans() error {
	j.s.resourceSpans++
	if j.s.resourceSpans > maxResourceSpansPerRequest {
		return errTooManyResourceSpans
	}
	var rs rsAccum
	err := j.object(func(key []byte) error {
		switch string(key) {
		case "resource":
			return j.object(func(key []byte) error {
				if string(key) == "attributes" {
					return j.keyValues(kvResource, &rs, nil, 0)
				}
				return j.skip()
			})
		case "scopeSpans", "scope_spans":
			return j.array(func() error { return j.scopeSpans(&rs) })
		case "schemaUrl", "schema_url":
			_, err := j.string()
			return err
		}
		return j.skip()
	})
	if err != nil {
		return err
	}
	return j.s.endResourceSpans(&rs)
}

func (j *jsonScan) scopeSpans(rs *rsAccum) error {
	j.s.scopeSpans++
	return j.object(func(key []byte) error {
		switch string(key) {
		case "scope":
			return j.object(func(key []byte) error {
				switch string(key) {
				case "name", "version":
					_, err := j.string()
					return err
				case "attributes":
					return j.keyValues(kvOther, rs, nil, 0)
				}
				return j.skip()
			})
		case "spans":
			return j.array(func() error { return j.span(rs) })
		case "schemaUrl", "schema_url":
			_, err := j.string()
			return err
		}
		return j.skip()
	})
}

func (j *jsonScan) span(rs *rsAccum) error {
	j.s.spans++
	if j.s.spans > maxSpansPerRequest {
		return errTooManySpans
	}
	sp := spanAccum{root: true}
	err := j.object(func(key []byte) error {
		switch string(key) {
		case "parentSpanId", "parent_span_id":
			info, err := j.str(false)
			sp.root = info.noNonZero
			return err
		case "traceState", "trace_state":
			_, err := j.string()
			return err
		case "name":
			info, err := j.string()
			sp.nameJSON = info.plain + 2
			sp.json += info.plain + 2
			return err
		case "attributes":
			return j.keyValues(kvSpan, rs, &sp, 0)
		case "events":
			return j.array(func() error { return j.event(&sp) })
		case "links":
			return j.array(j.link)
		case "status":
			return j.object(func(key []byte) error {
				if string(key) == "message" {
					info, err := j.string()
					sp.json += info.plain + 2
					return err
				}
				return j.skip()
			})
		}
		return j.skip()
	})
	if err != nil {
		return err
	}
	j.s.endSpan(rs, &sp)
	return nil
}

func (j *jsonScan) event(sp *spanAccum) error {
	j.s.events++
	var nameLen int64
	err := j.object(func(key []byte) error {
		switch string(key) {
		case "name":
			info, err := j.string()
			nameLen += info.decoded
			return err
		case "attributes":
			return j.keyValues(kvEvent, nil, sp, 0)
		}
		return j.skip()
	})
	sp.json += spanEventJSON(nameLen)
	j.s.hotCopyBytes += spanEventJSON(nameLen)
	return err
}

func (j *jsonScan) link() error {
	j.s.links++
	return j.object(func(key []byte) error {
		switch string(key) {
		case "traceState", "trace_state":
			_, err := j.string()
			return err
		case "attributes":
			return j.keyValues(kvOther, nil, nil, 0)
		}
		return j.skip()
	})
}

// keyValues walks an array of KeyValues in the given context.
func (j *jsonScan) keyValues(ctx kvContext, rs *rsAccum, sp *spanAccum, depth int) error {
	return j.array(func() error {
		kv, err := j.keyValue(depth)
		if err == nil {
			j.s.addKV(ctx, kv, rs, sp)
		}
		return err
	})
}

func (j *jsonScan) keyValue(depth int) (kvInfo, error) {
	var kv kvInfo
	if err := j.s.addKeyValue(); err != nil {
		return kv, err
	}
	err := j.object(func(key []byte) error {
		switch string(key) {
		case "key":
			info, err := j.str(true)
			if err != nil {
				return err
			}
			j.s.strings++
			j.s.stringBytes += info.decoded
			kv.keyJSON, kv.keyHTML, kv.keyRaw = info.plain, info.html, info.decoded
			kv.promoted, kv.stampKey = 0, 0
			if j.keyN >= 0 {
				kv.promoted = promotedStringKey(j.key[:j.keyN])
				kv.stampKey = resourceStampKey(j.key[:j.keyN])
				if c := classifyKey(j.key[:j.keyN]); c != keyPlain {
					kv.class = c
				}
			}
			return nil
		case "value":
			v, err := j.anyValue(depth)
			kv.val = addValues(kv.val, v)
			return err
		}
		return j.skip()
	})
	if kv.val == (valueInfo{}) {
		kv.val = valueInfo{json: 4, htmlJSON: 4, scalar: true}
	}
	return kv, err
}

func (j *jsonScan) anyValue(depth int) (valueInfo, error) {
	v := valueInfo{json: 4, htmlJSON: 4, scalar: true}
	set := false
	add := func(one valueInfo) {
		one.lastStringJSON = -1
		if one.isString {
			one.lastStringJSON = one.json
		}
		if !set {
			v, set = one, true
		} else {
			v = addValues(v, one)
		}
	}
	err := j.object(func(key []byte) error {
		switch string(key) {
		case "stringValue", "string_value":
			info, err := j.string()
			add(valueInfo{json: info.plain + 2, htmlJSON: info.html + 2, raw: info.decoded, scalar: true, isString: true})
			return err
		case "bytesValue", "bytes_value":
			info, err := j.str(false) // base64: decodes to fewer bytes
			j.s.bytesValues += info.decoded
			add(valueInfo{json: info.decoded + 4, htmlJSON: info.decoded + 4, raw: info.decoded})
			return err
		case "boolValue", "bool_value", "intValue", "int_value", "doubleValue", "double_value":
			add(valueInfo{json: numberJSON, htmlJSON: numberJSON, scalar: true})
			return j.skip()
		case "arrayValue", "array_value":
			if depth >= maxValueNesting {
				return errValueTooDeep
			}
			a := valueInfo{json: 2, htmlJSON: 2}
			j.s.containers++
			err := j.object(func(key []byte) error {
				if string(key) != "values" {
					return j.skip()
				}
				return j.array(func() error {
					if err := j.s.addArrayValue(); err != nil {
						return err
					}
					e, err := j.anyValue(depth + 1)
					a.json += e.json + 1
					a.htmlJSON += e.htmlJSON + 1
					a.raw += e.raw
					return err
				})
			})
			add(a)
			return err
		case "kvlistValue", "kvlist_value":
			if depth >= maxValueNesting {
				return errValueTooDeep
			}
			a := valueInfo{json: 2, htmlJSON: 2}
			j.s.containers++
			err := j.object(func(key []byte) error {
				if string(key) != "values" {
					return j.skip()
				}
				return j.array(func() error {
					kv, err := j.keyValue(depth + 1)
					a.json += kv.json()
					a.htmlJSON += kv.htmlJSON()
					a.raw += kv.keyRaw + kv.val.raw
					return err
				})
			})
			add(a)
			return err
		}
		return j.skip()
	})
	return v, err
}
