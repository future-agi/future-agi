// Package schematest checks JSON values against the gateway's contract schemas in
// api_contracts/gateway/examples, for tests. It is the one checker the
// gateway's tests share, so a contract change is checked the same way
// everywhere.
//
// It covers the JSON Schema (2020-12) keywords those schemas use and fails on
// any other, so a schema that starts using a new keyword cannot pass
// unchecked: $ref (to #/$defs), $defs, type (one name), const, enum,
// required, properties, additionalProperties, items, anyOf, pattern, format
// (date-time only), minLength, maxLength, minimum, maximum, minItems and
// maxItems, plus the annotations $schema, $id, title, description and
// default.
package schematest

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

// ExamplesDir is api_contracts/gateway/examples, found from this file, so it
// resolves from any package's test.
func ExamplesDir() string {
	_, here, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(here), "..", "..", "..", "..", "api_contracts", "gateway", "examples")
}

// ReadExample reads and parses one file of ExamplesDir, a golden example or
// a schema, and fails the test if it cannot.
func ReadExample(t testing.TB, name string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(ExamplesDir(), name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	var v map[string]any
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatalf("parse %s: %v", name, err)
	}
	return v
}

// Validate checks instance, a value decoded from JSON (or anything that
// marshals to it), against the schema file and returns every violation; none
// means it conforms.
func Validate(t testing.TB, schemaFile string, instance any) []string {
	t.Helper()
	root := ReadExample(t, schemaFile)
	return validate(root, root, decoded(t, instance))
}

// ValidateDef is Validate against one of the schema file's $defs.
func ValidateDef(t testing.TB, schemaFile, def string, instance any) []string {
	t.Helper()
	root := ReadExample(t, schemaFile)
	schema, ok := root["$defs"].(map[string]any)[def]
	if !ok {
		t.Fatalf("%s has no $defs/%s", schemaFile, def)
	}
	return validate(root, schema, decoded(t, instance))
}

func validate(root map[string]any, schema, instance any) []string {
	v := &validator{root: root}
	v.check(schema, instance, "$")
	return v.errs
}

// decoded is instance as encoding/json decodes it (maps, slices, float64),
// the only form the checker reads.
func decoded(t testing.TB, instance any) any {
	t.Helper()
	switch instance.(type) {
	case map[string]any, []any, string, float64, bool, nil:
		return instance
	}
	b, err := json.Marshal(instance)
	if err != nil {
		t.Fatalf("marshal the instance: %v", err)
	}
	var v any
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatalf("decode the instance: %v", err)
	}
	return v
}

type validator struct {
	root map[string]any
	errs []string
}

var annotations = map[string]bool{"$schema": true, "$id": true, "title": true, "description": true, "default": true}

func (v *validator) fail(path, format string, args ...any) {
	v.errs = append(v.errs, path+": "+fmt.Sprintf(format, args...))
}

func (v *validator) check(schema any, inst any, path string) {
	s, ok := schema.(map[string]any)
	if !ok {
		v.fail(path, "schema is not an object: %v", schema)
		return
	}
	keys := make([]string, 0, len(s))
	for k := range s {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		v.keyword(s, k, inst, path)
	}
	v.object(s, inst, path)
	if arr, ok := inst.([]any); ok {
		if items, ok := s["items"]; ok {
			for i, el := range arr {
				v.check(items, el, fmt.Sprintf("%s[%d]", path, i))
			}
		}
	}
}

