package contractspec

import (
	"fmt"
	"sort"
	"strings"
)

// maxDepth bounds every walk. Real response trees are under 15 deep; the
// bound only stops a pathological (or hostile) spec.
const maxDepth = 40

// openAPIWalker flattens OpenAPI 3.0, 3.1 and 3.2 schemas.
//
//   - $ref: local ("#/...") only; anything else is recorded as an
//     unresolved, opaque node and never fetched. 3.1 sibling keywords next
//     to a $ref are merged in.
//   - allOf: every member contributes at the same path.
//   - oneOf / anyOf: a path-merged union. Every arm contributes at the same
//     path, which is how a hand-written Go struct models a union. A
//     {"type":"null"} arm only makes the path nullable; a plain-string arm
//     beside enum arms makes the enum open. A discriminator's values (its
//     mapping keys, else each arm's const on the property) become the enum
//     of <path>.<property>, and each arm's paths are tagged with the arm.
//   - type as a string or a list (3.1), nullable (3.0), const, enum (a null
//     member makes the path nullable).
//   - properties, items, prefixItems, additionalProperties (a schema is the
//     map value ".*"; true or {} with no properties is an opaque map).
//   - writeOnly properties are skipped: they are never in a response.
//
// Vendor extensions are ignored.
type openAPIWalker struct {
	doc   map[string]any
	b     *builder
	elide map[string]string // schema pointer -> the root that flattens it
	prune map[string]string // path -> why nothing below it is listed
}

func (w *openAPIWalker) resolve(ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("non-local $ref %q is not followed", ref)
	}
	node, err := pointer(w.doc, ref)
	if err != nil {
		return nil, err
	}
	m, ok := node.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("$ref %q is not an object", ref)
	}
	return m, nil
}

// pointer resolves a JSON pointer ("#/a/b~1c") in doc.
func pointer(doc map[string]any, ptr string) (any, error) {
	if !strings.HasPrefix(ptr, "#/") {
		return nil, fmt.Errorf("pointer %q does not start with #/", ptr)
	}
	var node any = doc
	for _, seg := range strings.Split(ptr[2:], "/") {
		seg = strings.ReplaceAll(strings.ReplaceAll(seg, "~1", "/"), "~0", "~")
		switch n := node.(type) {
		case map[string]any:
			next, ok := n[seg]
			if !ok {
				return nil, fmt.Errorf("pointer %q: no %q", ptr, seg)
			}
			node = next
		case []any:
			var i int
			if _, err := fmt.Sscanf(seg, "%d", &i); err != nil || i < 0 || i >= len(n) {
				return nil, fmt.Errorf("pointer %q: bad index %q", ptr, seg)
			}
			node = n[i]
		default:
			return nil, fmt.Errorf("pointer %q: cannot step into %q", ptr, seg)
		}
	}
	return node, nil
}

func escapePointer(s string) string {
	return strings.ReplaceAll(strings.ReplaceAll(s, "~", "~0"), "/", "~1")
}

// openAPIRootSchemas finds the schemas a root selects: a schema pointer, or
// an operation's response(s). The second result describes the selection.
func openAPIRootSchemas(doc map[string]any, r RootDef) ([]map[string]any, string, error) {
	if r.Schema != "" {
		if _, err := pointer(doc, r.Schema); err != nil {
			return nil, "", err
		}
		return []map[string]any{{"$ref": r.Schema}}, r.Schema, nil
	}
	method, path, ok := strings.Cut(r.Op, " ")
	if !ok {
		return nil, "", fmt.Errorf("op %q: want \"METHOD /path\"", r.Op)
	}
	opPtr := "#/paths/" + escapePointer(path) + "/" + strings.ToLower(method)
	opNode, err := pointer(doc, opPtr)
	if err != nil {
		return nil, "", fmt.Errorf("op %q not in spec: %w", r.Op, err)
	}
	op, _ := opNode.(map[string]any)
	responses, _ := op["responses"].(map[string]any)
	if responses == nil {
		return nil, "", fmt.Errorf("op %q has no responses", r.Op)
	}
	content := r.Content
	if content == "" {
		content = "application/json"
	}
	var codes []string
	switch status := r.status(); status {
	case "errors":
		for code := range responses {
			if code == "default" || (len(code) == 3 && (code[0] == '4' || code[0] == '5')) {
				codes = append(codes, code)
			}
		}
		sort.Strings(codes)
		if len(codes) == 0 {
			return nil, "", fmt.Errorf("op %q models no error responses", r.Op)
		}
	default:
		codes = []string{status}
	}
	var out []map[string]any
	for _, code := range codes {
		resp, ok := responses[code].(map[string]any)
		if !ok {
			return nil, "", fmt.Errorf("op %q has no %s response", r.Op, code)
		}
		if ref, ok := resp["$ref"].(string); ok { // a components/responses reference
			t, err := pointer(doc, ref)
			if err != nil {
				return nil, "", err
			}
			resp, _ = t.(map[string]any)
		}
		ctMap, _ := resp["content"].(map[string]any)
		media, ok := ctMap[content].(map[string]any)
		if !ok {
			if r.status() == "errors" {
				continue // an error response with another media type
			}
			return nil, "", fmt.Errorf("op %q %s response has no %s content", r.Op, code, content)
		}
		schema, ok := media["schema"].(map[string]any)
		if !ok {
			return nil, "", fmt.Errorf("op %q %s %s has no schema", r.Op, code, content)
		}
		out = append(out, schema)
	}
	if len(out) == 0 {
		return nil, "", fmt.Errorf("op %q: no %s error schema", r.Op, content)
	}
	sel := fmt.Sprintf("%s -> %s %s", r.Op, strings.Join(codes, ","), content)
	return out, sel, nil
}

