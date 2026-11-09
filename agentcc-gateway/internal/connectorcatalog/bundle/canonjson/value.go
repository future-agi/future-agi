// Package canonjson is the JSON value model agentcc-catalog writes with.
//
// A value is an Object (members in order), a []any, a string, an int64, a
// float64, a bool or nil. Canonical renders a value in the RFC 8785 (JCS)
// form the catalog bundle's sha256 is taken over; Pretty renders it in the
// form Prettier 3 leaves unchanged, since the repository's commit hook
// formats every committed JSON file with Prettier; Compact renders it on one
// line for a YAML 1.2 reader. Parse and ParseYAML read values back, keeping
// member order.
package canonjson

import (
	"fmt"
	"slices"
	"unicode/utf16"
)

// Member is one name and value of an Object.
type Member struct {
	Key   string
	Value any
}

// Object is a JSON object whose members keep their order.
type Object []Member

// Get returns the value of key, and whether the object has it.
func (o Object) Get(key string) (any, bool) {
	for _, m := range o {
		if m.Key == key {
			return m.Value, true
		}
	}
	return nil, false
}

// Set replaces key's value, or appends the member.
func (o *Object) Set(key string, v any) {
	for i, m := range *o {
		if m.Key == key {
			(*o)[i].Value = v
			return
		}
	}
	*o = append(*o, Member{key, v})
}

// Delete removes key.
func (o *Object) Delete(key string) {
	*o = slices.DeleteFunc(*o, func(m Member) bool { return m.Key == key })
}

// Sorted returns v with every object's members in RFC 8785 order: by the
// UTF-16 code units of the key.
func Sorted(v any) any {
	switch x := v.(type) {
	case Object:
		out := make(Object, len(x))
		for i, m := range x {
			out[i] = Member{m.Key, Sorted(m.Value)}
		}
		slices.SortStableFunc(out, func(a, b Member) int { return compareUTF16(a.Key, b.Key) })
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = Sorted(e)
		}
		return out
	}
	return v
}

func compareUTF16(a, b string) int {
	return slices.Compare(utf16.Encode([]rune(a)), utf16.Encode([]rune(b)))
}

// normalize checks v's types, turning an int into an int64.
func normalize(v any) (any, error) {
	switch x := v.(type) {
	case nil, bool, string, int64, float64:
		return v, nil
	case int:
		return int64(x), nil
	case Object, []any:
		return v, nil
	}
	return nil, fmt.Errorf("canonjson: unsupported value of type %T", v)
}
