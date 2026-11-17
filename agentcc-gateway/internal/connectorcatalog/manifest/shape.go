package manifest

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"
)

// constraint is one field's `schema` tag, parsed (see the package comment).
// Bounds are -1 when unset.
type constraint struct {
	ref, keys        string
	constant         string
	hasConst         bool
	minLen, maxLen   int
	minElem, maxElem int
	min, max         float64
	hasRange         bool
	unique           bool
	object           bool
}

func parseConstraint(tag string) (constraint, error) {
	c := constraint{minLen: -1, maxLen: -1, minElem: -1, maxElem: -1}
	if tag == "" {
		return c, nil
	}
	for _, clause := range strings.Split(tag, ",") {
		key, val, _ := strings.Cut(clause, "=")
		var err error
		switch key {
		case "ref":
			c.ref = val
		case "keys":
			c.keys = val
		case "const":
			c.constant, c.hasConst = val, true
		case "len":
			c.minLen, c.maxLen, err = intBounds(val)
		case "elen":
			c.minElem, c.maxElem, err = intBounds(val)
		case "range":
			var lo, hi int
			lo, hi, err = intBounds(val)
			c.min, c.max, c.hasRange = float64(lo), float64(hi), true
		case "unique":
			c.unique = true
		case "object":
			c.object = true
		default:
			err = fmt.Errorf("unknown clause %q", clause)
		}
		if err != nil {
			return c, err
		}
	}
	for _, name := range []string{c.ref, c.keys} {
		if _, ok := stringDefs[name]; name != "" && !ok {
			return c, fmt.Errorf("no string def %q", name)
		}
	}
	return c, nil
}

func intBounds(s string) (int, int, error) {
	a, b, ok := strings.Cut(s, "..")
	lo, err1 := strconv.Atoi(a)
	hi, err2 := strconv.Atoi(b)
	if !ok || err1 != nil || err2 != nil || lo > hi {
		return 0, 0, fmt.Errorf("bad bounds %q", s)
	}
	return lo, hi, nil
}

// field is one schema key of a record type.
type field struct {
	name     string
	required bool
	typ      reflect.Type
	c        constraint
}

var fieldCache sync.Map // reflect.Type -> []field

// fieldsOf lists t's schema keys from its json and schema tags. A bad tag
// is a programming error, so it panics; TestSchemaParity visits every type.
func fieldsOf(t reflect.Type) []field {
	if v, ok := fieldCache.Load(t); ok {
		return v.([]field)
	}
	var out []field
	for i := range t.NumField() {
		sf := t.Field(i)
		tag := sf.Tag.Get("json")
		if tag == "-" || !sf.IsExported() {
			continue
		}
		name, opts, _ := strings.Cut(tag, ",")
		c, err := parseConstraint(sf.Tag.Get("schema"))
		if err != nil {
			panic(fmt.Sprintf("manifest: %s.%s: %v", t.Name(), sf.Name, err))
		}
		out = append(out, field{name: name, required: opts != "omitempty", typ: sf.Type, c: c})
	}
	fieldCache.Store(t, out)
	return out
}

var rawMessageType = reflect.TypeOf(json.RawMessage(nil))

