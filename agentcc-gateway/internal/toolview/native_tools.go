package toolview

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"io"
)

// nativeNode retains private byte spans. It never exposes a payload edit handle.
type nativeNode struct {
	start, end, member int
	kind               byte
	text               string
	keys               []string
	children           []*nativeNode
}

func (n *nativeNode) field(key string) *nativeNode {
	if n == nil {
		return nil
	}
	for i, k := range n.keys {
		if k == key {
			return n.children[i]
		}
	}
	return nil
}
func (n *nativeNode) str() string {
	if n == nil || n.kind != '"' {
		return ""
	}
	return n.text
}
func parseNative(ctx context.Context, body []byte) (*nativeNode, error) {
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	n, err := readNative(ctx, d, body, 0)
	if err != nil || n.kind != '{' {
		return nil, errNative
	}
	if _, err = d.Token(); err != io.EOF {
		return nil, errNative
	}
	return n, nil
}
func nativeStart(body []byte, i int) int {
	for i < len(body) && (body[i] == ' ' || body[i] == '\n' || body[i] == '\r' || body[i] == '\t' || body[i] == ',' || body[i] == ':') {
		i++
	}
	return i
}
func readNative(ctx context.Context, d *json.Decoder, body []byte, depth int) (*nativeNode, error) {
	if ctx.Err() != nil || depth > requestview.MaxDepth {
		return nil, errNative
	}
	start := nativeStart(body, int(d.InputOffset()))
	tok, err := d.Token()
	if err != nil || start >= len(body) {
		return nil, errNative
	}
	n := &nativeNode{start: start, member: start, kind: body[start]}
	switch v := tok.(type) {
	case json.Delim:
		if v != '{' && v != '[' {
			return nil, errNative
		}
		seen := map[string]bool{}
		for d.More() {
			member := nativeStart(body, int(d.InputOffset()))
			if v == '{' {
				k, e := d.Token()
				key, ok := k.(string)
				if e != nil || !ok || seen[key] {
					return nil, errNative
				}
				seen[key] = true
				n.keys = append(n.keys, key)
			}
			child, e := readNative(ctx, d, body, depth+1)
			if e != nil {
				return nil, errNative
			}
			child.member = member
			n.children = append(n.children, child)
		}
		close, e := d.Token()
		if e != nil || (v == '{' && close != json.Delim('}')) || (v == '[' && close != json.Delim(']')) {
			return nil, errNative
		}
	case string:
		n.text = v
	}
	n.end = int(d.InputOffset())
	return n, nil
}
func (r *NativeRequest) describe(s *Snapshot) error {
	tools := s.root.field("tools")
	if tools == nil || tools.kind == 'n' {
		return nil
	}
	items, ok := nativeList(tools, r.protocol == models.NativeGenAI)
	if !ok {
		return errNative
	}
	seen := map[string]bool{}
	add := func(node *nativeNode, name string, kind Kind) error {
		key := string(rune(kind)) + name
		if name == "" || seen[key] || kind == Unknown {
			return errNative
		}
		seen[key] = true
		s.tools = append(s.tools, Descriptor{id: ToolID(len(s.tools) + 1), policyName: name, kind: kind})
		s.declarations = append(s.declarations, node)
		return nil
	}
	for _, tool := range items {
		if tool.kind != '{' {
			return errNative
		}
		if r.protocol == models.NativeGenAI {
			if len(tool.keys) == 0 {
				return errNative
			}
			aliases := map[string]bool{}
			for i, key := range tool.keys {
				node := tool.children[i]
				if key == "functionDeclarations" || key == "function_declarations" {
					functions, ok := nativeList(node, true)
					if aliases["functions"] || !ok {
						return errNative
					}
					aliases["functions"] = true
					for _, fn := range functions {
						if fn.kind != '{' || add(fn, fn.field("name").str(), Function) != nil {
							return errNative
						}
					}
				} else {
					canonical := models.CanonicalCapability(key)
					if !models.IsBuiltinCapability(key) || node.kind != '{' || aliases[canonical] {
						return errNative
					}
					aliases[canonical] = true
					if err := add(node, canonical, Builtin); err != nil {
						return err
					}
				}
			}
			continue
		}
		typ := tool.field("type").str()
		name := tool.field("name").str()
		switch r.protocol {
		case models.NativeMessages:
			if (typ == "" && tool.field("type") == nil) || typ == "custom" {
				if schema := tool.field("input_schema"); schema == nil || schema.kind != '{' || add(tool, name, Function) != nil {
					return errNative
				}
				continue
			}
		case models.NativeResponses:
			if typ == "function" {
				if tool.field("function") != nil || add(tool, name, Function) != nil {
					return errNative
				}
				continue
			}
		default:
			return errNative
		}
		identity, ok := nativeBuiltinIdentity(r.protocol, tool)
		if !ok || add(tool, identity, Builtin) != nil {
			return errNative
		}
	}
	return nil
}

