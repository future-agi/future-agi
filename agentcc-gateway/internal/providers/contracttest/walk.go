package contracttest

import (
	"bytes"
	"encoding"
	"encoding/json"
	"fmt"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync"
)

// Kinds is a set of JSON value kinds, as Shape records them.
type Kinds uint8

// The JSON value kinds.
const (
	KindNull Kinds = 1 << iota
	KindBool
	KindNumber
	KindString
	KindArray
	KindObject
)

var kindNames = []struct {
	k    Kinds
	name string
}{{KindObject, "object"}, {KindArray, "array"}, {KindString, "string"}, {KindNumber, "number"}, {KindBool, "bool"}, {KindNull, "null"}}

// String renders the set as "object|null".
func (k Kinds) String() string {
	var names []string
	for _, kn := range kindNames {
		if k&kn.k != 0 {
			names = append(names, kn.name)
		}
	}
	if len(names) == 0 {
		return "none"
	}
	return strings.Join(names, "|")
}

func kindOf(v any) Kinds {
	switch v.(type) {
	case nil:
		return KindNull
	case bool:
		return KindBool
	case json.Number:
		return KindNumber
	case string:
		return KindString
	case []any:
		return KindArray
	case map[string]any:
		return KindObject
	}
	return 0
}

// Shape maps every path in one or more JSON documents to the kinds of value
// seen there, array elements unioned under "[]". Paths use the UnknownFields
// syntax; a map the struct models is "*", and keys inside unmodeled regions
// are spelled out. Regions declared Free are recorded at their own path only.
type Shape map[string]Kinds

// TypeMismatch is a value whose JSON kind the adapter's Go type does not
// accept: json.Unmarshal fails on it, and adapters that swallow the error
// lose the field.
type TypeMismatch struct {
	Path string
	// Got is the JSON kind found, e.g. "string" or "number 1.5".
	Got string
	// Want is what the Go type accepts, e.g. "number" or "integer".
	Want string
}

func (m TypeMismatch) String() string {
	return fmt.Sprintf("%s: got %s, struct wants %s", m.Path, m.Got, m.Want)
}

// Report is what the strict walk found in a case's upstream body: see
// Strict.Report.
type Report struct {
	// Unknown are keys a decode would discard (UnknownFields' paths), minus
	// the provider's Strict.Ignore.
	Unknown []string
	// Mismatches are values of the wrong JSON kind for the Go type.
	Mismatches []TypeMismatch
	// DecodeErrors are json.Unmarshal failures into a document's (or a
	// Decoded region's) type that no mismatch explains: a walker gap or a
	// custom unmarshaler's error.
	DecodeErrors []string
	// EnumViolations are values outside a declared EnumSpec.
	EnumViolations []string
	// Opaque are the regions where the walk had no Go type to follow
	// (json.RawMessage, any, map[string]any values, a custom unmarshaler
	// holding an object or array) and no Decoded, Watched or Free
	// declaration covered: what the adapter's structs do not watch. An
	// occurrence of a tagged Decoded region whose tag has no type is listed
	// as "<path> [<tag>=<value>]".
	Opaque []string
	// Shape is every path and the kinds seen there.
	Shape Shape

	targets  []target
	enumHits []string // declared EnumSpec paths that matched a value
}

// UnknownFields returns the JSON paths present in raw that typ does not
// represent: keys a json.Unmarshal into typ would silently discard. Paths are
// rooted at "$", step into objects with ".key" and into arrays with "[]" (no
// indices, so one path covers every element), and into map values with ".*".
// Only the outermost unknown key is reported; its children are implied.
//
// The walk mirrors encoding/json: it follows json tags (honouring "-", and
// treating ",string" values as scalars), promotes the fields of embedded
// structs, and falls back to case-insensitive key matching. It stops, and
// treats everything below as known, at interface types (any), json.RawMessage,
// and any type implementing json.Unmarshaler or encoding.TextUnmarshaler —
// those take whatever they are given. Mismatched kinds (an object where typ
// has a string) are a decode error, not an unknown field, and are skipped;
// Strict reports them.
//
// raw that is not valid JSON yields the single path "$".
func UnknownFields(raw []byte, typ reflect.Type) []string {
	return UnknownFieldsAt(raw, typ, "$")
}