// walkRoot flattens one schema at "$" or, with events set and a union at
// the top, one "<event>:$" root per arm.
func (w *openAPIWalker) walkRoot(s map[string]any, events bool) {
	if events {
		base := s
		for i := 0; i < 8; i++ { // follow a chain of plain $refs to the union
			ref, ok := base["$ref"].(string)
			if !ok {
				break
			}
			t, err := w.resolve(ref)
			if err != nil {
				break
			}
			base = t
		}
		if arms, disc := unionOf(base); arms != nil {
			done := map[string]bool{}
			// The mapping can list events the oneOf omits (Cohere's
			// citation-start), so it goes first.
			mapping := discriminatorMapping(base)
			for _, name := range sortedKeys(mapping) {
				ref := mapping[name]
				w.b.event(name, false)
				w.walk(map[string]any{"$ref": ref}, name+":$", nil, nil, 0)
				done[ref] = true
			}
			for _, a := range arms {
				am, _ := a.(map[string]any)
				if r, ok := am["$ref"].(string); ok && done[r] {
					continue
				}
				name := w.variantName(am, disc)
				if name == "" {
					w.walk(am, "$", nil, nil, 0) // an arm with no event name
					continue
				}
				w.b.event(name, false)
				w.walk(am, name+":$", nil, nil, 0)
			}
			return
		}
	}
	w.walk(s, "$", nil, nil, 0)
}

// discriminatorMapping returns a union's discriminator mapping as value ->
// local ref (bare schema names are 3.0 shorthand for components/schemas).
func discriminatorMapping(s map[string]any) map[string]string {
	d, _ := s["discriminator"].(map[string]any)
	m, _ := d["mapping"].(map[string]any)
	out := make(map[string]string, len(m))
	for k, v := range m {
		ref, ok := v.(string)
		if !ok {
			continue
		}
		if !strings.HasPrefix(ref, "#") && !strings.Contains(ref, "/") {
			ref = "#/components/schemas/" + ref
		}
		out[k] = ref
	}
	return out
}

// unionOf returns a schema's oneOf (else anyOf) arms and its discriminator
// property, if any.
func unionOf(s map[string]any) ([]any, string) {
	disc := ""
	if d, ok := s["discriminator"].(map[string]any); ok {
		disc, _ = d["propertyName"].(string)
	}
	if v, ok := s["oneOf"].([]any); ok {
		return v, disc
	}
	if v, ok := s["anyOf"].([]any); ok {
		return v, disc
	}
	return nil, disc
}

// variantName finds an arm's discriminator value: its const, single-value
// enum or default on the property ("type" when none is declared).
func (w *openAPIWalker) variantName(v map[string]any, disc string) string {
	if disc == "" {
		disc = "type"
	}
	for i := 0; i < 8 && v != nil; i++ {
		if props, ok := v["properties"].(map[string]any); ok {
			if p, ok := props[disc].(map[string]any); ok {
				if ref, ok := p["$ref"].(string); ok {
					if t, err := w.resolve(ref); err == nil {
						p = t
					}
				}
				if c, ok := p["const"]; ok && c != nil {
					return fmt.Sprint(c)
				}
				if e, ok := p["enum"].([]any); ok && len(e) == 1 && e[0] != nil {
					return fmt.Sprint(e[0])
				}
				if d, ok := p["default"].(string); ok {
					return d
				}
			}
		}
		if all, ok := v["allOf"].([]any); ok {
			for _, a := range all {
				if am, ok := a.(map[string]any); ok {
					if n := w.variantName(am, disc); n != "" {
						return n
					}
				}
			}
		}
		ref, ok := v["$ref"].(string)
		if !ok {
			break
		}
		t, err := w.resolve(ref)
		if err != nil {
			break
		}
		v = t
	}
	return ""
}