// Only Gemini accepts an object in place of a repeated tool/declaration field.
// Nodes retain their original spans; the list is a traversal aid, not a rewrite.
func nativeList(n *nativeNode, singleton bool) ([]*nativeNode, bool) {
	if n != nil {
		if n.kind == '[' {
			return n.children, true
		}
		if singleton && n.kind == '{' {
			return []*nativeNode{n}, true
		}
	}
	return nil, false
}

func filterNative(body []byte, root *nativeNode, removed map[*nativeNode]bool) ([]byte, bool) {
	var render func(*nativeNode, bool) ([]byte, bool, bool)
	render = func(n *nativeNode, dropEmpty bool) ([]byte, bool, bool) {
		if removed[n] {
			return nil, true, true
		}
		if len(n.children) == 0 {
			return body[n.start:n.end], false, false
		}
		parts := make([][]byte, len(n.children))
		gone := make([]bool, len(parts))
		changed := false
		count := 0
		for i, c := range n.children {
			prune := true
			if n == root && n.keys[i] == "tools" && c.kind == '[' {
				// Keep an empty tools array, but remove an exhausted singleton
				// object: tools:{} is not a valid declaration group.
				prune = false
			}
			var update bool
			parts[i], update, gone[i] = render(c, prune)
			changed = changed || update
			if !gone[i] {
				count++
			}
		}
		if !changed {
			return body[n.start:n.end], false, false
		}
		if count == 0 && dropEmpty {
			return nil, true, true
		}
		out := append([]byte(nil), body[n.start:n.start+1]...)
		first := true
		for i, c := range n.children {
			if gone[i] {
				continue
			}
			if first {
				out = append(out, body[n.start+1:n.children[0].member]...)
				first = false
			} else {
				out = append(out, body[n.children[i-1].end:c.member]...)
			}
			out = append(out, body[c.member:c.start]...)
			out = append(out, parts[i]...)
		}
		out = append(out, body[n.children[len(n.children)-1].end:n.end]...)
		return out, true, false
	}
	out, _, gone := render(root, false)
	if gone {
		return nil, false
	}
	final := append([]byte(nil), body[:root.start]...)
	final = append(final, out...)
	final = append(final, body[root.end:]...)
	return final, true
}

// RewriteModel changes only the model value span; unrelated native bytes are
// never round-tripped through a map. No tool schema or capability is inferred.
func RewriteModel(body []byte, model string) ([]byte, error) {
	d := json.NewDecoder(bytes.NewReader(body))
	if tok, err := d.Token(); err != nil || tok != json.Delim('{') {
		return nil, errNative
	}
	start, end := -1, -1
	for d.More() {
		tok, err := d.Token()
		key, ok := tok.(string)
		if err != nil || !ok {
			return nil, errNative
		}
		at := nativeStart(body, int(d.InputOffset()))
		var raw json.RawMessage
		if d.Decode(&raw) != nil {
			return nil, errNative
		}
		if key == "model" {
			if start >= 0 {
				return nil, errNative
			}
			start, end = at, int(d.InputOffset())
		}
	}
	if tok, err := d.Token(); err != nil || tok != json.Delim('}') {
		return nil, errNative
	}
	if _, err := d.Token(); err != io.EOF || start < 0 {
		return nil, errNative
	}
	encoded, _ := json.Marshal(model)
	out := append([]byte(nil), body[:start]...)
	out = append(out, encoded...)
	out = append(out, body[end:]...)
	return out, nil
}
