package server

import (
	"fmt"
	"unicode/utf8"

	"google.golang.org/protobuf/encoding/protowire"
)

// One pass over an OTLP trace request, before it is decoded, measures what
// decoding it, converting it to rows and writing those rows to ClickHouse will
// cost. Decoding allocates per element, not per byte: an empty span is 2 bytes
// of protobuf and over 4 KiB once converted, so a body's size says little
// about its cost. The pass allocates nothing. It follows the OTLP trace schema
// as pdata v1.20.0 decodes it, for protobuf (protowire) and for JSON (a byte
// scan that unescapes keys as jsoniter does), and it refuses requests past the
// limits below. Its counts are upper bounds: an element the decoder would
// drop or overwrite (an unknown field excepted, which the decoder skips
// without allocating) is still counted.
//
// What the counts are turned into, and why, is in otlp_price.go.

// Per-request limits, checked by the scan. A request past one of them is
// refused permanently (HTTP 413 or 400, gRPC INVALID_ARGUMENT): sending it
// again cannot succeed.
const (
	// maxValueNesting is the deepest an attribute value may nest arrays and
	// key-value lists. Trace attribute values are scalars or arrays of
	// scalars in practice; structured values (messages, tool calls) nest a few
	// levels. Decoding and converting recurse once per level, so a deep value
	// costs goroutine stack that its size does not show.
	maxValueNesting = 64
	// maxJSONNesting is the deepest JSON nesting accepted anywhere in an
	// OTLP/JSON body, unknown fields included: jsoniter skips an unknown
	// value recursively. An attribute value at maxValueNesting sits about 270
	// levels down.
	maxJSONNesting = 512
	// protoDepthPerValue is how many JSON nesting levels one protobuf value
	// level is counted as for its stack ({"arrayValue":{"values":[{...).
	protoDepthPerValue = 4
	// maxSpansPerRequest, maxResourceSpansPerRequest and
	// maxKeyValuesPerRequest bound the elements of one request. The OTel
	// Collector's batch processor sends 8,192 spans by default and SDKs 512.
	maxSpansPerRequest         = 65536
	maxResourceSpansPerRequest = 10000
	maxKeyValuesPerRequest     = 1 << 20
	// maxResourceAttrBytes bounds the keys and values of one ResourceSpans'
	// resource attributes. Every span's row repeats them.
	maxResourceAttrBytes = 64 << 10
)

// JSON bytes of one stored row, beyond what the scan measures for it: the
// column names and the fixed-format values (ids, times, numbers). A span with
// no attributes measures 868 bytes (TestRowJSONOverheadCoversAnEmptySpan).
const rowFixedJSON = 1024

// Bytes of one curated dimension row (end user, session or trace) beyond its
// strings.
const curatedFixedJSON = 512

// scanError is a request the scan refuses. Every refusal is permanent.
type scanError struct {
	reason   string // short label for metrics and logs
	tooLarge bool   // a limit on size or count (413), not a malformed or too-deep body (400)
	msg      string
}

func (e *scanError) Error() string { return e.msg }

func errMalformed(format string, args ...any) error {
	return &scanError{reason: "malformed", msg: fmt.Sprintf(format, args...)}
}

var (
	errValueTooDeep = &scanError{reason: "nesting",
		msg: fmt.Sprintf("attribute value nested deeper than %d levels", maxValueNesting)}
	errJSONTooDeep = &scanError{reason: "nesting",
		msg: fmt.Sprintf("JSON nested deeper than %d levels", maxJSONNesting)}
	errTooManySpans = &scanError{reason: "spans", tooLarge: true,
		msg: fmt.Sprintf("more than %d spans in one request", maxSpansPerRequest)}
	errTooManyResourceSpans = &scanError{reason: "resource_spans", tooLarge: true,
		msg: fmt.Sprintf("more than %d ResourceSpans in one request", maxResourceSpansPerRequest)}
	errTooManyKeyValues = &scanError{reason: "attributes", tooLarge: true,
		msg: fmt.Sprintf("more than %d attributes in one request", maxKeyValuesPerRequest)}
	errResourceTooLarge = &scanError{reason: "resource_attributes", tooLarge: true,
		msg: fmt.Sprintf("resource attributes larger than %d bytes", maxResourceAttrBytes)}
)