// typeList reads "type" (a string or, in 3.1, a list) and "nullable".
func typeList(s map[string]any) (types []string, nullable bool) {
	switch t := s["type"].(type) {
	case string:
		types = []string{t}
	case []any:
		for _, x := range t {
			if xs, ok := x.(string); ok {
				types = append(types, xs)
			}
		}
	}
	out := types[:0]
	for _, t := range types {
		if t == "null" {
			nullable = true
			continue
		}
		out = append(out, t)
	}
	if b, ok := s["nullable"].(bool); ok && b {
		nullable = true
	}
	return out, nullable
}

func scalarString(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case nil:
		return ""
	default:
		return fmt.Sprint(x)
	}
}

// armLabel tags the paths an arm of the union at path contributes below it.
type armLabel struct{ at, text string }

// variant records the innermost union arm enclosing path (not the union at
// path itself, whose arms are listed by its own enum).
func (w *openAPIWalker) variant(path string, labels []armLabel) {
	if n := len(labels); n > 0 && labels[n-1].at != path {
		w.b.variant(path, labels[n-1].text)
	}
}

func (w *openAPIWalker) walk(s map[string]any, path string, refs []string, labels []armLabel, depth int) {
	if s == nil {
		return
	}
	b := w.b
	if depth > maxDepth {
		b.at(path).Recursive = "max-depth"
		return
	}
	if ref, ok := s["$ref"].(string); ok {
		name := ref[strings.LastIndexAny(ref, "/#")+1:]
		for _, r := range refs {
			if r == ref {
				b.at(path).Recursive = name
				return
			}
		}
		if same, ok := w.elide[ref]; ok && depth > 0 {
			w.variant(path, labels)
			p := b.at(path)
			p.SameAs = same
			p.Kinds = append(p.Kinds, "object")
			return
		}
		t, err := w.resolve(ref)
		if err != nil {
			p := b.at(path)
			p.Opaque = true
			p.Recursive = "unresolved " + ref
			return
		}
		merged := make(map[string]any, len(t)+len(s))
		for k, v := range t {
			merged[k] = v
		}
		for k, v := range s {
			if k != "$ref" {
				merged[k] = v
			}
		}
		w.walk(merged, path, append(refs, ref), labels, depth+1)
		return
	}
	w.variant(path, labels)
	if d, ok := s["deprecated"].(bool); ok && d {
		b.at(path).Deprecated = true
	}
	if why, ok := w.prune[path]; ok {
		w.walkPruned(s, path, why)
		return
	}
	types, nullable := typeList(s)
	if nullable {
		b.at(path).Nullable = true
	}
	for _, t := range types {
		b.kind(path, t)
	}
	if c, ok := s["const"]; ok {
		if c == nil {
			b.at(path).Nullable = true
		} else {
			b.enum(path, scalarString(c))
		}
	}
	if e, ok := s["enum"].([]any); ok {
		for _, v := range e {
			if v == nil {
				b.at(path).Nullable = true
				continue
			}
			b.enum(path, scalarString(v))
		}
	}
	if all, ok := s["allOf"].([]any); ok {
		for _, a := range all {
			if am, ok := a.(map[string]any); ok {
				w.walk(am, path, refs, labels, depth+1)
			}
		}
	}
	if arms, disc := unionOf(s); arms != nil {
		w.walkUnion(s, arms, disc, path, refs, labels, depth)
	}

	props, hasProps := s["properties"].(map[string]any)
	if hasProps {
		b.kind(path, "object")
		for _, k := range sortedKeys(props) {
			pm, ok := props[k].(map[string]any)
			if !ok {
				continue
			}
			if wo, ok := pm["writeOnly"].(bool); ok && wo {
				continue
			}
			w.walk(pm, path+"."+k, refs, labels, depth+1)
		}
	}
	switch ap := s["additionalProperties"].(type) {
	case map[string]any:
		b.kind(path, "map")
		if len(ap) == 0 {
			if !hasProps {
				b.at(path).Opaque = true
			}
		} else {
			w.walk(ap, path+".*", refs, labels, depth+1)
		}
	case bool:
		if ap && !hasProps {
			b.kind(path, "map")
			b.at(path).Opaque = true
		}
	}
	if items, ok := s["items"].(map[string]any); ok {
		b.kind(path, "array")
		w.walk(items, path+"[]", refs, labels, depth+1)
	}
	if prefix, ok := s["prefixItems"].([]any); ok {
		b.kind(path, "array")
		for _, it := range prefix {
			if im, ok := it.(map[string]any); ok {
				w.walk(im, path+"[]", refs, labels, depth+1)
			}
		}
	}
	structural := hasProps || s["items"] != nil || s["prefixItems"] != nil || s["allOf"] != nil ||
		s["oneOf"] != nil || s["anyOf"] != nil || s["additionalProperties"] != nil
	switch {
	case len(types) == 0 && !structural && s["enum"] == nil && s["const"] == nil:
		// Nothing constrains the value: any JSON.
		b.kind(path, "any")
		b.at(path).Opaque = true
	case len(types) == 1 && types[0] == "object" && !structural:
		b.at(path).Opaque = true // a free-form object
	}
}