// UnknownFieldsAt is UnknownFields with paths rooted at root instead of "$".
// Use it to audit a nested payload that the adapter decodes separately, e.g.
// a streamed event's json.RawMessage field:
//
//	UnknownFieldsAt(ev.Message, reflect.TypeFor[messageStartData](), "message_start:$.message")
//
// Strict does this declaratively (Decoded) and checks more.
func UnknownFieldsAt(raw []byte, typ reflect.Type, root string) []string {
	var none Strict
	w := none.newWalker()
	w.walkDoc(Doc{Root: root, Data: raw, Type: typ}, -1)
	return w.report().Unknown
}

var (
	rawMessageType      = reflect.TypeFor[json.RawMessage]()
	jsonNumberType      = reflect.TypeFor[json.Number]()
	jsonUnmarshalerType = reflect.TypeFor[json.Unmarshaler]()
	textUnmarshalerType = reflect.TypeFor[encoding.TextUnmarshaler]()
)

func implements(t, iface reflect.Type) bool {
	return t.Implements(iface) || reflect.PointerTo(t).Implements(iface)
}

// target is one node the mutation self-test may mutate.
type target struct {
	doc  int   // -1: the whole body; else the event index
	ptr  []any // path within the document: string keys, int indices
	path string
	kind mutationKind
	json Kinds  // the node's JSON kind
	want string // Go type, for messages
}

type mutationKind int

const (
	mutUnknownKey mutationKind = iota // inject a key into a modeled object
	mutTypeFlip                       // replace a modeled node with another kind
	mutFraction                       // a fractional number into an integer field
	mutEnum                           // an undeclared value at an enum path
	mutEvent                          // an undeclared stream event
)

func (k mutationKind) String() string {
	return [...]string{"unknown key", "type flip", "fraction into integer", "enum probe", "unknown event"}[k]
}

// walker walks JSON documents against Go types for one Report.
type walker struct {
	s   *strictIndex
	doc int

	unknown    map[string]bool
	mismatches map[string]TypeMismatch
	nMismatch  int
	decodeErrs []string
	enumBad    map[string]bool
	enumHits   map[string]bool
	opaque     map[string]bool
	shape      Shape
	targets    []target
	targetSeen map[string]bool
	stack      []any
}

func (s *Strict) newWalker() *walker {
	return &walker{
		s:          s.index(),
		unknown:    map[string]bool{},
		mismatches: map[string]TypeMismatch{},
		enumBad:    map[string]bool{},
		enumHits:   map[string]bool{},
		opaque:     map[string]bool{},
		shape:      Shape{},
		targetSeen: map[string]bool{},
	}
}

func (w *walker) report() Report {
	r := Report{Shape: w.shape, DecodeErrors: w.decodeErrs, targets: w.targets}
	r.Unknown = sortedKeys(w.unknown)
	r.EnumViolations = sortedKeys(w.enumBad)
	r.enumHits = sortedKeys(w.enumHits)
	r.Opaque = sortedKeys(w.opaque)
	for _, k := range sortedKeys(mapKeys(w.mismatches)) {
		r.Mismatches = append(r.Mismatches, w.mismatches[k])
	}
	return r
}

// walkDoc walks one document; idx is its event index, or -1 for a whole body.
func (w *walker) walkDoc(d Doc, idx int) {
	w.doc, w.stack = idx, w.stack[:0]
	dec := json.NewDecoder(bytes.NewReader(d.Data))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil || dec.More() {
		if d.Type != nil {
			w.unknown[d.Root] = true // not JSON at all: nothing in it is decoded
		}
		return
	}
	if d.Type == nil {
		w.walk(v, rawMessageType, d.Root) // not decoded: opaque unless declared
		return
	}
	before := w.nMismatch
	w.walk(v, d.Type, d.Root)
	if w.nMismatch == before {
		if err := json.Unmarshal(d.Data, reflect.New(d.Type).Interface()); err != nil {
			w.decodeErrs = append(w.decodeErrs, fmt.Sprintf("%s: %v", d.Root, err))
		}
	}
}

// walk visits v at path. t is the Go type the adapter decodes v into, or nil
// inside a region no type covers (an unknown key, an opaque region).
func (w *walker) walk(v any, t reflect.Type, path string) {
	w.shape[path] |= kindOf(v)
	w.checkEnum(v, path)
	if w.s.free[path] || w.s.match(w.s.freeWild, path) {
		return
	}
	if t == nil {
		w.walkUntyped(v, path)
		return
	}
	w.walkTyped(v, t, path)
}