// otlpShape is what the scan measured. Element counts drive the heap price;
// the JSON byte counts drive the flush price and the limit on stored bytes.
type otlpShape struct {
	stamps                                          resourceStampBudget
	resourceSpans, scopeSpans, spans, events, links int64

	keyValues   int64 // every KeyValue: resource, scope, span, event, link and nested
	arrayValues int64 // AnyValues inside arrays
	containers  int64 // ArrayValue and KeyValueList messages
	stringBytes int64 // every string field, as decoded
	strings     int64 // how many there are
	bytesValues int64 // bytes values, which conversion copies
	// resourceSpansWithSpans counts the ResourceSpans that make rows; each
	// keeps a flattened copy of its resource attributes.
	resourceSpansWithSpans int64
	rootSpans              int64

	// rowJSONBytes is the JSON that the rows of this request write to
	// ClickHouse (JSONEachRow), and maxRowJSON the largest single row.
	rowJSONBytes, maxRowJSON int64
	// curatedJSONBytes is the JSON of the curated end user, session and trace
	// rows the request can produce.
	curatedJSONBytes int64
	// hotCopyBytes is the strings conversion builds rather than shares: the
	// hot-column copy of an input.value or output.value that is not a
	// string, and the span-events column.
	hotCopyBytes int64
	// depth is how deep decoding and converting the request recurse, in JSON
	// nesting levels (protoDepthPerValue for each protobuf value level),
	// for its goroutine stack (stackPrice).
	depth int64
	// transientBytes is what the JSON decoder allocates and drops while it
	// decodes (jsonDropped): for every object member name, known or not, and
	// every string with escapes. Priced in the handler part, as if it were
	// held until the handler returns.
	transientBytes int64
}

// addKeyValue and addArrayValue count one element against
// maxKeyValuesPerRequest, which bounds both.
func (s *otlpShape) addKeyValue() error {
	s.keyValues++
	return s.checkAttributes()
}

func (s *otlpShape) addArrayValue() error {
	s.arrayValues++
	return s.checkAttributes()
}

func (s *otlpShape) checkAttributes() error {
	if s.keyValues+s.arrayValues > maxKeyValuesPerRequest {
		return errTooManyKeyValues
	}
	return nil
}

// Attribute keys the scan prices specially. hotInput/hotOutput are copied to
// a column of their own (converter.go overflowAsString); the curated keys
// are copied into the curated dimension rows.
type keyClass uint8

const (
	keyPlain keyClass = iota
	keyHotInput
	keyHotOutput
	keyCurated
)

func classifyKey(k []byte) keyClass {
	switch string(k) {
	case "input.value":
		return keyHotInput
	case "output.value":
		return keyHotOutput
	case "user.id", "user.id.type", "user.id.hash", "user.metadata", "session.id":
		return keyCurated
	}
	return keyPlain
}

// promotedStringKey mirrors adapter.DeriveHotKeys' string aliases in priority
// order. Keep this fixed-size: the pre-decode scan must not allocate a map.
// Tests compare the resulting price with the real adapter/converter, including
// empty aliases and repeated keys/oneofs. Zero means not a promoted string.
func promotedStringKey(k []byte) int {
	switch string(k) {
	case "llm.model_name":
		return 1
	case "gen_ai.request.model":
		return 2
	case "gen_ai.response.model":
		return 3
	case "llm.request.model":
		return 4
	case "gen_ai.provider.name":
		return 5
	case "llm.system":
		return 6
	case "gen_ai.system":
		return 7
	case "llm.vendor":
		return 8
	case "llm.provider":
		return 9
	case "gen_ai.operation.name":
		return 10
	}
	return 0
}

// valueInfo is what one AnyValue writes as JSON.
type valueInfo struct {
	// Last recognized oneof: 0 means absent (an empty repeated message merges
	// without changing it), -1 a non-string, >=2 the final string's JSON length.
	// The other fields sum overwritten values to cover decoding allocations.
	lastStringJSON int64
	json           int64 // as chwriter encodes it (HTML characters unescaped)
	htmlJSON       int64 // as json.Marshal encodes it (HTML characters escaped)
	raw            int64 // string and bytes payload bytes inside it
	scalar         bool  // string, bool, int, double or empty
	isString       bool
}

// hotCopy is the JSON of the copy that conversion puts in the input or output
// column: the string itself, or json.Marshal of any other value, stored as a
// JSON string (so escaped once more, at most doubling it).
func (v valueInfo) hotCopy() int64 {
	if v.isString {
		return v.json
	}
	return 2*v.htmlJSON + 2
}

