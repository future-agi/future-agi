package manifest

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"regexp"
	"strconv"

	"gopkg.in/yaml.v3"
)

// A decoded document is a JSON value: map[string]any, []any, string, bool,
// int64, float64 or nil. The shape check refuses nil wherever the schema
// does; only a free JSON Schema document may hold it.

var decimalInt = regexp.MustCompile(`^-?(0|[1-9][0-9]*)$`)

// yaml11Bool is a plain scalar a YAML 1.1 reader (PyYAML, for one) takes as a
// boolean although YAML 1.2 reads text; such a key or value is quoted, so
// every reader of a record sees the same data (retry's "on" key, for one).
var yaml11Bool = regexp.MustCompile(`^(?i:y|n|yes|no|on|off)$`)

// decodeYAML reads one YAML document strictly: a single document whose root
// is a mapping, string keys, no duplicate keys, no anchors, aliases, merge
// keys or explicit tags, decimal integers and finite floats. An unquoted
// date stays the text it is.
func decodeYAML(data []byte) (any, error) {
	dec := yaml.NewDecoder(bytes.NewReader(data))
	var doc yaml.Node
	if err := dec.Decode(&doc); err != nil {
		if errors.Is(err, io.EOF) {
			return nil, errors.New("is empty")
		}
		return nil, fmt.Errorf("is not valid YAML: %v", err)
	}
	var next yaml.Node
	if err := dec.Decode(&next); !errors.Is(err, io.EOF) {
		return nil, errors.New("holds more than one YAML document")
	}
	if len(doc.Content) != 1 || doc.Content[0].Kind != yaml.MappingNode {
		return nil, errors.New("must be a YAML mapping")
	}
	return yamlValue(doc.Content[0], "")
}

func yamlValue(n *yaml.Node, path string) (any, error) {
	fail := func(format string, args ...any) error {
		return fmt.Errorf("%s: %s (line %d)", pointer(path), fmt.Sprintf(format, args...), n.Line)
	}
	if n.Kind == yaml.AliasNode || n.Anchor != "" {
		return nil, fail("anchors and aliases are not allowed")
	}
	if n.Style&yaml.TaggedStyle != 0 {
		return nil, fail("explicit tags are not allowed")
	}
	switch n.Kind {
	case yaml.MappingNode:
		m := make(map[string]any, len(n.Content)/2)
		for i := 0; i+1 < len(n.Content); i += 2 {
			k, v := n.Content[i], n.Content[i+1]
			if k.Kind != yaml.ScalarNode || k.Tag != "!!str" || k.Style&yaml.TaggedStyle != 0 {
				return nil, fail("keys must be plain strings")
			}
			if k.Style == 0 && yaml11Bool.MatchString(k.Value) {
				return nil, fmt.Errorf("%s: write the key %q in quotes; a YAML 1.1 reader takes it as a boolean (line %d)", pointer(path+"/"+k.Value), k.Value, k.Line)
			}
			if _, dup := m[k.Value]; dup {
				return nil, fmt.Errorf("%s: duplicate key (line %d)", pointer(path+"/"+k.Value), k.Line)
			}
			val, err := yamlValue(v, path+"/"+k.Value)
			if err != nil {
				return nil, err
			}
			m[k.Value] = val
		}
		return m, nil
	case yaml.SequenceNode:
		out := make([]any, 0, len(n.Content))
		for i, c := range n.Content {
			val, err := yamlValue(c, fmt.Sprintf("%s/%d", path, i))
			if err != nil {
				return nil, err
			}
			out = append(out, val)
		}
		return out, nil
	case yaml.ScalarNode:
		switch n.Tag {
		case "!!str", "!!timestamp":
			if n.Style == 0 && yaml11Bool.MatchString(n.Value) {
				return nil, fail("write %q in quotes; a YAML 1.1 reader takes it as a boolean", n.Value)
			}
			return n.Value, nil
		case "!!null":
			return nil, nil
		case "!!bool":
			var b bool
			if err := n.Decode(&b); err != nil {
				return nil, fail("%v", err)
			}
			return b, nil
		case "!!int":
			if !decimalInt.MatchString(n.Value) {
				return nil, fail("integers are written in decimal")
			}
			i, err := strconv.ParseInt(n.Value, 10, 64)
			if err != nil {
				return nil, fail("%v", err)
			}
			return i, nil
		case "!!float":
			f, err := strconv.ParseFloat(n.Value, 64)
			if err != nil || math.IsInf(f, 0) || math.IsNaN(f) {
				return nil, fail("numbers must be finite")
			}
			return f, nil
		}
		return nil, fail("tag %s is not allowed", n.Tag)
	}
	return nil, fail("unsupported YAML node")
}

// decodeJSON reads one JSON value strictly: duplicate keys and trailing data
// are refused, and numbers become int64 or float64.
func decodeJSON(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	v, err := jsonValue(dec, "")
	if err != nil {
		return nil, err
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, errors.New("holds data after its JSON value")
	}
	return v, nil
}

func jsonValue(dec *json.Decoder, path string) (any, error) {
	tok, err := dec.Token()
	if err != nil {
		return nil, fmt.Errorf("%s: is not valid JSON: %v", pointer(path), err)
	}
	switch t := tok.(type) {
	case json.Delim:
		switch t {
		case '{':
			m := map[string]any{}
			for dec.More() {
				kt, err := dec.Token()
				if err != nil {
					return nil, fmt.Errorf("%s: is not valid JSON: %v", pointer(path), err)
				}
				k := kt.(string)
				if _, dup := m[k]; dup {
					return nil, fmt.Errorf("%s: duplicate key", pointer(path+"/"+k))
				}
				if m[k], err = jsonValue(dec, path+"/"+k); err != nil {
					return nil, err
				}
			}
			_, err := dec.Token() // the closing brace
			return m, err
		case '[':
			out := []any{}
			for i := 0; dec.More(); i++ {
				v, err := jsonValue(dec, fmt.Sprintf("%s/%d", path, i))
				if err != nil {
					return nil, err
				}
				out = append(out, v)
			}
			_, err := dec.Token()
			return out, err
		}
	case json.Number:
		if decimalInt.MatchString(t.String()) {
			if i, err := t.Int64(); err == nil {
				return i, nil
			}
		}
		f, err := t.Float64()
		if err != nil {
			return nil, fmt.Errorf("%s: %v", pointer(path), err)
		}
		return f, nil
	case string, bool, nil:
		return t, nil
	}
	return nil, fmt.Errorf("%s: unexpected JSON token %v", pointer(path), tok)
}

// pointer renders a JSON pointer, "/" for the root.
func pointer(path string) string {
	if path == "" {
		return "/"
	}
	return path
}