// walkUntyped walks a region no Go type covers, for the shape and enums, and
// for declarations that give part of it a type again.
func (w *walker) walkUntyped(v any, path string) {
	if d := w.s.decodedAt(path); d != nil {
		w.walkDecoded(v, d, path)
		return
	}
	if keys := w.s.watchedAt(path); keys != nil {
		w.walkWatched(v, keys, path)
		return
	}
	w.walkChildren(v, path)
}

func (w *walker) walkChildren(v any, path string) {
	switch val := v.(type) {
	case map[string]any:
		for _, k := range sortedKeys(val) {
			w.push(k)
			w.walk(val[k], nil, path+"."+k)
			w.pop()
		}
	case []any:
		for i, el := range val {
			w.push(i)
			w.walk(el, nil, path+"[]")
			w.pop()
		}
	}
}

// enterOpaque handles a value whose Go type accepts anything.
func (w *walker) enterOpaque(v any, path string) {
	if d := w.s.decodedAt(path); d != nil {
		w.walkDecoded(v, d, path)
		return
	}
	if keys := w.s.watchedAt(path); keys != nil {
		w.walkWatched(v, keys, path)
		return
	}
	if _, isArray := v.([]any); isArray && w.s.declaredAt(path+"[]") {
		w.walkChildren(v, path) // each element is declared
		return
	}
	w.opaque[path] = true
	w.walkChildren(v, path)
}

func (w *walker) walkDecoded(v any, d *Decoded, path string) {
	typ := d.Type
	if d.Tag != "" {
		if v == nil {
			return
		}
		obj, ok := v.(map[string]any)
		if !ok {
			w.mismatch(path, v, fmt.Sprintf("object (a union tagged by %q)", d.Tag))
			w.walkChildren(v, path)
			return
		}
		tag, ok := obj[d.Tag].(string)
		if !ok {
			// Without its tag nothing can tell which type decodes it.
			w.mismatchMsg(path+"."+d.Tag, obj[d.Tag], "string (the union's tag)")
			w.walkChildren(v, path)
			return
		}
		if typ = d.Types[tag]; typ == nil {
			w.opaque[fmt.Sprintf("%s [%s=%s]", path, d.Tag, tag)] = true
			w.walkChildren(v, path)
			return
		}
	}
	if takesAnything(typ, v) {
		// The declared type is opaque itself (a custom unmarshaler, say):
		// following it would come straight back here.
		w.opaque[path] = true
		w.walkChildren(v, path)
		return
	}
	before := w.nMismatch
	w.walkTyped(v, typ, path)
	if w.nMismatch == before && v != nil {
		raw, _ := json.Marshal(v)
		if err := json.Unmarshal(raw, reflect.New(typ).Interface()); err != nil {
			w.decodeErrs = append(w.decodeErrs, fmt.Sprintf("%s: %v", path, err))
		}
	}
}

// takesAnything reports whether t accepts v without a structure the walk
// can follow: json.RawMessage, an interface, or a custom unmarshaler given
// an object or array.
func takesAnything(t reflect.Type, v any) bool {
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if t == rawMessageType || t.Kind() == reflect.Interface {
		return true
	}
	k := kindOf(v)
	return implements(t, jsonUnmarshalerType) && (k == KindObject || k == KindArray)
}

func (w *walker) walkWatched(v any, keys map[string]bool, path string) {
	obj, ok := v.(map[string]any)
	if !ok {
		if v != nil {
			w.mismatch(path, v, "object (a Watched region)")
		}
		w.walkChildren(v, path)
		return
	}
	w.addTarget(mutUnknownKey, path, KindObject, "watched")
	for _, k := range sortedKeys(obj) {
		cp := path + "." + k
		if !keys[k] && !w.ignored(cp) {
			w.unknown[cp] = true
		}
		w.push(k)
		w.walk(obj[k], nil, cp)
		w.pop()
	}
}

