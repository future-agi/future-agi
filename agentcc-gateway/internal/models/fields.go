package models

import (
	"reflect"
	"strings"
)

// JSONFieldNames returns the JSON keys a struct defines, for telling a
// dialect's own request fields apart from a caller's additions. Derived from
// the struct so a hand-written list cannot go stale against it.
//
// Accepts a struct or pointer; walks embedded structs; skips `json:"-"`.
func JSONFieldNames(v any) map[string]struct{} {
	names := make(map[string]struct{})
	collectJSONFieldNames(reflect.TypeOf(v), names)
	return names
}

func collectJSONFieldNames(t reflect.Type, names map[string]struct{}) {
	for t != nil && t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if t == nil || t.Kind() != reflect.Struct {
		return
	}
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if f.PkgPath != "" && !f.Anonymous {
			continue // unexported, never serialised
		}
		tag, name := f.Tag.Get("json"), ""
		if comma := strings.IndexByte(tag, ','); comma >= 0 {
			name = tag[:comma]
		} else {
			name = tag
		}
		if name == "-" {
			continue
		}
		// Embedded with no name of its own flattens into the parent.
		if f.Anonymous && name == "" {
			collectJSONFieldNames(f.Type, names)
			continue
		}
		if name == "" {
			name = f.Name
		}
		names[name] = struct{}{}
	}
}
