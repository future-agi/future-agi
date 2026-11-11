package contractspec

import (
	"fmt"
	"strings"
)

// discoveryWalker flattens Google API discovery documents
// (discovery#restDescription), as served by each API's $discovery endpoint.
// Schemas live in "schemas" keyed by ID and $ref is a bare ID. There is no
// oneOf: protobuf oneofs are flattened into sibling optional properties,
// which is what a flat Go struct expects. 64-bit integers have type string
// with format int64 (JSON carries them as strings) and are recorded as
// "string(int64)".
type discoveryWalker struct {
	schemas map[string]any
	b       *builder
}

// discoveryRootSchema resolves a root's selector: "schema:<id>" or a method
// path such as "models.generateContent" or
// "projects.locations.publishers.models.streamGenerateContent", whose
// response schema is the root.
func discoveryRootSchema(doc map[string]any, r RootDef) (string, string, error) {
	if r.Schema != "" {
		schemas, _ := doc["schemas"].(map[string]any)
		if _, ok := schemas[r.Schema]; !ok {
			return "", "", fmt.Errorf("schema %q not in discovery document", r.Schema)
		}
		return r.Schema, "schema " + r.Schema, nil
	}
	parts := strings.Split(r.Method, ".")
	if len(parts) < 2 {
		return "", "", fmt.Errorf("method %q: want <resource>[.<resource>...].<method>", r.Method)
	}
	node := doc
	for _, res := range parts[:len(parts)-1] {
		resources, _ := node["resources"].(map[string]any)
		next, ok := resources[res].(map[string]any)
		if !ok {
			return "", "", fmt.Errorf("method %q: no resource %q", r.Method, res)
		}
		node = next
	}
	methods, _ := node["methods"].(map[string]any)
	m, ok := methods[parts[len(parts)-1]].(map[string]any)
	if !ok {
		return "", "", fmt.Errorf("method %q not in discovery document", r.Method)
	}
	resp, _ := m["response"].(map[string]any)
	ref, _ := resp["$ref"].(string)
	if ref == "" {
		return "", "", fmt.Errorf("method %q has no response schema", r.Method)
	}
	return ref, fmt.Sprintf("method %s -> %s", r.Method, ref), nil
}

func (w *discoveryWalker) walk(s map[string]any, path string, refs []string, depth int) {
	b := w.b
	if depth > maxDepth {
		b.at(path).Recursive = "max-depth"
		return
	}
	if ref, ok := s["$ref"].(string); ok {
		for _, r := range refs {
			if r == ref {
				b.at(path).Recursive = ref
				return
			}
		}
		t, ok := w.schemas[ref].(map[string]any)
		if !ok {
			p := b.at(path)
			p.Opaque = true
			p.Recursive = "unresolved " + ref
			return
		}
		w.walk(t, path, append(refs, ref), depth+1)
		return
	}
	t, _ := s["type"].(string)
	if f, _ := s["format"].(string); t == "string" && (f == "int64" || f == "uint64") {
		t = "string(int64)"
	}
	if d, ok := s["deprecated"].(bool); ok && d {
		b.at(path).Deprecated = true
	}
	switch t {
	case "any":
		b.kind(path, "any")
		b.at(path).Opaque = true
	case "":
	default:
		b.kind(path, t)
	}
	if e, ok := s["enum"].([]any); ok {
		for _, v := range e {
			if vs, ok := v.(string); ok {
				b.enum(path, vs)
			}
		}
	}
	props, hasProps := s["properties"].(map[string]any)
	for _, k := range sortedKeys(props) {
		if pm, ok := props[k].(map[string]any); ok {
			w.walk(pm, path+"."+k, refs, depth+1)
		}
	}
	ap, hasAP := s["additionalProperties"].(map[string]any)
	if hasAP {
		b.kind(path, "map")
		w.walk(ap, path+".*", refs, depth+1)
	}
	if it, ok := s["items"].(map[string]any); ok {
		w.walk(it, path+"[]", refs, depth+1)
	}
	if t == "object" && !hasProps && !hasAP {
		b.at(path).Opaque = true // e.g. google.protobuf.Struct
	}
}

func discoveryMeta(doc map[string]any) map[string]string {
	m := map[string]string{}
	for _, k := range []string{"id", "version", "revision"} {
		if v, ok := doc[k]; ok {
			m[k] = scalarString(v)
		}
	}
	return m
}
