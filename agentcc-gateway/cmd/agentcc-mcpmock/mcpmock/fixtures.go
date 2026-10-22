package mcpmock

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
)

// Mock-only keys in the fixture files. They shape the mock's answers and are
// stripped from every definition the mock lists.
const (
	keyPriceUSD = "price_usd" // tools.json: the tool's upstream price per call
	keyResult   = "result"    // tools.json: a canned CallToolResult
	keyText     = "text"      // resources.json: the resource's text content
	keyMessages = "messages"  // prompts.json: the prompt's messages
)

// Tool is one fixture tool: its MCP definition as listed, and the mock-only
// price and canned result.
type Tool struct {
	Name     string
	Def      json.RawMessage
	PriceUSD float64
	Result   json.RawMessage
}

// Resource is one fixture resource: its listed definition and its text.
type Resource struct {
	URI  string
	Def  json.RawMessage
	Text string
	Mime string
}

// Prompt is one fixture prompt: its listed definition and its messages.
type Prompt struct {
	Name     string
	Def      json.RawMessage
	Messages json.RawMessage
}

// Fixtures is what --fixtures loads. A missing file is an empty list.
type Fixtures struct {
	Tools     []Tool
	Resources []Resource
	Prompts   []Prompt
}

// LoadFixtures reads tools.json, resources.json and prompts.json from dir.
// An empty dir loads nothing.
func LoadFixtures(dir string) (*Fixtures, error) {
	fix := &Fixtures{}
	if dir == "" {
		return fix, nil
	}
	read := func(name string) ([]byte, error) {
		data, err := os.ReadFile(filepath.Join(dir, name))
		if errors.Is(err, fs.ErrNotExist) {
			return nil, nil
		}
		return data, err
	}
	var err error
	if data, rerr := read("tools.json"); rerr != nil {
		return nil, rerr
	} else if data != nil {
		if fix.Tools, err = ParseTools(data); err != nil {
			return nil, fmt.Errorf("tools.json: %w", err)
		}
	}
	if data, rerr := read("resources.json"); rerr != nil {
		return nil, rerr
	} else if data != nil {
		if fix.Resources, err = parseResources(data); err != nil {
			return nil, fmt.Errorf("resources.json: %w", err)
		}
	}
	if data, rerr := read("prompts.json"); rerr != nil {
		return nil, rerr
	} else if data != nil {
		if fix.Prompts, err = parsePrompts(data); err != nil {
			return nil, fmt.Errorf("prompts.json: %w", err)
		}
	}
	return fix, nil
}

// ParseTools reads a tool list: a JSON array of tool objects, or an object
// with a "tools" array (the tools/list result shape, which /__control/tools
// also accepts). Each tool needs a unique, non-empty name.
func ParseTools(data []byte) ([]Tool, error) {
	items, err := objectList(data, "tools")
	if err != nil {
		return nil, err
	}
	tools := make([]Tool, 0, len(items))
	seen := map[string]bool{}
	for i, obj := range items {
		var t Tool
		if err := stringField(obj, "name", &t.Name); err != nil || t.Name == "" {
			return nil, fmt.Errorf("tool %d: a non-empty string name is required", i)
		}
		if seen[t.Name] {
			return nil, fmt.Errorf("tool %q is listed twice", t.Name)
		}
		seen[t.Name] = true
		if raw, ok := obj[keyPriceUSD]; ok {
			if err := json.Unmarshal(raw, &t.PriceUSD); err != nil || t.PriceUSD < 0 {
				return nil, fmt.Errorf("tool %q: price_usd must be a number >= 0", t.Name)
			}
		}
		t.Result = obj[keyResult]
		if _, ok := obj["inputSchema"]; !ok {
			obj["inputSchema"] = json.RawMessage(`{"type":"object"}`)
		}
		if t.Def, err = marshalWithout(obj, keyPriceUSD, keyResult); err != nil {
			return nil, err
		}
		tools = append(tools, t)
	}
	return tools, nil
}

func parseResources(data []byte) ([]Resource, error) {
	items, err := objectList(data, "resources")
	if err != nil {
		return nil, err
	}
	out := make([]Resource, 0, len(items))
	for i, obj := range items {
		var r Resource
		if err := stringField(obj, "uri", &r.URI); err != nil || r.URI == "" {
			return nil, fmt.Errorf("resource %d: a non-empty string uri is required", i)
		}
		if raw, ok := obj[keyText]; ok {
			if err := json.Unmarshal(raw, &r.Text); err != nil {
				return nil, fmt.Errorf("resource %q: text must be a string", r.URI)
			}
		}
		_ = stringField(obj, "mimeType", &r.Mime)
		if r.Def, err = marshalWithout(obj, keyText); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, nil
}

func parsePrompts(data []byte) ([]Prompt, error) {
	items, err := objectList(data, "prompts")
	if err != nil {
		return nil, err
	}
	out := make([]Prompt, 0, len(items))
	for i, obj := range items {
		var p Prompt
		if err := stringField(obj, "name", &p.Name); err != nil || p.Name == "" {
			return nil, fmt.Errorf("prompt %d: a non-empty string name is required", i)
		}
		p.Messages = obj[keyMessages]
		if p.Def, err = marshalWithout(obj, keyMessages); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, nil
}

// objectList decodes a JSON array of objects, or an object holding one
// under key: a fixture wrapper, or a whole list result such as tools/list's,
// whose other members (nextCursor, resultType, ttlMs, cacheScope) are
// ignored. An object without key is refused, so a misspelt wrapper never
// reads as an empty list.
func objectList(data []byte, key string) ([]map[string]json.RawMessage, error) {
	var items []map[string]json.RawMessage
	if err := json.Unmarshal(data, &items); err == nil {
		return items, nil
	}
	var wrapped map[string]json.RawMessage
	if err := json.Unmarshal(data, &wrapped); err != nil {
		return nil, fmt.Errorf("want a JSON array of objects or {%q: [...]}: %w", key, err)
	}
	raw, ok := wrapped[key]
	if !ok {
		return nil, fmt.Errorf("want a JSON array of objects or {%q: [...]}: the object has no %q", key, key)
	}
	if err := json.Unmarshal(raw, &items); err != nil {
		return nil, fmt.Errorf("%q: want an array of objects: %w", key, err)
	}
	return items, nil
}

func stringField(obj map[string]json.RawMessage, key string, dst *string) error {
	raw, ok := obj[key]
	if !ok {
		return nil
	}
	return json.Unmarshal(raw, dst)
}

func marshalWithout(obj map[string]json.RawMessage, keys ...string) (json.RawMessage, error) {
	clean := make(map[string]json.RawMessage, len(obj))
	for k, v := range obj {
		clean[k] = v
	}
	for _, k := range keys {
		delete(clean, k)
	}
	return json.Marshal(clean)
}
