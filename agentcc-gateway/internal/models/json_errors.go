package models

import (
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
)

// DescribeJSONError says what is wrong with a request body that err, from
// decoding it with encoding/json, refused, in the terms of the JSON the
// caller sent: never a Go type, struct or package name. A field of the
// wrong type is named by its JSON path ("messages", "tools.0.function"),
// with the kind of value it takes and the kind it got; a syntax error keeps
// encoding/json's own description, which names only the JSON.
func DescribeJSONError(err error) string {
	var typeErr *json.UnmarshalTypeError
	if errors.As(err, &typeErr) {
		want := jsonKind(typeErr.Type)
		got := typeErr.Value
		if i := strings.IndexByte(got, ' '); i > 0 {
			got = got[:i] // "number -5" is a number
		}
		if field := jsonFieldPath(typeErr.Field); field != "" {
			return fmt.Sprintf("%q must be %s, not %s.", field, want, withArticle(got))
		}
		if want == "an object" {
			want = "a JSON object"
		}
		return fmt.Sprintf("the body must be %s, not %s.", want, withArticle(got))
	}
	var syntaxErr *json.SyntaxError
	if errors.As(err, &syntaxErr) {
		return syntaxErr.Error()
	}
	msg := err.Error()
	if strings.Contains(msg, "into Go value") || strings.Contains(msg, "Go struct field") {
		return "the body does not have the fields this endpoint expects, or one of them has the wrong type."
	}
	return msg
}

// jsonFieldPath is the JSON path of a field encoding/json names by its
// struct path: the names of the embedded alias types a custom UnmarshalJSON
// decodes through ("Alias", "toolAlias", "plain") are not part of the JSON.
func jsonFieldPath(field string) string {
	var parts []string
	for _, part := range strings.Split(field, ".") {
		if part == "" || part == "plain" || strings.HasSuffix(part, "Alias") {
			continue
		}
		parts = append(parts, part)
	}
	return strings.Join(parts, ".")
}

// jsonKind names the JSON value a Go type decodes from, with its article.
func jsonKind(t reflect.Type) string {
	if t == nil {
		return "a valid value"
	}
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	switch t.Kind() {
	case reflect.Struct, reflect.Map:
		return "an object"
	case reflect.Slice, reflect.Array:
		return "an array"
	case reflect.String:
		return "a string"
	case reflect.Bool:
		return "true or false"
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64,
		reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return "an integer"
	case reflect.Float32, reflect.Float64:
		return "a number"
	}
	return "a valid value"
}

// withArticle is a JSON kind encoding/json reports ("string", "number",
// "array", "object", "bool") with its article.
func withArticle(kind string) string {
	switch kind {
	case "array", "object":
		return "an " + kind
	case "bool":
		return "true or false"
	case "":
		return "that value"
	}
	return "a " + kind
}