// asResourceColumn is the JSON of a resource attribute value in the row's
// resource_attrs map: a scalar as itself, anything else as its string form.
func (v valueInfo) asResourceColumn() int64 {
	if v.scalar {
		return v.json
	}
	return 2*v.htmlJSON + 2
}

// jsonEscapedLen is the length of s as encoding/json writes it inside a
// string, without (plain) and with (html) HTML escaping: a control byte
// becomes \u00XX (6 bytes), an invalid UTF-8 byte \ufffd (6), U+2028 and
// U+2029 6 bytes, and `"` and `\` 2.
func jsonEscapedLen(s []byte) (plain, html int64) {
	for i := 0; i < len(s); {
		c := s[i]
		if c < utf8.RuneSelf {
			n, h := asciiEscapedLen(c)
			plain += n
			html += h
			i++
			continue
		}
		r, size := utf8.DecodeRune(s[i:])
		n := runeEscapedLen(r, size)
		plain += n
		html += n
		i += size
	}
	return plain, html
}

func asciiEscapedLen(c byte) (plain, html int64) {
	switch {
	case c == '"' || c == '\\' || c == '\b' || c == '\f' || c == '\n' || c == '\r' || c == '\t':
		return 2, 2
	case c < 0x20:
		return 6, 6
	case c == '<' || c == '>' || c == '&':
		return 1, 6
	}
	return 1, 1
}

// runeEscapedLen is the escaped length of a non-ASCII rune decoded from size
// bytes (utf8.RuneError with size 1 is an invalid byte).
func runeEscapedLen(r rune, size int) int64 {
	if (r == utf8.RuneError && size == 1) || r == '\u2028' || r == '\u2029' {
		return 6
	}
	return int64(size)
}

// spanEventsJSON(n) bounds the span_events column for an event name of n
// bytes: the converter writes the name with %q (at most 4 bytes per byte),
// and the column is a string, so the whole is escaped again.
func spanEventJSON(nameLen int64) int64 { return 8*nameLen + 128 }

// Number of JSON bytes for a number, bool or null value: float64 formatting
// is at most 24 bytes.
const numberJSON = 24

// rsAccum gathers one ResourceSpans. Its resource attributes are written into
// every one of its spans' rows, and they may come before or after the spans.
type rsAccum struct {
	stampSeen     [6]bool
	stampValues   [6]valueInfo
	resourceJSON  int64 // resource_attrs plus the columns taken from it
	resourceBytes int64 // payload bytes, for maxResourceAttrBytes
	spans         int64
	spanJSON      int64
	maxSpanJSON   int64
}

func (s *otlpShape) endResourceSpans(a *rsAccum) error {
	if a.resourceBytes > maxResourceAttrBytes {
		return errResourceTooLarge
	}
	s.reserveResourceStamps(a)
	perRow := rowFixedJSON + a.resourceJSON
	s.rowJSONBytes += a.spanJSON + a.spans*perRow
	if a.spans > 0 {
		s.resourceSpansWithSpans++
		s.maxRowJSON = max(s.maxRowJSON, perRow+a.maxSpanJSON)
	}
	return nil
}

// spanAccum gathers one span.
type spanAccum struct {
	json      int64 // row JSON beyond rowFixedJSON and the resource
	nameJSON  int64
	hotInput  int64
	hotOutput int64
	curated   int64 // curated attribute values
	root      bool
	promoted  [11]int64 // last string for each alias, as adapter.Split keeps it
}

func firstPromotedJSON(lengths []int64) int64 {
	for _, n := range lengths {
		if n > 2 { // firstString skips empty strings
			return n
		}
	}
	return 0
}

func (s *otlpShape) endSpan(rs *rsAccum, sp *spanAccum) {
	sp.json += sp.hotInput + sp.hotOutput
	// model, provider, raw gen_ai_system and raw gen_ai_operation. These
	// share string backing with attrs_string but each is encoded on the wire.
	sp.json += firstPromotedJSON(sp.promoted[1:5]) + firstPromotedJSON(sp.promoted[5:10]) + sp.promoted[7] + sp.promoted[10]
	rs.spans++
	rs.spanJSON += sp.json
	rs.maxSpanJSON = max(rs.maxSpanJSON, sp.json)
	if sp.curated > 0 {
		s.curatedJSONBytes += curatedFixedJSON + sp.curated
	}
	if sp.root {
		s.rootSpans++
		s.curatedJSONBytes += curatedFixedJSON + sp.nameJSON + sp.hotInput + sp.hotOutput
	}
}

