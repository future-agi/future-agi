package trajectory

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sort"
	"unicode/utf8"
)

// CanonicalJSON re-encodes valid JSON with recursively sorted object keys and
// compact whitespace while preserving exact numeric representations via
// json.Number. Two payloads differing only in object key order or whitespace
// produce identical canonical bytes.
func CanonicalJSON(raw []byte) ([]byte, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var val any
	if err := dec.Decode(&val); err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	buf.Grow(len(raw))
	if err := encodeCanonical(&buf, val); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// HashCanonical returns the 32-char lowercase hex encoding of the first 16
// bytes (128 bits) of SHA-256 over the canonical JSON representation of raw.
// Falls back to hashing raw bytes directly if raw is not valid JSON.
func HashCanonical(raw []byte) string {
	canon, err := CanonicalJSON(raw)
	if err != nil {
		canon = raw
	}
	sum := sha256.Sum256(canon)
	return hex.EncodeToString(sum[:16])
}

func encodeCanonical(buf *bytes.Buffer, v any) error {
	switch x := v.(type) {
	case nil:
		buf.WriteString("null")
	case bool:
		if x {
			buf.WriteString("true")
		} else {
			buf.WriteString("false")
		}
	case json.Number:
		buf.WriteString(x.String())
	case string:
		b, err := marshalNoEscape(x)
		if err != nil {
			return err
		}
		buf.Write(b)
	case []any:
		buf.WriteByte('[')
		for i, elem := range x {
			if i > 0 {
				buf.WriteByte(',')
			}
			if err := encodeCanonical(buf, elem); err != nil {
				return err
			}
		}
		buf.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		buf.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				buf.WriteByte(',')
			}
			kb, err := marshalNoEscape(k)
			if err != nil {
				return err
			}
			buf.Write(kb)
			buf.WriteByte(':')
			if err := encodeCanonical(buf, x[k]); err != nil {
				return err
			}
		}
		buf.WriteByte('}')
	default:
		b, err := marshalNoEscape(x)
		if err != nil {
			return err
		}
		buf.Write(b)
	}
	return nil
}

func marshalNoEscape(v any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	b := buf.Bytes()
	if len(b) > 0 && b[len(b)-1] == '\n' {
		b = b[:len(b)-1]
	}
	return b, nil
}

// TruncateUTF8 returns s capped at maxBytes without splitting a multi-byte UTF-8 rune.
func TruncateUTF8(s string, maxBytes int) string {
	if maxBytes <= 0 {
		return ""
	}
	if len(s) <= maxBytes {
		return s
	}
	n := maxBytes
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}
