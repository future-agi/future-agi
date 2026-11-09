package canonjson

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

var decimalInt = regexp.MustCompile(`^-?(0|[1-9][0-9]*)$`)

// Parse reads one JSON value strictly, keeping member order: duplicate keys,
// trailing data and non-finite numbers are refused; a decimal integer that
// fits an int64 is an int64, any other number a float64.
func Parse(data []byte) (any, error) {
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
		if t == '{' {
			obj := Object{}
			for dec.More() {
				kt, err := dec.Token()
				if err != nil {
					return nil, fmt.Errorf("%s: is not valid JSON: %v", pointer(path), err)
				}
				k := kt.(string)
				if _, dup := obj.Get(k); dup {
					return nil, fmt.Errorf("%s: duplicate key", pointer(path+"/"+k))
				}
				v, err := jsonValue(dec, path+"/"+k)
				if err != nil {
					return nil, err
				}
				obj = append(obj, Member{k, v})
			}
			_, err := dec.Token()
			return obj, err
		}
		arr := []any{}
		for i := 0; dec.More(); i++ {
			v, err := jsonValue(dec, fmt.Sprintf("%s/%d", path, i))
			if err != nil {
				return nil, err
			}
			arr = append(arr, v)
		}
		_, err := dec.Token()
		return arr, err
	case json.Number:
		return number(t.String(), path)
	case string, bool, nil:
		return t, nil
	}
	return nil, fmt.Errorf("%s: unexpected JSON token %v", pointer(path), tok)
}

func number(s, path string) (any, error) {
	if decimalInt.MatchString(s) {
		if i, err := strconv.ParseInt(s, 10, 64); err == nil {
			return i, nil
		}
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil || math.IsInf(f, 0) || math.IsNaN(f) {
		return nil, fmt.Errorf("%s: numbers must be finite", pointer(path))
	}
	return f, nil
}

// ParseYAML reads one YAML document into a value, keeping member order. It
// reads scalars as the catalog loader does (manifest.LoadDir): a string or
// an unquoted date is text, !!int a decimal int64, !!float a finite
// float64, and anchors, aliases, explicit tags and non-string keys are
// refused. It is meant for records LoadDir has already accepted.
func ParseYAML(data []byte) (any, error) {
	dec := yaml.NewDecoder(bytes.NewReader(data))
	var doc yaml.Node
	if err := dec.Decode(&doc); err != nil {
		return nil, fmt.Errorf("is not valid YAML: %v", err)
	}
	var next yaml.Node
	if err := dec.Decode(&next); !errors.Is(err, io.EOF) {
		return nil, errors.New("holds more than one YAML document")
	}
	if len(doc.Content) != 1 {
		return nil, errors.New("must hold one YAML value")
	}
	return yamlValue(doc.Content[0], "")
}

func yamlValue(n *yaml.Node, path string) (any, error) {
	fail := func(format string, args ...any) error {
		return fmt.Errorf("%s: %s (line %d)", pointer(path), fmt.Sprintf(format, args...), n.Line)
	}
	if n.Kind == yaml.AliasNode || n.Anchor != "" || n.Style&yaml.TaggedStyle != 0 {
		return nil, fail("anchors, aliases and explicit tags are not allowed")
	}
	switch n.Kind {
	case yaml.MappingNode:
		obj := make(Object, 0, len(n.Content)/2)
		for i := 0; i+1 < len(n.Content); i += 2 {
			k, v := n.Content[i], n.Content[i+1]
			if k.Kind != yaml.ScalarNode || k.Tag != "!!str" {
				return nil, fail("keys must be strings")
			}
			if _, dup := obj.Get(k.Value); dup {
				return nil, fail("duplicate key %q", k.Value)
			}
			val, err := yamlValue(v, path+"/"+k.Value)
			if err != nil {
				return nil, err
			}
			obj = append(obj, Member{k.Value, val})
		}
		return obj, nil
	case yaml.SequenceNode:
		arr := make([]any, 0, len(n.Content))
		for i, c := range n.Content {
			val, err := yamlValue(c, fmt.Sprintf("%s/%d", path, i))
			if err != nil {
				return nil, err
			}
			arr = append(arr, val)
		}
		return arr, nil
	case yaml.ScalarNode:
		switch n.Tag {
		case "!!str", "!!timestamp":
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

// Compact renders v on one line in a form a YAML 1.2 reader reads as the
// same value: JSON with a space after each colon and comma, and every
// character YAML may not hold literally written as a \u escape.
func Compact(v any) ([]byte, error) {
	var buf bytes.Buffer
	if err := writeCompact(&buf, v); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func writeCompact(buf *bytes.Buffer, v any) error {
	v, err := normalize(v)
	if err != nil {
		return err
	}
	switch x := v.(type) {
	case Object:
		buf.WriteByte('{')
		for i, m := range x {
			if i > 0 {
				buf.WriteString(", ")
			}
			if err := writeString(buf, m.Key, escapeYAMLSafe); err != nil {
				return err
			}
			buf.WriteString(": ")
			if err := writeCompact(buf, m.Value); err != nil {
				return err
			}
		}
		buf.WriteByte('}')
	case []any:
		buf.WriteByte('[')
		for i, e := range x {
			if i > 0 {
				buf.WriteString(", ")
			}
			if err := writeCompact(buf, e); err != nil {
				return err
			}
		}
		buf.WriteByte(']')
	case string:
		return writeString(buf, x, escapeYAMLSafe)
	default:
		return writeCanonical(buf, x)
	}
	return nil
}

func pointer(path string) string {
	if path == "" {
		return "/"
	}
	return path
}
