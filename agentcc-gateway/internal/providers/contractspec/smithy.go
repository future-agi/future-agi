package contractspec

import (
	"fmt"
	"strings"
)

// smithyWalker flattens Smithy 2.0 JSON AST models (aws/api-models-aws) as
// their JSON bodies serialize under the AWS JSON protocols:
//
//   - structure: members are keys, smithy.api#jsonName overriding the member
//     name. Members bound to the HTTP status, headers, query or labels are
//     not in the body and are skipped. A member bound with httpPayload *is*
//     the body.
//   - union: an object with exactly one member set. The member names become
//     the enum of the pseudo-path "<path>.<union>" (so a new member is a new
//     enum value, and an EnumCheck can require each member handled), and
//     each member's path is tagged "<union>=<member>".
//   - list and set step into "[]", map into ".*".
//   - enum: values from smithy.api#enumValue (or the member name); the
//     Smithy 1.0 smithy.api#enum trait on a string is read too.
//   - document is opaque. blob is base64 text, or the raw body when bound
//     with httpPayload.
//   - a union with smithy.api#streaming is an event stream: each member is
//     an event (the :event-type header) flattened under "<member>:$", and a
//     member whose target has smithy.api#error is an exception event.
//
// Prelude shapes (smithy.api#String, ...) map to scalars.
type smithyWalker struct {
	shapes map[string]any
	b      *builder
}

// unionMemberKey is the pseudo-key under which a Smithy union's member names
// are recorded as an enum, so a new member is reported like a new enum value.
const unionMemberKey = "<union>"

var smithyPrelude = map[string]string{
	"String": "string", "Boolean": "boolean", "PrimitiveBoolean": "boolean",
	"Byte": "integer", "Short": "integer", "Integer": "integer", "Long": "integer",
	"PrimitiveByte": "integer", "PrimitiveShort": "integer", "PrimitiveInteger": "integer", "PrimitiveLong": "integer",
	"BigInteger": "integer", "Float": "number", "Double": "number", "PrimitiveFloat": "number",
	"PrimitiveDouble": "number", "BigDecimal": "number", "Timestamp": "timestamp", "Blob": "blob",
	"Document": "any", "Unit": "unit",
}

var smithyHTTPBindings = []string{
	"smithy.api#httpHeader", "smithy.api#httpResponseCode", "smithy.api#httpQuery",
	"smithy.api#httpQueryParams", "smithy.api#httpLabel", "smithy.api#httpPrefixHeaders",
}

func traitsOf(m map[string]any) map[string]any {
	t, _ := m["traits"].(map[string]any)
	return t
}

func (w *smithyWalker) shape(id string) (map[string]any, bool) {
	s, ok := w.shapes[id].(map[string]any)
	return s, ok
}

// walkOperation flattens an operation's output body. With events set and
// the body bound to a streaming union, each event becomes its own root.
func (w *smithyWalker) walkOperation(opID string, events bool) (string, error) {
	op, ok := w.shape(opID)
	if !ok || op["type"] != "operation" {
		return "", fmt.Errorf("shape %q is not an operation", opID)
	}
	out, _ := op["output"].(map[string]any)
	outID, _ := out["target"].(string)
	if outID == "" {
		return "", fmt.Errorf("operation %q has no output", opID)
	}
	sel := fmt.Sprintf("operation %s -> output %s", opID, outID)
	shape, ok := w.shape(outID)
	if !ok {
		return "", fmt.Errorf("output shape %q not in model", outID)
	}
	members, _ := shape["members"].(map[string]any)
	for _, name := range sortedKeys(members) {
		m, _ := members[name].(map[string]any)
		if _, ok := traitsOf(m)["smithy.api#httpPayload"]; !ok {
			continue
		}
		target, _ := m["target"].(string)
		sel += " (httpPayload " + name + " -> " + target + ")"
		ts, _ := w.shape(target)
		if _, streaming := traitsOf(ts)["smithy.api#streaming"]; streaming && events && ts["type"] == "union" {
			w.walkEvents(target)
			return sel + ", events", nil
		}
		w.walk(target, "$", nil, 0)
		return sel, nil
	}
	w.walk(outID, "$", nil, 0)
	return sel, nil
}

// walkErrors flattens every error shape an operation declares, each under
// "<ErrorShapeName>:$". The shape name is what AWS sends as the error type
// (the x-amzn-ErrorType header), so a new one is reported as a new event.
func (w *smithyWalker) walkErrors(opID string) (string, error) {
	op, ok := w.shape(opID)
	if !ok || op["type"] != "operation" {
		return "", fmt.Errorf("shape %q is not an operation", opID)
	}
	errs, _ := op["errors"].([]any)
	if len(errs) == 0 {
		return "", fmt.Errorf("operation %q declares no errors", opID)
	}
	for _, e := range errs {
		em, _ := e.(map[string]any)
		id, _ := em["target"].(string)
		name := id[strings.LastIndex(id, "#")+1:]
		w.b.event(name, true)
		w.walk(id, name+":$", nil, 0)
	}
	return fmt.Sprintf("operation %s -> errors", opID), nil
}

