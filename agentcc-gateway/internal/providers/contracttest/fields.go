package contracttest

import (
	"bytes"
	"encoding"
	"encoding/json"
	"reflect"
	"sort"
	"strings"
	"testing"
)

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
// has a string) are a decode error, not an unknown field, and are skipped.
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
func UnknownFieldsAt(raw []byte, typ reflect.Type, root string) []string {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return []string{root}
	}
	found := map[string]bool{}
	walkUnknown(v, typ, root, found)
	out := make([]string, 0, len(found))
	for p := range found {
		out = append(out, p)
	}
	sort.Strings(out)
	return out
}

var (
	rawMessageType      = reflect.TypeFor[json.RawMessage]()
	jsonUnmarshalerType = reflect.TypeFor[json.Unmarshaler]()
	textUnmarshalerType = reflect.TypeFor[encoding.TextUnmarshaler]()
)

// opaque reports whether t accepts any JSON, so nothing below it can be
// unknown.
func opaque(t reflect.Type) bool {
	if t == rawMessageType || t.Kind() == reflect.Interface {
		return true
	}
	pt := reflect.PointerTo(t)
	return t.Implements(jsonUnmarshalerType) || pt.Implements(jsonUnmarshalerType) ||
		t.Implements(textUnmarshalerType) || pt.Implements(textUnmarshalerType)
}

func walkUnknown(v any, t reflect.Type, path string, found map[string]bool) {
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if opaque(t) {
		return
	}
	switch t.Kind() {
	case reflect.Struct:
		obj, ok := v.(map[string]any)
		if !ok {
			return
		}
		fields := jsonFields(t)
		for key, val := range obj {
			ft, ok := fields.lookup(key)
			if !ok {
				found[path+"."+key] = true
				continue
			}
			if ft == nil { // a ",string" field: a scalar encoded as a string
				continue
			}
			walkUnknown(val, ft, path+"."+key, found)
		}
	case reflect.Map:
		obj, ok := v.(map[string]any)
		if !ok {
			return
		}
		for _, val := range obj {
			walkUnknown(val, t.Elem(), path+".*", found)
		}
	case reflect.Slice, reflect.Array:
		if t.Elem().Kind() == reflect.Uint8 {
			return // []byte is a base64 string
		}
		arr, ok := v.([]any)
		if !ok {
			return
		}
		for _, el := range arr {
			walkUnknown(el, t.Elem(), path+"[]", found)
		}
	}
}

// fieldSet maps JSON key to field type. A nil type marks a ",string" field.
type fieldSet map[string]reflect.Type

func (fs fieldSet) lookup(key string) (reflect.Type, bool) {
	if ft, ok := fs[key]; ok {
		return ft, true
	}
	// encoding/json falls back to a case-insensitive match.
	for name, ft := range fs {
		if strings.EqualFold(name, key) {
			return ft, true
		}
	}
	return nil, false
}

// jsonFields collects the JSON keys a struct decodes, including the promoted
// fields of embedded structs. As in encoding/json, a shallower field shadows
// a deeper one of the same name.
func jsonFields(t reflect.Type) fieldSet {
	fs := fieldSet{}
	collectFields(t, fs, map[reflect.Type]bool{})
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
		if hasOpt(opts, "string") {
			fs[name] = nil
		} else {
			fs[name] = f.Type
		}
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

// AssertNoUnknownFields audits raw against typ for case c: it fails for every
// path UnknownFields reports that c's meta.json does not allowlist. Call it
// (or AssertKnownPaths) exactly once per case so Discover can check the
// allowlist for stale entries.
func AssertNoUnknownFields(t testing.TB, c Case, raw []byte, typ reflect.Type) {
	t.Helper()
	AssertKnownPaths(t, c, UnknownFields(raw, typ))
}

// AssertKnownPaths is AssertNoUnknownFields for paths the caller gathered
// itself — typically a stream, audited event by event with UnknownFieldsAt
// and collected into one list.
func AssertKnownPaths(t testing.TB, c Case, paths []string) {
	t.Helper()
	allowed := make(map[string]bool, len(c.Meta.AllowedUnknownFields))
	for _, f := range c.Meta.AllowedUnknownFields {
		allowed[f.Path] = true
	}
	seen := map[string]bool{}
	var used, unknown []string
	for _, p := range paths {
		if seen[p] {
			continue
		}
		seen[p] = true
		if allowed[p] {
			used = append(used, p)
		} else {
			unknown = append(unknown, p)
		}
	}
	c.Meta.noteFields(c.Name, used)
	if len(unknown) > 0 {
		sort.Strings(unknown)
		t.Errorf("upstream response has fields the adapter's structs do not represent (content would be dropped):\n  %s\n"+
			"Represent them, or allowlist each in %s/%s under allowed_unknown_fields with a reason.",
			strings.Join(unknown, "\n  "), c.Meta.dir, MetaFile)
	}
}