// checkShape appends to errs every way v, a decoded document, breaks the
// shape of type t under constraint c: a missing required key, a key the
// type does not define, a null, a wrong JSON type, or a value outside its
// constraint. path is the JSON pointer of v.
func checkShape(path string, v any, t reflect.Type, c constraint, errs *[]error) {
	add := func(p, format string, args ...any) {
		*errs = append(*errs, fmt.Errorf("%s: %s", pointer(p), fmt.Sprintf(format, args...)))
	}
	if v == nil {
		add(path, "must not be null")
		return
	}
	if t == rawMessageType {
		if _, ok := v.(map[string]any); !ok {
			add(path, "must be a JSON object")
		}
		return
	}
	switch t.Kind() {
	case reflect.Pointer:
		checkShape(path, v, t.Elem(), c, errs)
	case reflect.Struct:
		obj, ok := v.(map[string]any)
		if !ok {
			add(path, "must be an object")
			return
		}
		known := map[string]field{}
		for _, f := range fieldsOf(t) {
			known[f.name] = f
			if _, present := obj[f.name]; f.required && !present {
				add(path+"/"+f.name, "is required")
			}
		}
		for _, k := range sortedNames(obj) {
			f, ok := known[k]
			if !ok {
				add(path+"/"+k, "is not a connector-manifest/v1 field")
				continue
			}
			checkShape(path+"/"+k, obj[k], f.typ, f.c, errs)
		}
	case reflect.Map:
		obj, ok := v.(map[string]any)
		if !ok {
			add(path, "must be an object")
			return
		}
		checkCount(path, len(obj), c, "entries", add)
		for _, k := range sortedNames(obj) {
			if c.keys != "" {
				if err := stringDefs[c.keys].validate(c.keys, k); err != nil {
					add(path, "%v", err)
				}
			}
			elem := constraint{minLen: c.minElem, maxLen: c.maxElem, minElem: -1, maxElem: -1}
			checkShape(path+"/"+k, obj[k], t.Elem(), elem, errs)
		}
	case reflect.Slice:
		arr, ok := v.([]any)
		if !ok {
			add(path, "must be an array")
			return
		}
		checkCount(path, len(arr), c, "items", add)
		if c.unique {
			seen := map[string]bool{}
			for i, e := range arr {
				key, _ := json.Marshal(e)
				if seen[string(key)] {
					add(fmt.Sprintf("%s/%d", path, i), "repeats %s", key)
				}
				seen[string(key)] = true
			}
		}
		elem := constraint{ref: c.ref, minLen: c.minElem, maxLen: c.maxElem, minElem: -1, maxElem: -1,
			min: c.min, max: c.max, hasRange: c.hasRange}
		for i, e := range arr {
			checkShape(fmt.Sprintf("%s/%d", path, i), e, t.Elem(), elem, errs)
		}
	case reflect.String:
		s, ok := v.(string)
		if !ok {
			add(path, "must be a string")
			return
		}
		n := utf8.RuneCountInString(s)
		switch {
		case c.hasConst && s != c.constant:
			add(path, "must be %q", c.constant)
		case (c.minLen >= 0 && n < c.minLen) || (c.maxLen >= 0 && n > c.maxLen):
			add(path, "must be %d to %d characters long", c.minLen, c.maxLen)
		case c.ref != "":
			if err := stringDefs[c.ref].validate(c.ref, s); err != nil {
				add(path, "%v", err)
			}
		}
	case reflect.Bool:
		if _, ok := v.(bool); !ok {
			add(path, "must be true or false")
		}
	case reflect.Int, reflect.Float64:
		var n float64
		switch x := v.(type) {
		case int64:
			n = float64(x)
		case float64:
			n = x
		default:
			add(path, "must be a number")
			return
		}
		if t.Kind() == reflect.Int && n != math.Trunc(n) {
			add(path, "must be an integer")
			return
		}
		if c.hasRange && (n < c.min || n > c.max) {
			add(path, "must be between %v and %v", c.min, c.max)
		}
	default:
		panic(fmt.Sprintf("manifest: no shape rule for %s", t))
	}
}

func checkCount(path string, n int, c constraint, noun string, add func(string, string, ...any)) {
	if (c.minLen >= 0 && n < c.minLen) || (c.maxLen >= 0 && n > c.maxLen) {
		add(path, "has %d %s; it holds %d to %d", n, noun, c.minLen, c.maxLen)
	}
}

// sortedNames returns a map's keys in order.
func sortedNames[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

// decodeShaped checks v against T's shape and, when it fits, decodes it into
// a T. The JSON round trip with unknown fields refused is a second guard.
func decodeShaped[T any](v any) (*T, []error) {
	var errs []error
	checkShape("", v, reflect.TypeFor[T](), constraint{minLen: -1, maxLen: -1, minElem: -1, maxElem: -1}, &errs)
	if len(errs) > 0 {
		return nil, errs
	}
	data, err := json.Marshal(v)
	if err != nil {
		return nil, []error{err}
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	out := new(T)
	if err := dec.Decode(out); err != nil {
		return nil, []error{err}
	}
	return out, nil
}