func (w *smithyWalker) walkEvents(unionID string) {
	u, _ := w.shape(unionID)
	members, _ := u["members"].(map[string]any)
	for _, ev := range sortedKeys(members) {
		m, _ := members[ev].(map[string]any)
		target, _ := m["target"].(string)
		ts, _ := w.shape(target)
		_, isErr := traitsOf(ts)["smithy.api#error"]
		w.b.event(ev, isErr)
		w.walk(target, ev+":$", nil, 0)
	}
}

func (w *smithyWalker) walk(id, path string, refs []string, depth int) {
	b := w.b
	if depth > maxDepth {
		b.at(path).Recursive = "max-depth"
		return
	}
	if strings.HasPrefix(id, "smithy.api#") {
		switch t := smithyPrelude[strings.TrimPrefix(id, "smithy.api#")]; t {
		case "any":
			b.kind(path, "any")
			b.at(path).Opaque = true
		case "unit", "":
		default:
			b.kind(path, t)
		}
		return
	}
	for _, r := range refs {
		if r == id {
			b.at(path).Recursive = id[strings.LastIndex(id, "#")+1:]
			return
		}
	}
	shape, ok := w.shape(id)
	if !ok {
		p := b.at(path)
		p.Opaque = true
		p.Recursive = "unresolved " + id
		return
	}
	refs = append(refs, id)
	if _, ok := traitsOf(shape)["smithy.api#deprecated"]; ok {
		b.at(path).Deprecated = true
	}
	typ, _ := shape["type"].(string)
	switch typ {
	case "structure", "union":
		b.kind(path, "object")
		members, _ := shape["members"].(map[string]any)
		for _, name := range sortedKeys(members) {
			m, _ := members[name].(map[string]any)
			tr := traitsOf(m)
			bound := false
			for _, t := range smithyHTTPBindings {
				if _, ok := tr[t]; ok {
					bound = true
				}
			}
			if bound {
				continue
			}
			key := name
			if jn, ok := tr["smithy.api#jsonName"].(string); ok {
				key = jn
			}
			if _, ok := tr["smithy.api#deprecated"]; ok {
				b.at(path + "." + key).Deprecated = true
			}
			if typ == "union" {
				b.enum(path+"."+unionMemberKey, key)
				b.variant(path+"."+key, unionMemberKey+"="+key)
			}
			target, _ := m["target"].(string)
			w.walk(target, path+"."+key, refs, depth+1)
		}
	case "list", "set":
		b.kind(path, "array")
		if m, ok := shape["member"].(map[string]any); ok {
			t, _ := m["target"].(string)
			w.walk(t, path+"[]", refs, depth+1)
		}
	case "map":
		b.kind(path, "map")
		if m, ok := shape["value"].(map[string]any); ok {
			t, _ := m["target"].(string)
			w.walk(t, path+".*", refs, depth+1)
		}
	case "enum":
		b.kind(path, "string")
		members, _ := shape["members"].(map[string]any)
		for _, name := range sortedKeys(members) {
			m, _ := members[name].(map[string]any)
			v, ok := traitsOf(m)["smithy.api#enumValue"].(string)
			if !ok {
				v = name
			}
			b.enum(path, v)
		}
	case "intEnum":
		b.kind(path, "integer")
	case "document":
		b.kind(path, "any")
		b.at(path).Opaque = true
	case "string":
		b.kind(path, "string")
		if e, ok := traitsOf(shape)["smithy.api#enum"].([]any); ok { // Smithy 1.0 enum trait
			for _, x := range e {
				if xm, ok := x.(map[string]any); ok {
					if v, ok := xm["value"].(string); ok {
						b.enum(path, v)
					}
				}
			}
		}
	case "blob", "timestamp", "boolean":
		b.kind(path, typ)
	case "byte", "short", "integer", "long", "bigInteger":
		b.kind(path, "integer")
	case "float", "double", "bigDecimal":
		b.kind(path, "number")
	default:
		b.kind(path, typ)
	}
}

func smithyMeta(doc map[string]any) map[string]string {
	m := map[string]string{}
	if v, ok := doc["smithy"]; ok {
		m["smithy"] = scalarString(v)
	}
	shapes, _ := doc["shapes"].(map[string]any)
	for _, id := range sortedKeys(shapes) {
		if s, ok := shapes[id].(map[string]any); ok && s["type"] == "service" {
			m["service"] = id
			m["service.version"] = scalarString(s["version"])
		}
	}
	return m
}
