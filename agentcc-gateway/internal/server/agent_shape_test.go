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

// jsonShape is the expected JSON shape of a value.
type jsonShape struct {
	kind     string               // "string", "number", "bool", "object", "array", "map" (an object with any keys), "any"
	nullable bool                 // null is also allowed
	fields   map[string]jsonShape // object: the pinned fields
	optional map[string]bool      // object: fields that may be absent (omitempty)
	elem     *jsonShape           // array and map: every element's shape
}

func shapeString() jsonShape { return jsonShape{kind: "string"} }
func shapeNumber() jsonShape { return jsonShape{kind: "number"} }
func shapeBool() jsonShape   { return jsonShape{kind: "bool"} }
func shapeNullable(s jsonShape) jsonShape {
	s.nullable = true
	return s
}
func shapeObject(fields map[string]jsonShape, optional ...string) jsonShape {
	o := map[string]bool{}
	for _, f := range optional {
		o[f] = true
	}
	return jsonShape{kind: "object", fields: fields, optional: o}
}
func shapeArray(elem jsonShape) jsonShape { return jsonShape{kind: "array", elem: &elem} }
func shapeMap(elem jsonShape) jsonShape   { return jsonShape{kind: "map", elem: &elem} }

// checkShape reports every place body does not match shape. extra lists
// the unpinned fields it found (allowed: additive).
func checkShape(t *testing.T, body []byte, shape jsonShape) (extra []string) {
	t.Helper()
	var v any
	if err := json.Unmarshal(body, &v); err != nil {
		t.Fatalf("not JSON: %v: %s", err, body)
	}
	var problems []string
	walkShape("$", v, shape, &problems, &extra)
	for _, p := range problems {
		t.Error(p)
	}
	sort.Strings(extra)
	return extra
}

func walkShape(path string, v any, s jsonShape, problems, extra *[]string) {
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
			bad(shapeKindOf(v))
		}
	case "number":
		if _, ok := v.(float64); !ok {
			bad(shapeKindOf(v))
		}
	case "bool":
		if _, ok := v.(bool); !ok {
			bad(shapeKindOf(v))
		}
	case "array":
		a, ok := v.([]any)
		if !ok {
			bad(shapeKindOf(v))
			return
		}
		for i, e := range a {
			walkShape(path+"["+strconv.Itoa(i)+"]", e, *s.elem, problems, extra)
		}
	case "map":
		m, ok := v.(map[string]any)
		if !ok {
			bad(shapeKindOf(v))
			return
		}
		for k, e := range m {
			walkShape(path+"."+k, e, *s.elem, problems, extra)
		}
	case "object":
		m, ok := v.(map[string]any)
		if !ok {
			bad(shapeKindOf(v))
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
			walkShape(path+"."+name, fv, fs, problems, extra)
		}
		for name := range m {
			if _, pinned := s.fields[name]; !pinned {
				*extra = append(*extra, path+"."+name)
			}
		}
	}
}

func shapeKindOf(v any) string {
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

// errorShape is every gateway error body: {"error": {message, type, param,
// code}}; metadata is optional.
var errorShape = shapeObject(map[string]jsonShape{
	"error": shapeObject(map[string]jsonShape{
		"message":  shapeString(),
		"type":     shapeString(),
		"param":    shapeNullable(shapeString()),
		"code":     shapeNullable(shapeString()),
		"metadata": {kind: "map", elem: &jsonShape{kind: "any"}}, // any value
	}, "metadata"),
})

// hasNestedMapEntry reports whether some element of the array field arr
// has a non-empty object field inner.
func hasNestedMapEntry(body []byte, arr, inner string) bool {
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

// bodyHasField reports whether body names the JSON field name anywhere.
func bodyHasField(body []byte, name string) bool {
	return strings.Contains(string(body), `"`+name+`"`)
}

// The checker itself: a retyped, renamed, missing or null field fails; an
// added field is only listed.
func TestShapeCheckerCatchesChanges(t *testing.T) {
	shape := shapeObject(map[string]jsonShape{
		"id":   shapeString(),
		"n":    shapeNumber(),
		"rows": shapeArray(shapeObject(map[string]jsonShape{"ok": shapeBool()})),
		"m":    shapeMap(shapeNumber()),
		"opt":  shapeString(),
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
		walkShape("$", v, shape, &problems, &extra)
		if len(problems) != wantProblems {
			t.Errorf("%s: problems %v, want %d", body, problems, wantProblems)
		}
	}
	var v any
	_ = json.Unmarshal([]byte(`{"id":"x","n":1,"rows":[],"m":{},"added":true}`), &v)
	var problems, extra []string
	walkShape("$", v, shape, &problems, &extra)
	if len(problems) != 0 || len(extra) != 1 || extra[0] != "$.added" {
		t.Errorf("an added field: problems %v, extra %v; want none and [$.added]", problems, extra)
	}
}
