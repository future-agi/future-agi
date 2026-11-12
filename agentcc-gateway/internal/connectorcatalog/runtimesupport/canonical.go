package runtimesupport

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

var errJSON = errors.New("runtime support: invalid canonical JSON")
var versionPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)

// DeclarationRevision hashes the entire record using the consumer's Python
// JSON convention, not JCS or a typed manifest projection. Integer tokens are
// preserved without float64 round trips. Floats/exponents are conservatively
// refused: Python and Go/JCS number formatting is not interchangeable. Such a
// record remains an unknown revision, never a guessed positive identity.
func DeclarationRevision(raw []byte) (string, error) {
	obj, err := strictObject(raw)
	if err != nil {
		return "", err
	}
	version, ok := obj["version"].(string)
	if !ok || !versionPattern.MatchString(version) || len(version)+72 > 256 {
		return "", errJSON
	}
	var buf bytes.Buffer
	if err := pythonJSON(&buf, obj); err != nil {
		return "", err
	}
	sum := sha256.Sum256(buf.Bytes())
	return version + "+sha256:" + hex.EncodeToString(sum[:]), nil
}

func strictObject(raw []byte) (map[string]any, error) {
	if !utf8.Valid(raw) || !validSurrogates(raw) {
		return nil, errJSON
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	v, err := strictValue(dec, 0)
	if err != nil {
		return nil, errJSON
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, errJSON
	}
	obj, ok := v.(map[string]any)
	if !ok {
		return nil, errJSON
	}
	return obj, nil
}

func strictValue(dec *json.Decoder, depth int) (any, error) {
	if depth > 64 {
		return nil, errJSON
	}
	token, err := dec.Token()
	if err != nil {
		return nil, err
	}
	switch v := token.(type) {
	case json.Delim:
		switch v {
		case '{':
			obj := map[string]any{}
			for dec.More() {
				keyToken, err := dec.Token()
				if err != nil {
					return nil, err
				}
				key, ok := keyToken.(string)
				if !ok {
					return nil, errJSON
				}
				if _, duplicate := obj[key]; duplicate {
					return nil, errJSON
				}
				value, err := strictValue(dec, depth+1)
				if err != nil {
					return nil, err
				}
				obj[key] = value
			}
			end, err := dec.Token()
			if err != nil || end != json.Delim('}') {
				return nil, errJSON
			}
			return obj, nil
		case '[':
			arr := []any{}
			for dec.More() {
				value, err := strictValue(dec, depth+1)
				if err != nil {
					return nil, err
				}
				arr = append(arr, value)
			}
			end, err := dec.Token()
			if err != nil || end != json.Delim(']') {
				return nil, errJSON
			}
			return arr, nil
		}
	case string, bool, nil, json.Number:
		return v, nil
	}
	return nil, errJSON
}

// encoding/json replaces unpaired surrogate escapes. Reject them rather than
// hashing the replacement; a valid pair is decoded to the actual code point.
func validSurrogates(raw []byte) bool {
	unit := func(at int) (uint64, bool) {
		if at+6 > len(raw) || raw[at] != '\\' || raw[at+1] != 'u' {
			return 0, false
		}
		n, err := strconv.ParseUint(string(raw[at+2:at+6]), 16, 16)
		return n, err == nil
	}
	for i := 0; i < len(raw); i++ {
		if raw[i] != '\\' {
			continue
		}
		if i+1 >= len(raw) {
			return false
		}
		if raw[i+1] != 'u' {
			i++
			continue
		}
		n, ok := unit(i)
		if !ok {
			return false
		}
		if n >= 0xdc00 && n <= 0xdfff {
			return false
		}
		if n >= 0xd800 && n <= 0xdbff {
			low, ok := unit(i + 6)
			if !ok || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		}
		i += 5
	}
	return true
}

func pythonJSON(buf *bytes.Buffer, v any) error {
	switch x := v.(type) {
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
			pythonString(buf, k)
			buf.WriteByte(':')
			if err := pythonJSON(buf, x[k]); err != nil {
				return err
			}
		}
		buf.WriteByte('}')
	case []any:
		buf.WriteByte('[')
		for i, value := range x {
			if i > 0 {
				buf.WriteByte(',')
			}
			if err := pythonJSON(buf, value); err != nil {
				return err
			}
		}
		buf.WriteByte(']')
	case string:
		pythonString(buf, x)
	case json.Number:
		if strings.ContainsAny(x.String(), ".eE") {
			return errJSON
		}
		if x.String() == "-0" {
			buf.WriteByte('0')
		} else {
			buf.WriteString(x.String())
		}
	case bool:
		if x {
			buf.WriteString("true")
		} else {
			buf.WriteString("false")
		}
	case nil:
		buf.WriteString("null")
	default:
		return errJSON
	}
	return nil
}

// UTF-8 lexicographic key order above is Unicode code-point order (Python),
// unlike JCS's UTF-16 order. No HTML, slash, U+2028 or U+2029 escaping.
func pythonString(buf *bytes.Buffer, s string) {
	const digits = "0123456789abcdef"
	buf.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"', '\\':
			buf.WriteByte('\\')
			buf.WriteRune(r)
		case '\b':
			buf.WriteString(`\b`)
		case '\f':
			buf.WriteString(`\f`)
		case '\n':
			buf.WriteString(`\n`)
		case '\r':
			buf.WriteString(`\r`)
		case '\t':
			buf.WriteString(`\t`)
		default:
			if r < 0x20 {
				buf.WriteString(`\u00`)
				buf.WriteByte(digits[r>>4])
				buf.WriteByte(digits[r&15])
			} else {
				buf.WriteRune(r)
			}
		}
	}
	buf.WriteByte('"')
}