func (w *walker) walkTyped(v any, t reflect.Type, path string) {
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if v == nil {
		return // null decodes into anything, leaving the zero value
	}
	if t == rawMessageType || t.Kind() == reflect.Interface {
		w.enterOpaque(v, path)
		return
	}
	if implements(t, jsonUnmarshalerType) {
		switch kindOf(v) {
		case KindObject, KindArray:
			w.enterOpaque(v, path) // its own decoder: keys below are unseen
		default:
			raw, _ := json.Marshal(v)
			if err := reflect.New(t).Interface().(json.Unmarshaler).UnmarshalJSON(raw); err != nil {
				w.mismatchMsg(path, v, fmt.Sprintf("a value %s accepts (%v)", t, err))
			}
		}
		return
	}
	if implements(t, textUnmarshalerType) {
		s, ok := v.(string)
		if !ok {
			w.mismatch(path, v, "string")
			w.walkChildren(v, path)
			return
		}
		if err := reflect.New(t).Interface().(encoding.TextUnmarshaler).UnmarshalText([]byte(s)); err != nil {
			w.mismatchMsg(path, v, fmt.Sprintf("text %s accepts (%v)", t, err))
		}
		return
	}

	switch t.Kind() {
	case reflect.Struct:
		obj, ok := v.(map[string]any)
		if !ok {
			w.mismatch(path, v, "object")
			w.walkChildren(v, path)
			return
		}
		w.addTarget(mutUnknownKey, path, KindObject, t.String())
		w.addTarget(mutTypeFlip, path, KindObject, t.String())
		fields := fieldsOf(t)
		for _, k := range sortedKeys(obj) {
			child, cp := obj[k], path+"."+k
			w.push(k)
			f, known := fields.lookup(k)
			switch {
			case !known:
				if !w.ignored(cp) {
					w.unknown[cp] = true
				}
				w.walk(child, nil, cp)
			case f.quoted:
				// ",string": the scalar arrives inside a JSON string.
				w.shape[cp] |= kindOf(child)
				w.checkEnum(child, cp)
				if _, isStr := child.(string); child != nil && !isStr {
					w.mismatch(cp, child, "string (the field is tagged \",string\")")
				}
			default:
				w.walk(child, f.typ, cp)
			}
			w.pop()
		}
	case reflect.Map:
		obj, ok := v.(map[string]any)
		if !ok {
			w.mismatch(path, v, "object")
			w.walkChildren(v, path)
			return
		}
		w.addTarget(mutTypeFlip, path, KindObject, t.String())
		for _, k := range sortedKeys(obj) {
			w.push(k)
			w.walk(obj[k], t.Elem(), path+".*")
			w.pop()
		}
	case reflect.Slice, reflect.Array:
		if t.Kind() == reflect.Slice && t.Elem().Kind() == reflect.Uint8 {
			w.leaf(v, path, KindString, "string", t) // []byte is base64 text
			return
		}
		arr, ok := v.([]any)
		if !ok {
			w.mismatch(path, v, "array")
			w.walkChildren(v, path)
			return
		}
		w.addTarget(mutTypeFlip, path, KindArray, t.String())
		for i, el := range arr {
			w.push(i)
			w.walk(el, t.Elem(), path+"[]")
			w.pop()
		}
	case reflect.String:
		if t == jsonNumberType {
			// json.Number takes a number, or a string holding one.
			_, isNum := v.(json.Number)
			if s, isStr := v.(string); isStr {
				_, err := strconv.ParseFloat(s, 64)
				isNum = err == nil
			}
			if !isNum {
				w.mismatch(path, v, "number")
			}
			return
		}
		w.leaf(v, path, KindString, "string", t)
	case reflect.Bool:
		w.leaf(v, path, KindBool, "bool", t)
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		w.integer(v, path, t, func(s string) error { _, err := strconv.ParseInt(s, 10, t.Bits()); return err })
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr:
		w.integer(v, path, t, func(s string) error { _, err := strconv.ParseUint(s, 10, t.Bits()); return err })
	case reflect.Float32, reflect.Float64:
		w.leaf(v, path, KindNumber, "number", t)
	}
}

// leaf checks a scalar against the kind its Go type accepts.
func (w *walker) leaf(v any, path string, want Kinds, wantName string, t reflect.Type) {
	if kindOf(v) != want {
		w.mismatch(path, v, wantName)
		w.walkChildren(v, path)
		return
	}
	w.addTarget(mutTypeFlip, path, want, t.String())
}

func (w *walker) integer(v any, path string, t reflect.Type, parse func(string) error) {
	n, ok := v.(json.Number)
	if !ok {
		w.mismatch(path, v, "integer")
		w.walkChildren(v, path)
		return
	}
	if err := parse(string(n)); err != nil {
		w.mismatchMsg(path, v, fmt.Sprintf("integer (%s)", t))
		return
	}
	w.addTarget(mutTypeFlip, path, KindNumber, t.String())
	w.addTarget(mutFraction, path, KindNumber, t.String())
}

func (w *walker) mismatch(path string, v any, want string) { w.mismatchMsg(path, v, want) }