// kvInfo is one KeyValue.
type kvInfo struct {
	keyJSON  int64
	keyHTML  int64
	keyRaw   int64
	class    keyClass
	promoted int // final key, not an earlier overwritten key field
	stampKey int
	val      valueInfo
}

// json is the KeyValue as a member of a JSON object: "key":value,
func (kv kvInfo) json() int64 { return kv.keyJSON + 4 + kv.val.json }

func (kv kvInfo) htmlJSON() int64 { return kv.keyHTML + 4 + kv.val.htmlJSON }

// kvContext is where a KeyValue sits, which decides what it adds to a row.
type kvContext uint8

const (
	kvResource kvContext = iota
	kvSpan
	kvOther // scope, event, link: decoded, never stored
)

func (s *otlpShape) addKV(ctx kvContext, kv kvInfo, rs *rsAccum, sp *spanAccum) {
	switch ctx {
	case kvResource:
		if k := kv.stampKey; k != 0 && !rs.stampSeen[k] {
			rs.stampSeen[k], rs.stampValues[k] = true, kv.val
		}
		// The value may also be copied into a column of its own (service
		// name, project, org, semconv), so it is counted twice.
		rs.resourceJSON += 2 * (kv.keyJSON + 4 + kv.val.asResourceColumn())
		rs.resourceBytes += kv.keyRaw + kv.val.raw
	case kvSpan:
		sp.json += kv.json()
		if kv.promoted != 0 && kv.val.lastStringJSON > 0 {
			// Split overwrites the last string of a repeated attribute key. A
			// later non-string goes into another typed map, not attrs_string.
			sp.promoted[kv.promoted] = kv.val.lastStringJSON
		}
		switch kv.class {
		case keyHotInput:
			sp.hotInput += kv.val.hotCopy()
			if !kv.val.isString {
				s.hotCopyBytes += kv.val.hotCopy()
			}
		case keyHotOutput:
			sp.hotOutput += kv.val.hotCopy()
			if !kv.val.isString {
				s.hotCopyBytes += kv.val.hotCopy()
			}
		case keyCurated:
			sp.curated += kv.val.hotCopy()
		}
	}
}

// ── protobuf ───────────────────────────────────────────────────────────────

// scanOTLPProto measures a protobuf ExportTraceServiceRequest.
func scanOTLPProto(b []byte, stamps ...resourceStampBudget) (otlpShape, error) {
	var s otlpShape
	if len(stamps) != 0 {
		s.stamps = stamps[0]
	}
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return s, err
		}
		b = rest
		if num == 1 {
			if typ != protowire.BytesType {
				return s, errMalformed("resource_spans: wrong wire type")
			}
			if err := s.protoResourceSpans(val); err != nil {
				return s, err
			}
		}
	}
	return s, nil
}

// nextField splits the first field off b: val is the payload of a
// length-delimited field, nil otherwise.
func nextField(b []byte) (num protowire.Number, typ protowire.Type, val, rest []byte, err error) {
	num, typ, n := protowire.ConsumeTag(b)
	if n < 0 {
		return 0, 0, nil, nil, errMalformed("protobuf: %v", protowire.ParseError(n))
	}
	b = b[n:]
	if typ == protowire.BytesType {
		v, m := protowire.ConsumeBytes(b)
		if m < 0 {
			return 0, 0, nil, nil, errMalformed("protobuf: %v", protowire.ParseError(m))
		}
		return num, typ, v, b[m:], nil
	}
	m := protowire.ConsumeFieldValue(num, typ, b)
	if m < 0 {
		return 0, 0, nil, nil, errMalformed("protobuf: %v", protowire.ParseError(m))
	}
	return num, typ, nil, b[m:], nil
}

// protoString adds a string or bytes field to the decoded bytes and returns
// its escaped JSON length.
func (s *otlpShape) protoString(val []byte) (plain, html int64) {
	s.strings++
	s.stringBytes += int64(len(val))
	return jsonEscapedLen(val)
}

