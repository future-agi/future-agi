// Package attributecatalog defines the pure canonical scalar codec for the
// ingestion-fed span-attribute catalog. It is intentionally not wired into the
// collector writer yet; the first stacked-PR slice only freezes the byte
// contract shared with Django/Python.
package attributecatalog

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	FingerprintDomain        = "futureagi.span-attribute-catalog.scalar.v1"
	maxCanonicalNumberLength = 4096
)

// Scalar is the stable payload stored in one catalog value row.
type Scalar struct {
	Kind        string
	ValueJSON   string
	SearchText  string
	Fingerprint string
}

// EncodeScalar returns canonical JSON plus a typed lowercase SHA-256 hex
// fingerprint. Only selectable JSON scalars are accepted. Arrays are expanded
// by a future bounded writer before this function; maps/JSON remain key-only.
func EncodeScalar(value any) (Scalar, error) {
	var out Scalar
	switch typed := value.(type) {
	case bool:
		out.Kind = "boolean"
		out.ValueJSON = strconv.FormatBool(typed)
		out.SearchText = out.ValueJSON
	case string:
		encoded, err := canonicalJSONString(typed)
		if err != nil {
			return Scalar{}, err
		}
		out.Kind = "string"
		out.ValueJSON = encoded
		out.SearchText = typed
	case json.Number:
		encoded, err := canonicalJSONNumber(typed.String())
		if err != nil {
			return Scalar{}, err
		}
		out.Kind = "number"
		out.ValueJSON = encoded
		out.SearchText = encoded
	case int:
		return encodeNumber(strconv.FormatInt(int64(typed), 10))
	case int8:
		return encodeNumber(strconv.FormatInt(int64(typed), 10))
	case int16:
		return encodeNumber(strconv.FormatInt(int64(typed), 10))
	case int32:
		return encodeNumber(strconv.FormatInt(int64(typed), 10))
	case int64:
		return encodeNumber(strconv.FormatInt(typed, 10))
	case uint:
		return encodeNumber(strconv.FormatUint(uint64(typed), 10))
	case uint8:
		return encodeNumber(strconv.FormatUint(uint64(typed), 10))
	case uint16:
		return encodeNumber(strconv.FormatUint(uint64(typed), 10))
	case uint32:
		return encodeNumber(strconv.FormatUint(uint64(typed), 10))
	case uint64:
		return encodeNumber(strconv.FormatUint(typed, 10))
	case float32:
		f := float64(typed)
		if math.IsNaN(f) || math.IsInf(f, 0) {
			return Scalar{}, fmt.Errorf("catalog numbers must be finite")
		}
		return encodeNumber(strconv.FormatFloat(f, 'g', -1, 32))
	case float64:
		if math.IsNaN(typed) || math.IsInf(typed, 0) {
			return Scalar{}, fmt.Errorf("catalog numbers must be finite")
		}
		return encodeNumber(strconv.FormatFloat(typed, 'g', -1, 64))
	default:
		return Scalar{}, fmt.Errorf("catalog values must be JSON scalars, got %T", value)
	}

	out.Fingerprint = fingerprint(out.Kind, out.ValueJSON)
	return out, nil
}

func encodeNumber(raw string) (Scalar, error) {
	encoded, err := canonicalJSONNumber(raw)
	if err != nil {
		return Scalar{}, err
	}
	return Scalar{
		Kind:        "number",
		ValueJSON:   encoded,
		SearchText:  encoded,
		Fingerprint: fingerprint("number", encoded),
	}, nil
}

func fingerprint(kind, valueJSON string) string {
	sum := sha256.Sum256([]byte(FingerprintDomain + "\x00" + kind + "\x00" + valueJSON))
	return hex.EncodeToString(sum[:])
}

func canonicalJSONString(value string) (string, error) {
	if !utf8.ValidString(value) {
		return "", fmt.Errorf("catalog strings must be valid UTF-8")
	}
	var out strings.Builder
	out.Grow(len(value) + 2)
	out.WriteByte('"')
	for _, char := range value {
		switch char {
		case '"':
			out.WriteString(`\"`)
		case '\\':
			out.WriteString(`\\`)
		case '\b':
			out.WriteString(`\b`)
		case '\f':
			out.WriteString(`\f`)
		case '\n':
			out.WriteString(`\n`)
		case '\r':
			out.WriteString(`\r`)
		case '\t':
			out.WriteString(`\t`)
		default:
			if char < 0x20 {
				fmt.Fprintf(&out, `\u%04x`, char)
			} else {
				out.WriteRune(char)
			}
		}
	}
	out.WriteByte('"')
	return out.String(), nil
}

func canonicalJSONNumber(raw string) (string, error) {
	if raw == "" {
		return "", fmt.Errorf("catalog number is empty")
	}
	if !json.Valid([]byte(raw)) {
		return "", fmt.Errorf("catalog number is not valid JSON")
	}
	negative := false
	if raw[0] == '-' {
		negative = true
		raw = raw[1:]
	} else if raw[0] == '+' {
		return "", fmt.Errorf("catalog number is not valid JSON")
	}

	mantissa := raw
	exponent := int64(0)
	if split := strings.IndexAny(raw, "eE"); split >= 0 {
		mantissa = raw[:split]
		parsed, err := strconv.ParseInt(raw[split+1:], 10, 32)
		if err != nil {
			return "", fmt.Errorf("catalog number has invalid exponent")
		}
		exponent = parsed
	}
	if strings.Count(mantissa, ".") > 1 || mantissa == "" {
		return "", fmt.Errorf("catalog number has invalid mantissa")
	}
	if point := strings.IndexByte(mantissa, '.'); point >= 0 {
		exponent -= int64(len(mantissa) - point - 1)
		mantissa = mantissa[:point] + mantissa[point+1:]
	}
	if mantissa == "" {
		return "", fmt.Errorf("catalog number has no digits")
	}
	for _, char := range mantissa {
		if char < '0' || char > '9' {
			return "", fmt.Errorf("catalog number contains a non-digit")
		}
	}
	digits := strings.TrimLeft(mantissa, "0")
	if digits == "" {
		return "0", nil
	}
	trimmed := strings.TrimRight(digits, "0")
	exponent += int64(len(digits) - len(trimmed))
	digits = trimmed

	var out strings.Builder
	if negative {
		out.WriteByte('-')
	}
	point := int64(len(digits)) + exponent
	switch {
	case exponent >= 0:
		if int64(len(digits))+exponent > maxCanonicalNumberLength {
			return "", fmt.Errorf("canonical catalog number exceeds 4096 bytes")
		}
		out.WriteString(digits)
		out.WriteString(strings.Repeat("0", int(exponent)))
	case point > 0:
		out.WriteString(digits[:point])
		out.WriteByte('.')
		out.WriteString(digits[point:])
	default:
		if 2-point+int64(len(digits)) > maxCanonicalNumberLength {
			return "", fmt.Errorf("canonical catalog number exceeds 4096 bytes")
		}
		out.WriteString("0.")
		out.WriteString(strings.Repeat("0", int(-point)))
		out.WriteString(digits)
	}
	if out.Len() > maxCanonicalNumberLength {
		return "", fmt.Errorf("canonical catalog number exceeds 4096 bytes")
	}
	return out.String(), nil
}