// keyword checks one keyword of s that does not need the object or array
// walk.
func (v *validator) keyword(s map[string]any, k string, inst any, path string) {
	kw := s[k]
	switch k {
	case "$ref":
		ref, _ := kw.(string)
		name, ok := strings.CutPrefix(ref, "#/$defs/")
		defs, _ := v.root["$defs"].(map[string]any)
		def, found := defs[name]
		if !ok || !found {
			v.fail(path, "unresolvable $ref %q", ref)
			return
		}
		v.check(def, inst, path)
	case "$defs", "properties", "additionalProperties", "items", "required":
		// walked by check and object
	case "type":
		typ, ok := kw.(string)
		if !ok {
			v.fail(path, "unsupported type form %v (one type name only)", kw)
		} else if !hasType(inst, typ) {
			v.fail(path, "%v is not of type %s", inst, typ)
		}
	case "const":
		if !reflect.DeepEqual(inst, kw) {
			v.fail(path, "%v is not the const %v", inst, kw)
		}
	case "enum":
		for _, e := range asSlice(kw) {
			if reflect.DeepEqual(inst, e) {
				return
			}
		}
		v.fail(path, "%v is not in enum %v", inst, kw)
	case "anyOf":
		for _, alt := range asSlice(kw) {
			sub := &validator{root: v.root}
			sub.check(alt, inst, path)
			if len(sub.errs) == 0 {
				return
			}
		}
		v.fail(path, "%v matches no anyOf branch", inst)
	case "pattern":
		if str, ok := inst.(string); ok && !regexp.MustCompile(kw.(string)).MatchString(str) {
			v.fail(path, "%q does not match %s", str, kw)
		}
	case "format":
		if kw != "date-time" {
			v.fail(path, "unsupported format %v", kw)
		} else if str, ok := inst.(string); ok {
			if _, err := time.Parse(time.RFC3339Nano, str); err != nil {
				v.fail(path, "%q is not a date-time", str)
			}
		}
	case "minLength", "maxLength":
		if str, ok := inst.(string); ok {
			n := float64(utf8.RuneCountInString(str))
			if (k == "minLength" && n < kw.(float64)) || (k == "maxLength" && n > kw.(float64)) {
				v.fail(path, "length %v breaks %s %v", n, k, kw)
			}
		}
	case "minimum", "maximum":
		if num, ok := inst.(float64); ok {
			if (k == "minimum" && num < kw.(float64)) || (k == "maximum" && num > kw.(float64)) {
				v.fail(path, "%v breaks %s %v", num, k, kw)
			}
		}
	case "minItems", "maxItems":
		if arr, ok := inst.([]any); ok {
			n := float64(len(arr))
			if (k == "minItems" && n < kw.(float64)) || (k == "maxItems" && n > kw.(float64)) {
				v.fail(path, "%v items breaks %s %v", n, k, kw)
			}
		}
	default:
		if !annotations[k] {
			v.fail(path, "unsupported schema keyword %q", k)
		}
	}
}

// object checks required, properties and additionalProperties on an object
// instance.
func (v *validator) object(s map[string]any, inst any, path string) {
	obj, ok := inst.(map[string]any)
	if !ok {
		return
	}
	for _, r := range asSlice(s["required"]) {
		if _, ok := obj[r.(string)]; !ok {
			v.fail(path, "required %q missing", r)
		}
	}
	props, _ := s["properties"].(map[string]any)
	names := make([]string, 0, len(obj))
	for k := range obj {
		names = append(names, k)
	}
	sort.Strings(names)
	for _, k := range names {
		if ps, ok := props[k]; ok {
			v.check(ps, obj[k], path+"."+k)
			continue
		}
		switch ap := s["additionalProperties"].(type) {
		case bool:
			if !ap {
				v.fail(path, "unknown key %q", k)
			}
		case map[string]any:
			v.check(ap, obj[k], path+"."+k)
		}
	}
}

func asSlice(v any) []any {
	s, _ := v.([]any)
	return s
}

func hasType(inst any, typ string) bool {
	switch typ {
	case "object":
		_, ok := inst.(map[string]any)
		return ok
	case "array":
		_, ok := inst.([]any)
		return ok
	case "string":
		_, ok := inst.(string)
		return ok
	case "boolean":
		_, ok := inst.(bool)
		return ok
	case "number":
		_, ok := inst.(float64)
		return ok
	case "integer":
		n, ok := inst.(float64)
		return ok && n == math.Trunc(n)
	case "null":
		return inst == nil
	}
	return false
}