// walkPruned records a pruned path's own kinds and nullability, and nothing
// below it.
func (w *openAPIWalker) walkPruned(s map[string]any, path, why string) {
	p := w.b.at(path)
	p.Pruned = why
	kinds, nullable := w.ownKinds(s, 0)
	p.Kinds = append(p.Kinds, kinds...)
	p.Nullable = p.Nullable || nullable
}

// ownKinds is the kinds a schema allows at its own level, through $refs,
// unions and allOf.
func (w *openAPIWalker) ownKinds(s map[string]any, depth int) (kinds []string, nullable bool) {
	if s == nil || depth > 8 {
		return nil, false
	}
	if ref, ok := s["$ref"].(string); ok {
		t, err := w.resolve(ref)
		if err != nil {
			return nil, false
		}
		return w.ownKinds(t, depth+1)
	}
	kinds, nullable = typeList(s)
	if s["properties"] != nil {
		kinds = append(kinds, "object")
	}
	if s["items"] != nil {
		kinds = append(kinds, "array")
	}
	var arms []any
	if a, ok := s["oneOf"].([]any); ok {
		arms = append(arms, a...)
	}
	if a, ok := s["anyOf"].([]any); ok {
		arms = append(arms, a...)
	}
	if a, ok := s["allOf"].([]any); ok {
		arms = append(arms, a...)
	}
	for _, a := range arms {
		if am, ok := a.(map[string]any); ok {
			k, n := w.ownKinds(am, depth+1)
			kinds = append(kinds, k...)
			nullable = nullable || n
		}
	}
	return kinds, nullable
}

func (w *openAPIWalker) walkUnion(s map[string]any, arms []any, disc, path string, refs []string, labels []armLabel, depth int) {
	b := w.b
	byRef := map[string]string{} // ref -> discriminator value
	for name, ref := range discriminatorMapping(s) {
		byRef[ref] = name
	}
	enumArm, stringArm := false, false
	for _, a := range arms {
		am, ok := a.(map[string]any)
		if !ok {
			continue
		}
		if t, n := typeList(am); len(t) == 0 && n && am["properties"] == nil && am["$ref"] == nil {
			b.at(path).Nullable = true // the {"type":"null"} arm
			continue
		}
		switch w.armKind(am) {
		case "plain-string":
			stringArm = true
		case "enum":
			enumArm = true
		}
		armLabels := labels
		name := ""
		if r, ok := am["$ref"].(string); ok {
			name = byRef[r]
		}
		if name == "" && disc != "" {
			name = w.variantName(am, disc)
		}
		if name != "" && disc != "" {
			b.enum(path+"."+disc, name)
			armLabels = append(append([]armLabel(nil), labels...), armLabel{at: path, text: disc + "=" + name})
		}
		w.walk(am, path, refs, armLabels, depth+1)
	}
	if stringArm && enumArm {
		b.at(path).OpenEnum = true
	}
}

// armKind classifies a union arm (after following $refs): "plain-string"
// for an unconstrained string, "enum" for a string with listed values.
func (w *openAPIWalker) armKind(v map[string]any) string {
	for i := 0; i < 8 && v != nil; i++ {
		ref, ok := v["$ref"].(string)
		if !ok {
			break
		}
		t, err := w.resolve(ref)
		if err != nil {
			return ""
		}
		v = t
	}
	types, _ := typeList(v)
	stringish := len(types) == 0 || (len(types) == 1 && types[0] == "string")
	switch {
	case !stringish:
		return ""
	case v["enum"] != nil || v["const"] != nil:
		return "enum" // {const: "x"} with no type counts: Anthropic's Model arms
	case len(types) == 1:
		return "plain-string"
	}
	return ""
}

// openAPIMeta returns the version fields a provenance record keeps.
func openAPIMeta(doc map[string]any) map[string]string {
	m := map[string]string{}
	if v, ok := doc["openapi"]; ok {
		m["openapi"] = scalarString(v)
	}
	if info, ok := doc["info"].(map[string]any); ok {
		if v, ok := info["version"]; ok {
			m["info.version"] = scalarString(v)
		}
	}
	return m
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
