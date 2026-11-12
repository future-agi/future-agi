package restmcp

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// Generator annotations on a tool's inputSchema (the generator's README,
// "Request contract"): the vendor origin on the schema root, and the name a renamed
// query input has on the wire.
const (
	keyOrigin   = "x-origin"
	keyWireName = "x-wireName"
)

// nameMaps are the JSON Schema keywords whose object value maps names (not
// keywords) to schemas, or, for dependentRequired, to lists of names: an x-
// prefix there is a name and is kept.
var nameMaps = map[string]bool{
	"properties": true, "patternProperties": true, "$defs": true,
	"definitions": true, "dependentSchemas": true, "dependentRequired": true,
}

// dataKeywords hold JSON values, not schemas: they are kept as they are, x-
// members included, so a model is shown the value the runtime checks.
var dataKeywords = map[string]bool{"const": true, "enum": true, "default": true, "examples": true}

// input is one top-level property of a tool's inputSchema.
type input struct {
	name     string
	wireName string          // the query parameter name on the wire
	constVal json.RawMessage // set for a const input, always sent
	types    []string        // the schema's "type", as a list
	pattern  string          // required for Discovery reserved resource expansion
}

// inputSchema is what the runtime reads from a tool's inputSchema.
type inputSchema struct {
	origin   string
	inputs   map[string]*input
	required []string
	closed   bool            // additionalProperties: false
	listed   json.RawMessage // the schema as tools/list serves it: no x- keywords
}

// parseInputSchema reads a tool's inputSchema. An absent schema is an empty
// object schema.
func parseInputSchema(raw json.RawMessage) (*inputSchema, error) {
	if len(bytes.TrimSpace(raw)) == 0 {
		raw = json.RawMessage(`{"type":"object"}`)
	}
	var root map[string]json.RawMessage
	if err := json.Unmarshal(raw, &root); err != nil || root == nil {
		return nil, fmt.Errorf("inputSchema is not a JSON object")
	}
	s := &inputSchema{inputs: map[string]*input{}}
	if v, ok := root[keyOrigin]; ok {
		if err := json.Unmarshal(v, &s.origin); err != nil {
			return nil, fmt.Errorf("inputSchema %s is not a string", keyOrigin)
		}
	}
	if v, ok := root["required"]; ok {
		if err := json.Unmarshal(v, &s.required); err != nil {
			return nil, fmt.Errorf("inputSchema required is not a list of names")
		}
	}
	if v, ok := root["additionalProperties"]; ok {
		s.closed = bytes.Equal(bytes.TrimSpace(v), []byte("false"))
	}
	var props map[string]map[string]json.RawMessage
	if v, ok := root["properties"]; ok {
		if err := json.Unmarshal(v, &props); err != nil {
			return nil, fmt.Errorf("inputSchema properties is not an object of schemas")
		}
	}
	for name, prop := range props {
		in := &input{name: name, wireName: name}
		if v, ok := prop[keyWireName]; ok {
			if err := json.Unmarshal(v, &in.wireName); err != nil || in.wireName == "" {
				return nil, fmt.Errorf("input %q: %s is not a name", name, keyWireName)
			}
		}
		if v, ok := prop["const"]; ok {
			in.constVal = v
		}
		in.types = schemaTypes(prop["type"])
		if v, ok := prop["pattern"]; ok {
			if err := json.Unmarshal(v, &in.pattern); err != nil {
				return nil, fmt.Errorf("input %q: pattern is not a string", name)
			}
		}
		s.inputs[name] = in
	}
	for _, name := range s.required {
		if s.inputs[name] == nil {
			return nil, fmt.Errorf("inputSchema requires %q, which it does not define", name)
		}
	}
	listed, err := json.Marshal(stripAnnotations(decodeAny(raw), false))
	if err != nil {
		return nil, err
	}
	s.listed = listed
	return s, nil
}

// schemaTypes reads a "type" keyword: a name or a list of names.
func schemaTypes(raw json.RawMessage) []string {
	if len(raw) == 0 {
		return nil
	}
	var one string
	if json.Unmarshal(raw, &one) == nil {
		return []string{one}
	}
	var many []string
	_ = json.Unmarshal(raw, &many)
	return many
}

// names returns the schema's input names, sorted.
func (s *inputSchema) names() []string {
	out := make([]string, 0, len(s.inputs))
	for n := range s.inputs {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

func decodeAny(raw json.RawMessage) any {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	_ = dec.Decode(&v)
	return v
}

// stripAnnotations removes every x- keyword from a schema, at every depth,
// keeping x- names inside the name maps (properties and the like) and
// leaving values (const, enum, default, examples) as they are.
func stripAnnotations(v any, inNameMap bool) any {
	switch t := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, val := range t {
			switch {
			case inNameMap: // k is a name, val its schema
				out[k] = stripAnnotations(val, false)
			case strings.HasPrefix(k, "x-"): // an annotation
			case dataKeywords[k]:
				out[k] = val
			default:
				out[k] = stripAnnotations(val, nameMaps[k])
			}
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, val := range t {
			out[i] = stripAnnotations(val, false)
		}
		return out
	}
	return v
}