// mismatchMsg records that v at path is not what want describes.
func (w *walker) mismatchMsg(path string, v any, want string) {
	got := kindOf(v).String()
	if v == nil {
		got = "null or missing"
	}
	if n, ok := v.(json.Number); ok {
		got = "number " + string(n)
	}
	w.nMismatch++
	key := path + "\x00" + kindOf(v).String()
	if _, seen := w.mismatches[key]; !seen {
		w.mismatches[key] = TypeMismatch{Path: path, Got: got, Want: want}
	}
}

func (w *walker) ignored(path string) bool {
	_, ok := w.s.ignore[path]
	return ok
}

func (w *walker) checkEnum(v any, path string) {
	specs := w.s.enumsAt(path)
	if len(specs) == 0 || v == nil {
		return
	}
	if w.s.enums[path] != nil {
		w.enumHits[path] = true
	}
	if wp := wild(path); wp != "" && w.s.enums[wp] != nil {
		w.enumHits[wp] = true
	}
	s, isStr := v.(string)
	for _, e := range specs {
		switch {
		case !isStr:
			w.enumBad[fmt.Sprintf("%s: got %s, want one of %s's string values", path, kindOf(v), e.name())] = true
		case !e.values[s]:
			w.enumBad[fmt.Sprintf("%s: %q is not one of %s's values %q", path, s, e.name(), e.spec.Values)] = true
		}
	}
	if isStr {
		w.addTarget(mutEnum, path, KindString, "enum")
	}
}

func (w *walker) push(tok any) { w.stack = append(w.stack, tok) }
func (w *walker) pop()         { w.stack = w.stack[:len(w.stack)-1] }

// addTarget records the current node as a mutation target, once per
// (kind, path, Go type) across the report: the first occurrence stands for
// the rest.
func (w *walker) addTarget(kind mutationKind, path string, jsonKind Kinds, want string) {
	key := fmt.Sprintf("%d\x00%s\x00%s", kind, path, want)
	if w.targetSeen[key] {
		return
	}
	w.targetSeen[key] = true
	w.targets = append(w.targets, target{
		doc: w.doc, ptr: append([]any(nil), w.stack...), path: path,
		kind: kind, json: jsonKind, want: want,
	})
}

// field is one JSON key a struct decodes.
type field struct {
	typ    reflect.Type
	quoted bool // tagged ",string"
}

// fieldSet maps JSON key to field.
type fieldSet map[string]field

func (fs fieldSet) lookup(key string) (field, bool) {
	if f, ok := fs[key]; ok {
		return f, true
	}
	// encoding/json falls back to a case-insensitive match.
	for name, f := range fs {
		if strings.EqualFold(name, key) {
			return f, true
		}
	}
	return field{}, false
}

var fieldCache sync.Map // reflect.Type -> fieldSet

// fieldsOf collects the JSON keys a struct decodes, including the promoted
// fields of embedded structs. As in encoding/json, a shallower field shadows
// a deeper one of the same name.
func fieldsOf(t reflect.Type) fieldSet {
	if fs, ok := fieldCache.Load(t); ok {
		return fs.(fieldSet)
	}
	fs := fieldSet{}
	collectFields(t, fs, map[reflect.Type]bool{})
	fieldCache.Store(t, fs)
	return fs
}

func collectFields(t reflect.Type, fs fieldSet, visiting map[reflect.Type]bool) {
	if visiting[t] {
		return
	}
	visiting[t] = true
	defer delete(visiting, t)

	var embedded []reflect.Type
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		tag := f.Tag.Get("json")
		if tag == "-" {
			continue
		}
		name, opts, _ := strings.Cut(tag, ",")
		if f.Anonymous && name == "" {
			et := f.Type
			for et.Kind() == reflect.Pointer {
				et = et.Elem()
			}
			if et.Kind() == reflect.Struct {
				embedded = append(embedded, et)
				continue
			}
		}
		if !f.IsExported() {
			continue
		}
		if name == "" {
			name = f.Name
		}
		if _, taken := fs[name]; taken {
			continue
		}
		fs[name] = field{typ: f.Type, quoted: hasOpt(opts, "string")}
	}
	// Embedded fields come after this level's own, so they never shadow them.
	for _, et := range embedded {
		collectFields(et, fs, visiting)
	}
}

func hasOpt(opts, want string) bool {
	for _, o := range strings.Split(opts, ",") {
		if o == want {
			return true
		}
	}
	return false
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func mapKeys[V any](m map[string]V) map[string]bool {
	out := make(map[string]bool, len(m))
	for k := range m {
		out[k] = true
	}
	return out
}
