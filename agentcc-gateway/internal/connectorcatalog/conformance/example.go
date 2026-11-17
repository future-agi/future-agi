package conformance

import (
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"regexp/syntax"
	"strings"
)

// exampleString is the string L3 sends where a schema asks for any string.
const exampleString = "cn8"

// maxExampleDepth bounds how deep exampleFor follows nested schemas.
const maxExampleDepth = 8

// exampleArguments is a tool's call arguments: an object holding every
// property its input schema requires, each with a value of its type, which
// is what the gateway's argument check reads (required names and top-level
// types). A missing or unreadable schema gives {}.
func exampleArguments(schema json.RawMessage) json.RawMessage {
	v := exampleFor(schema, 0)
	obj, ok := v.(map[string]any)
	if !ok {
		obj = map[string]any{}
	}
	out, _ := json.Marshal(obj) // a value built from decoded JSON always encodes
	return out
}

// exampleFor is a small value valid under a JSON Schema subset: const, enum,
// default, examples, the first of oneOf, anyOf or allOf, then the type's
// smallest value (an object with its required properties, minItems items,
// minLength characters, the minimum number).
func exampleFor(raw json.RawMessage, depth int) any {
	var s map[string]json.RawMessage
	if len(raw) == 0 || json.Unmarshal(raw, &s) != nil || s == nil || depth > maxExampleDepth {
		return map[string]any{}
	}
	for _, key := range []string{"const", "default"} {
		if v, ok := s[key]; ok {
			var out any
			if json.Unmarshal(v, &out) == nil {
				return out
			}
		}
	}
	for _, key := range []string{"enum", "examples", "oneOf", "anyOf", "allOf"} {
		var list []json.RawMessage
		if v, ok := s[key]; ok && json.Unmarshal(v, &list) == nil && len(list) > 0 {
			if key == "enum" || key == "examples" {
				var out any
				if json.Unmarshal(list[0], &out) == nil {
					return out
				}
				continue
			}
			return exampleFor(list[0], depth+1)
		}
	}
	switch schemaType(s) {
	case "object":
		return exampleObject(s, depth)
	case "array":
		return exampleArray(s, depth)
	case "integer":
		return math.Ceil(exampleNumber(s))
	case "number":
		return exampleNumber(s)
	case "boolean":
		return false
	case "null":
		return nil
	}
	return exampleText(s)
}

// schemaType is the schema's type: the first non-null one of a type list,
// object when it only lists properties, else string.
func schemaType(s map[string]json.RawMessage) string {
	var one string
	if json.Unmarshal(s["type"], &one) == nil && one != "" {
		return one
	}
	var many []string
	if json.Unmarshal(s["type"], &many) == nil {
		for _, t := range many {
			if t != "null" {
				return t
			}
		}
		if len(many) > 0 {
			return "null"
		}
	}
	if _, ok := s["properties"]; ok {
		return "object"
	}
	return "string"
}

func exampleObject(s map[string]json.RawMessage, depth int) map[string]any {
	var props map[string]json.RawMessage
	var required []string
	_ = json.Unmarshal(s["properties"], &props)
	_ = json.Unmarshal(s["required"], &required)
	out := map[string]any{}
	for _, name := range required {
		out[name] = exampleFor(props[name], depth+1)
	}
	return out
}

func exampleArray(s map[string]json.RawMessage, depth int) []any {
	var n int
	_ = json.Unmarshal(s["minItems"], &n)
	out := make([]any, 0, n)
	for i := 0; i < n && i < 16; i++ {
		out = append(out, exampleFor(s["items"], depth+1))
	}
	return out
}

// exampleNumber is the schema's minimum, just above an exclusive minimum, or
// 1, capped at its maximum.
func exampleNumber(s map[string]json.RawMessage) float64 {
	v := 1.0
	var bound float64
	if json.Unmarshal(s["minimum"], &bound) == nil {
		v = bound
	} else if json.Unmarshal(s["exclusiveMinimum"], &bound) == nil {
		v = math.Floor(bound) + 1
	}
	if json.Unmarshal(s["maximum"], &bound) == nil && v > bound {
		v = bound
	}
	return v
}

// exampleText is a string for the schema: one matching its pattern, a value
// of its format, else exampleString padded to minLength.
func exampleText(s map[string]json.RawMessage) string {
	var pattern, format string
	if json.Unmarshal(s["pattern"], &pattern) == nil && pattern != "" {
		if v, err := exampleMatching(pattern); err == nil {
			return v
		}
	}
	_ = json.Unmarshal(s["format"], &format)
	switch format {
	case "date-time":
		return "2026-10-19T00:00:00Z"
	case "date":
		return "2026-10-19"
	case "uri", "url":
		return "https://example.com/cn8"
	case "email":
		return "cn8@example.com"
	case "uuid":
		return "00000000-0000-4000-8000-000000000000"
	}
	v := exampleString
	var minLen int
	if json.Unmarshal(s["minLength"], &minLen) == nil && minLen > len(v) {
		v += strings.Repeat("x", minLen-len(v))
	}
	return v
}

// exampleMatching is a short string the pattern matches, built from the
// pattern's syntax tree (a letter for a character class, the fewest
// repetitions, the first alternative) and confirmed against the pattern.
func exampleMatching(pattern string) (string, error) {
	re, err := syntax.Parse(pattern, syntax.Perl)
	if err != nil {
		return "", fmt.Errorf("pattern %q: %w", pattern, err)
	}
	var b strings.Builder
	if !writeExample(&b, re.Simplify()) {
		return "", fmt.Errorf("pattern %q matches nothing", pattern)
	}
	match, err := regexp.MatchString(pattern, b.String())
	if err != nil || !match {
		return "", fmt.Errorf("pattern %q: no short example matches it (give the variable a default)", pattern)
	}
	return b.String(), nil
}

func writeExample(b *strings.Builder, re *syntax.Regexp) bool {
	switch re.Op {
	case syntax.OpNoMatch:
		return false
	case syntax.OpLiteral:
		b.WriteString(string(re.Rune))
	case syntax.OpCharClass:
		if len(re.Rune) == 0 {
			return false
		}
		b.WriteRune(pickRune(re.Rune))
	case syntax.OpAnyChar, syntax.OpAnyCharNotNL:
		b.WriteByte('a')
	case syntax.OpCapture, syntax.OpPlus:
		return writeExample(b, re.Sub[0])
	case syntax.OpRepeat:
		for i := 0; i < re.Min; i++ {
			if !writeExample(b, re.Sub[0]) {
				return false
			}
		}
	case syntax.OpConcat:
		for _, sub := range re.Sub {
			if !writeExample(b, sub) {
				return false
			}
		}
	case syntax.OpAlternate:
		return writeExample(b, re.Sub[0])
	}
	// OpStar and OpQuest repeat nothing; anchors and boundaries write nothing.
	return true
}

// pickRune is the first lower-case letter, upper-case letter or digit a
// class holds (pairs of lo, hi), else its first rune.
func pickRune(ranges []rune) rune {
	for _, want := range "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" {
		for i := 0; i+1 < len(ranges); i += 2 {
			if ranges[i] <= want && want <= ranges[i+1] {
				return want
			}
		}
	}
	return ranges[0]
}
