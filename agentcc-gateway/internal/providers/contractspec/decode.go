package contractspec

import (
	"bytes"
	"compress/gzip"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"

	"gopkg.in/yaml.v3"
)

// Specs are untrusted input. They are only ever parsed: nothing in them is
// executed, only local "#/" refs are followed, and decoded sizes are capped.

// MaxSpecBytes caps a decoded spec. The largest spec in use (Vertex
// v1beta1 discovery) is about 5 MB.
const MaxSpecBytes = 64 << 20

// Decoders a Source may name.
const (
	DecodeNone = ""
	// DecodeGzip gunzips the fetched file (Anthropic's bundled
	// scripts/mock-spec.json.gz).
	DecodeGzip = "gzip"
	// DecodeEmbeddedMock extracts EMBEDDED_SPEC="<base64 gzip>" from a
	// Stainless SDK's scripts/mock shell script (Groq, Together). The script
	// is read as text and never run.
	DecodeEmbeddedMock = "embedded-mock"
)

var embeddedSpecRE = regexp.MustCompile(`EMBEDDED_SPEC="([A-Za-z0-9+/=\s]+)"`)

// DecodeSource turns fetched bytes into the spec document's bytes.
func DecodeSource(raw []byte, decode string) ([]byte, error) {
	switch decode {
	case DecodeNone:
		return raw, nil
	case DecodeGzip:
		return gunzip(raw)
	case DecodeEmbeddedMock:
		m := embeddedSpecRE.FindSubmatch(raw)
		if m == nil {
			return nil, errors.New(`no EMBEDDED_SPEC="..." in the mock script`)
		}
		b64 := bytes.Join(bytes.Fields(m[1]), nil)
		gz := make([]byte, base64.StdEncoding.DecodedLen(len(b64)))
		n, err := base64.StdEncoding.Decode(gz, b64)
		if err != nil {
			return nil, fmt.Errorf("EMBEDDED_SPEC is not base64: %w", err)
		}
		return gunzip(gz[:n])
	default:
		return nil, fmt.Errorf("unknown decode %q", decode)
	}
}

func gunzip(raw []byte) ([]byte, error) {
	zr, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	defer func() { _ = zr.Close() }()
	out, err := io.ReadAll(io.LimitReader(zr, MaxSpecBytes+1))
	if err != nil {
		return nil, err
	}
	if len(out) > MaxSpecBytes {
		return nil, fmt.Errorf("decompressed spec exceeds %d bytes", MaxSpecBytes)
	}
	return out, nil
}

// ParseDocument decodes a JSON or YAML spec into generic maps. YAML keys
// are stringified (a bare 200: response key becomes "200"), so both forms
// walk the same way.
func ParseDocument(raw []byte) (map[string]any, error) {
	if len(raw) > MaxSpecBytes {
		return nil, fmt.Errorf("spec exceeds %d bytes", MaxSpecBytes)
	}
	if t := bytes.TrimSpace(raw); len(t) > 0 && t[0] == '{' {
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.UseNumber()
		var doc map[string]any
		if err := dec.Decode(&doc); err != nil {
			return nil, fmt.Errorf("spec is not valid JSON: %w", err)
		}
		return doc, nil
	}
	var y any
	if err := yaml.Unmarshal(raw, &y); err != nil {
		return nil, fmt.Errorf("spec is neither JSON nor valid YAML: %w", err)
	}
	doc, ok := stringKeys(y, 0).(map[string]any)
	if !ok {
		return nil, errors.New("spec is not an object")
	}
	return doc, nil
}

// stringKeys converts yaml.v3's generic values so every mapping is a
// map[string]any.
func stringKeys(v any, depth int) any {
	if depth > 200 {
		return nil
	}
	switch t := v.(type) {
	case map[string]any:
		for k, x := range t {
			t[k] = stringKeys(x, depth+1)
		}
		return t
	case map[any]any:
		m := make(map[string]any, len(t))
		for k, x := range t {
			m[fmt.Sprint(k)] = stringKeys(x, depth+1)
		}
		return m
	case []any:
		for i, x := range t {
			t[i] = stringKeys(x, depth+1)
		}
		return t
	}
	return v
}