func (s *otlpShape) protoResourceSpans(b []byte) error {
	s.resourceSpans++
	if s.resourceSpans > maxResourceSpansPerRequest {
		return errTooManyResourceSpans
	}
	var rs rsAccum
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return err
		}
		b = rest
		if typ != protowire.BytesType {
			continue
		}
		switch num {
		case 1: // resource
			if err := s.protoAttributesOf(val, 1, kvResource, &rs, nil); err != nil {
				return err
			}
		case 2, 1000: // scope_spans; 1000: the deprecated InstrumentationLibrarySpans
			if err := s.protoScopeSpans(val, &rs); err != nil {
				return err
			}
		case 3: // schema_url
			s.protoString(val)
		}
	}
	return s.endResourceSpans(&rs)
}

// protoAttributesOf walks a message whose field attrField holds KeyValues
// (Resource, InstrumentationScope) and whose other fields are strings or
// scalars (strField, when non-zero, is a string).
func (s *otlpShape) protoAttributesOf(b []byte, attrField protowire.Number, ctx kvContext, rs *rsAccum, sp *spanAccum) error {
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return err
		}
		b = rest
		if typ != protowire.BytesType {
			continue
		}
		if num == attrField {
			kv, err := s.protoKeyValue(val, 0)
			if err != nil {
				return err
			}
			s.addKV(ctx, kv, rs, sp)
		} else {
			s.protoString(val) // scope name and version; nothing else is length-delimited
		}
	}
	return nil
}

func (s *otlpShape) protoScopeSpans(b []byte, rs *rsAccum) error {
	s.scopeSpans++
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return err
		}
		b = rest
		if typ != protowire.BytesType {
			continue
		}
		switch num {
		case 1: // scope: name 1, version 2, attributes 3
			if err := s.protoAttributesOf(val, 3, kvOther, rs, nil); err != nil {
				return err
			}
		case 2:
			if err := s.protoSpan(val, rs); err != nil {
				return err
			}
		case 3: // schema_url
			s.protoString(val)
		}
	}
	return nil
}

func (s *otlpShape) protoSpan(b []byte, rs *rsAccum) error {
	s.spans++
	if s.spans > maxSpansPerRequest {
		return errTooManySpans
	}
	sp := spanAccum{root: true}
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return err
		}
		b = rest
		if typ != protowire.BytesType {
			continue
		}
		switch num {
		case 1, 2: // trace_id, span_id: fixed-size arrays once decoded
		case 4: // SpanID.Unmarshal resets on empty; otherwise the last 8 bytes win.
			sp.root = true
			for _, b := range val {
				if b != 0 {
					sp.root = false
					break
				}
			}
		case 3: // trace_state, not stored
			s.protoString(val)
		case 5: // name: the name column, and the curated trace name of a root
			n, _ := s.protoString(val)
			sp.nameJSON = n + 2
			sp.json += n + 2
		case 9:
			kv, err := s.protoKeyValue(val, 0)
			if err != nil {
				return err
			}
			s.addKV(kvSpan, kv, rs, &sp)
		case 11:
			if err := s.protoEvent(val, &sp); err != nil {
				return err
			}
		case 13:
			if err := s.protoLink(val); err != nil {
				return err
			}
		case 15: // status: message 2
			if err := s.protoStatus(val, &sp); err != nil {
				return err
			}
		}
	}
	s.endSpan(rs, &sp)
	return nil
}

func (s *otlpShape) protoStatus(b []byte, sp *spanAccum) error {
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return err
		}
		b = rest
		if typ == protowire.BytesType && num == 2 {
			n, _ := s.protoString(val)
			sp.json += n + 2
		}
	}
	return nil
}

func (s *otlpShape) protoEvent(b []byte, sp *spanAccum) error {
	s.events++
	var nameLen int64
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return err
		}
		b = rest
		if typ != protowire.BytesType {
			continue
		}
		switch num {
		case 2: // name, written into the span_events column
			s.protoString(val)
			nameLen += int64(len(val))
		case 3:
			if _, err := s.protoKeyValue(val, 0); err != nil {
				return err
			}
		}
	}
	sp.json += spanEventJSON(nameLen)
	s.hotCopyBytes += spanEventJSON(nameLen)
	return nil
}

func (s *otlpShape) protoLink(b []byte) error {
	s.links++
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return err
		}
		b = rest
		if typ != protowire.BytesType {
			continue
		}
		switch num {
		case 3: // trace_state
			s.protoString(val)
		case 4:
			if _, err := s.protoKeyValue(val, 0); err != nil {
				return err
			}
		}
	}
	return nil
}

