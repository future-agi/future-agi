package server

// A small JSON shape checker for the contract tests that
// freeze GET /v1/capabilities and POST /v1/feedback (field names and JSON
// types, never values). A frozen shape may gain fields (an additive change);
// removing, renaming or retyping a pinned field fails.

import (
	"encoding/json"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// r7Shape is the expected JSON shape of a value.
type r7Shape struct {
	kind     string             // "string", "number", "bool", "object", "array", "map" (an object with any keys), "any"
	nullable bool               // null is also allowed
	fields   map[string]r7Shape // object: the pinned fields
	optional map[string]bool    // object: fields that may be absent (omitempty)
	elem     *r7Shape           // array and map: every element's shape
}

func r7String() r7Shape { return r7Shape{kind: "string"} }
func r7Number() r7Shape { return r7Shape{kind: "number"} }
func r7Bool() r7Shape   { return r7Shape{kind: "bool"} }
func r7Nullable(s r7Shape) r7Shape {
	s.nullable = true
	return s
}
func r7Object(fields map[string]r7Shape, optional ...string) r7Shape {
	o := map[string]bool{}
	for _, f := range optional {
		o[f] = true
	}
	return r7Shape{kind: "object", fields: fields, optional: o}
}
func r7Array(elem r7Shape) r7Shape { return r7Shape{kind: "array", elem: &elem} }
func r7Map(elem r7Shape) r7Shape   { return r7Shape{kind: "map", elem: &elem} }

// r7CheckShape reports every place body does not match shape. extra lists
// the unpinned fields it found (allowed: additive).
func r7CheckShape(t *testing.T, body []byte, shape r7Shape) (extra []string) {
	t.Helper()
	var v any
	if err := json.Unmarshal(body, &v); err != nil {
		t.Fatalf("not JSON: %v: %s", err, body)
	}
	var problems []string
	r7Walk("$", v, shape, &problems, &extra)
	for _, p := range problems {
		t.Error(p)
	}
	sort.Strings(extra)
	return extra
}

func r7Walk(path string, v any, s r7Shape, problems, extra *[]string) {
	if s.kind == "any" {
		return
	}
	if v == nil {
		if !s.nullable {
			*problems = append(*problems, path+": null, want "+s.kind)
		}
		return
	}
	bad := func(got string) { *problems = append(*problems, path+": "+got+", want "+s.kind) }
	switch s.kind {
	case "string":
		if _, ok := v.(string); !ok {
			bad(r7KindOf(v))
		}
	case "number":
		if _, ok := v.(float64); !ok {
			bad(r7KindOf(v))
		}
	case "bool":
		if _, ok := v.(bool); !ok {
			bad(r7KindOf(v))
		}
	case "array":
		a, ok := v.([]any)
		if !ok {
			bad(r7KindOf(v))
			return
		}
		for i, e := range a {
			r7Walk(path+"["+strconv.Itoa(i)+"]", e, *s.elem, problems, extra)
		}
	case "map":
		m, ok := v.(map[string]any)
		if !ok {
			bad(r7KindOf(v))
			return
		}
		for k, e := range m {
			r7Walk(path+"."+k, e, *s.elem, problems, extra)
		}
	case "object":
		m, ok := v.(map[string]any)
		if !ok {
			bad(r7KindOf(v))
			return
		}
		for name, fs := range s.fields {
			fv, present := m[name]
			if !present {
				if !s.optional[name] {
					*problems = append(*problems, path+"."+name+": missing")
				}
				continue
			}
			r7Walk(path+"."+name, fv, fs, problems, extra)
		}
		for name := range m {
			if _, pinned := s.fields[name]; !pinned {
				*extra = append(*extra, path+"."+name)
			}
		}
	}
}

func r7KindOf(v any) string {
	switch v.(type) {
	case string:
		return "string"
	case float64:
		return "number"
	case bool:
		return "bool"
	case []any:
		return "array"
	case map[string]any:
		return "object"
	}
	return "unknown"
}

// r7ErrorShape is every gateway error body: {"error": {message, type, param,
// code}}; metadata is optional.
var r7ErrorShape = r7Object(map[string]r7Shape{
	"error": r7Object(map[string]r7Shape{
		"message":  r7String(),
		"type":     r7String(),
		"param":    r7Nullable(r7String()),
		"code":     r7Nullable(r7String()),
		"metadata": {kind: "map", elem: &r7Shape{kind: "any"}}, // any value
	}, "metadata"),
})

// r7HasNestedMapEntry reports whether some element of the array field arr
// has a non-empty object field inner.
func r7HasNestedMapEntry(body []byte, arr, inner string) bool {
	var v map[string]any
	if json.Unmarshal(body, &v) != nil {
		return false
	}
	rows, _ := v[arr].([]any)
	for _, r := range rows {
		row, _ := r.(map[string]any)
		if m, _ := row[inner].(map[string]any); len(m) > 0 {
			return true
		}
	}
	return false
}

// r7BodyHas reports whether body names the JSON field name anywhere.
func r7BodyHas(body []byte, name string) bool {
	return strings.Contains(string(body), `"`+name+`"`)
}

// The checker itself: a retyped, renamed, missing or null field fails; an
// added field is only listed.
func TestR7ShapeCheckerCatchesChanges(t *testing.T) {
	shape := r7Object(map[string]r7Shape{
		"id":   r7String(),
		"n":    r7Number(),
		"rows": r7Array(r7Object(map[string]r7Shape{"ok": r7Bool()})),
		"m":    r7Map(r7Number()),
		"opt":  r7String(),
	}, "opt")
	for body, wantProblems := range map[string]int{
		`{"id":"x","n":1,"rows":[{"ok":true}],"m":{"a":1}}`:             0,
		`{"id":"x","n":1,"rows":[{"ok":true}],"m":{"a":1},"opt":"y"}`:   0,
		`{"id":1,"n":1,"rows":[{"ok":true}],"m":{"a":1}}`:               1, // retyped
		`{"ident":"x","n":1,"rows":[{"ok":true}],"m":{"a":1}}`:          1, // renamed: id missing
		`{"id":null,"n":1,"rows":[{"ok":true}],"m":{"a":1}}`:            1, // null
		`{"id":"x","n":1,"rows":[{"ok":"yes"}],"m":{"a":"1"}}`:          2, // nested
		`{"id":"x","n":1,"rows":[{"ok":true}],"m":{"a":1},"opt":false}`: 1,
	} {
		var v any
		if err := json.Unmarshal([]byte(body), &v); err != nil {
			t.Fatal(err)
		}
		var problems, extra []string
		r7Walk("$", v, shape, &problems, &extra)
		if len(problems) != wantProblems {
			t.Errorf("%s: problems %v, want %d", body, problems, wantProblems)
		}
	}
	var v any
	_ = json.Unmarshal([]byte(`{"id":"x","n":1,"rows":[],"m":{},"added":true}`), &v)
	var problems, extra []string
	r7Walk("$", v, shape, &problems, &extra)
	if len(problems) != 0 || len(extra) != 1 || extra[0] != "$.added" {
		t.Errorf("an added field: problems %v, extra %v; want none and [$.added]", problems, extra)
	}
}