// protoKeyValue walks a KeyValue at the given value nesting depth.
func (s *otlpShape) protoKeyValue(b []byte, depth int) (kvInfo, error) {
	var kv kvInfo
	if err := s.addKeyValue(); err != nil {
		return kv, err
	}
	keyAny := keyPlain
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return kv, err
		}
		b = rest
		if typ != protowire.BytesType {
			continue
		}
		switch num {
		case 1: // key; the last one wins, but any special key prices the value
			kv.keyJSON, kv.keyHTML = s.protoString(val)
			kv.keyRaw = int64(len(val))
			kv.promoted = promotedStringKey(val)
			kv.stampKey = resourceStampKey(val)
			if c := classifyKey(val); c != keyPlain {
				keyAny = c
			}
		case 2:
			v, err := s.protoAnyValue(val, depth)
			if err != nil {
				return kv, err
			}
			kv.val = addValues(kv.val, v)
		}
	}
	kv.class = keyAny
	if kv.val == (valueInfo{}) {
		kv.val = valueInfo{json: 4, htmlJSON: 4, scalar: true} // no value: null
	}
	return kv, nil
}

// addValues combines repeated value fields: the decoder keeps the last, so
// the sum is an upper bound.
func addValues(a, b valueInfo) valueInfo {
	if a == (valueInfo{}) {
		return b
	}
	last := b.lastStringJSON
	if last == 0 {
		last = a.lastStringJSON
	}
	return valueInfo{json: a.json + b.json, htmlJSON: a.htmlJSON + b.htmlJSON, raw: a.raw + b.raw,
		scalar: a.scalar && b.scalar, isString: a.isString && b.isString, lastStringJSON: last}
}

func (s *otlpShape) protoAnyValue(b []byte, depth int) (valueInfo, error) {
	v := valueInfo{json: 4, htmlJSON: 4, scalar: true} // empty: null
	set := false
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return v, err
		}
		b = rest
		var one valueInfo
		switch {
		case num == 1 && typ == protowire.BytesType: // string_value
			p, h := s.protoString(val)
			one = valueInfo{json: p + 2, htmlJSON: h + 2, raw: int64(len(val)), scalar: true, isString: true}
		case num == 7 && typ == protowire.BytesType: // bytes_value: base64 in JSON
			s.bytesValues += int64(len(val))
			n := 4*((int64(len(val))+2)/3) + 2
			one = valueInfo{json: n, htmlJSON: n, raw: int64(len(val))}
		case num == 5 && typ == protowire.BytesType: // array_value
			if depth >= maxValueNesting {
				return v, errValueTooDeep
			}
			s.depth = max(s.depth, int64(depth+1)*protoDepthPerValue)
			a, err := s.protoArray(val, depth+1)
			if err != nil {
				return v, err
			}
			one = a
		case num == 6 && typ == protowire.BytesType: // kvlist_value
			if depth >= maxValueNesting {
				return v, errValueTooDeep
			}
			s.depth = max(s.depth, int64(depth+1)*protoDepthPerValue)
			a, err := s.protoKVList(val, depth+1)
			if err != nil {
				return v, err
			}
			one = a
		case num >= 2 && num <= 4: // bool, int, double
			one = valueInfo{json: numberJSON, htmlJSON: numberJSON, scalar: true}
		default:
			continue
		}
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
	return v, nil
}

func (s *otlpShape) protoArray(b []byte, depth int) (valueInfo, error) {
	s.containers++
	v := valueInfo{json: 2, htmlJSON: 2}
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return v, err
		}
		b = rest
		if num != 1 || typ != protowire.BytesType {
			continue
		}
		if err := s.addArrayValue(); err != nil {
			return v, err
		}
		e, err := s.protoAnyValue(val, depth)
		if err != nil {
			return v, err
		}
		v.json += e.json + 1
		v.htmlJSON += e.htmlJSON + 1
		v.raw += e.raw
	}
	return v, nil
}

func (s *otlpShape) protoKVList(b []byte, depth int) (valueInfo, error) {
	s.containers++
	v := valueInfo{json: 2, htmlJSON: 2}
	for len(b) > 0 {
		num, typ, val, rest, err := nextField(b)
		if err != nil {
			return v, err
		}
		b = rest
		if num != 1 || typ != protowire.BytesType {
			continue
		}
		kv, err := s.protoKeyValue(val, depth)
		if err != nil {
			return v, err
		}
		v.json += kv.json()
		v.htmlJSON += kv.htmlJSON()
		v.raw += kv.keyRaw + kv.val.raw
	}
	return v, nil
}
