package canonjson

import (
	"bytes"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"
)

// maxSafeInteger is 2^53: a larger integer is not one IEEE 754 double, so
// another JSON reader may read a different number.
const maxSafeInteger = 1 << 53

// Canonical renders v in the JSON Canonicalization Scheme (RFC 8785): no
// whitespace, object members sorted by the UTF-16 code units of their keys,
// strings with only the escapes RFC 8785 requires, and numbers as
// ECMAScript's Number.prototype.toString writes them. In JavaScript the same
// bytes are JSON.stringify of the value with its keys sorted.
func Canonical(v any) ([]byte, error) {
	var buf bytes.Buffer
	if err := writeCanonical(&buf, Sorted(v)); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func writeCanonical(buf *bytes.Buffer, v any) error {
	v, err := normalize(v)
	if err != nil {
		return err
	}
	switch x := v.(type) {
	case nil:
		buf.WriteString("null")
	case bool:
		buf.WriteString(strconv.FormatBool(x))
	case string:
		return writeString(buf, x, escapeJCS)
	case int64, float64:
		s, err := NumberText(x)
		if err != nil {
			return err
		}
		buf.WriteString(s)
	case Object:
		buf.WriteByte('{')
		for i, m := range x {
			if i > 0 {
				buf.WriteByte(',')
			}
			if err := writeString(buf, m.Key, escapeJCS); err != nil {
				return err
			}
			buf.WriteByte(':')
			if err := writeCanonical(buf, m.Value); err != nil {
				return err
			}
		}
		buf.WriteByte('}')
	case []any:
		buf.WriteByte('[')
		for i, e := range x {
			if i > 0 {
				buf.WriteByte(',')
			}
			if err := writeCanonical(buf, e); err != nil {
				return err
			}
		}
		buf.WriteByte(']')
	}
	return nil
}

// NumberText renders an int64 or float64 as ECMAScript's
// Number.prototype.toString does (ECMA-262 Number::toString), the form
// RFC 8785 requires. It refuses NaN, infinities and integers beyond 2^53.
func NumberText(v any) (string, error) {
	var f float64
	switch x := v.(type) {
	case int64:
		if x > maxSafeInteger || x < -maxSafeInteger {
			return "", fmt.Errorf("canonjson: integer %d is beyond 2^53, so JSON readers may disagree on it", x)
		}
		return strconv.FormatInt(x, 10), nil
	case float64:
		f = x
	default:
		return "", fmt.Errorf("canonjson: %T is not a number", v)
	}
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return "", errors.New("canonjson: numbers must be finite")
	}
	if f == 0 {
		return "0", nil // and -0
	}
	sign := ""
	if f < 0 {
		sign, f = "-", -f
	}
	// The shortest digits that round-trip, s, and n with s × 10^(n−k) = f.
	e := strconv.FormatFloat(f, 'e', -1, 64) // d.ddde±XX
	mant, exp, _ := strings.Cut(e, "e")
	digits := strings.Replace(mant, ".", "", 1)
	x, _ := strconv.Atoi(exp)
	k, n := len(digits), x+1
	switch {
	case k <= n && n <= 21:
		return sign + digits + strings.Repeat("0", n-k), nil
	case 0 < n && n <= 21:
		return sign + digits[:n] + "." + digits[n:], nil
	case -6 < n && n <= 0:
		return sign + "0." + strings.Repeat("0", -n) + digits, nil
	}
	expSign := "+"
	if n-1 < 0 {
		expSign = "-"
	}
	frac := ""
	if k > 1 {
		frac = "." + digits[1:]
	}
	return sign + digits[:1] + frac + "e" + expSign + strconv.Itoa(abs(n-1)), nil
}

func abs(i int) int {
	if i < 0 {
		return -i
	}
	return i
}

// escapeMode says which characters a string escapes beyond RFC 8785's set.
type escapeMode int

const (
	escapeJCS      escapeMode = iota // RFC 8785: only " \ and C0 controls
	escapeASCII                      // and every non-ASCII character, as \uXXXX
	escapeYAMLSafe                   // and what YAML 1.2 may not hold literally
)

func writeString(buf *bytes.Buffer, s string, mode escapeMode) error {
	if !utf8.ValidString(s) {
		return errors.New("canonjson: a string is not valid UTF-8")
	}
	buf.WriteByte('"')
	for _, r := range s {
		switch {
		case r == '"':
			buf.WriteString(`\"`)
		case r == '\\':
			buf.WriteString(`\\`)
		case r == '\b':
			buf.WriteString(`\b`)
		case r == '\t':
			buf.WriteString(`\t`)
		case r == '\n':
			buf.WriteString(`\n`)
		case r == '\f':
			buf.WriteString(`\f`)
		case r == '\r':
			buf.WriteString(`\r`)
		case r < 0x20:
			fmt.Fprintf(buf, `\u%04x`, r)
		case mode == escapeASCII && r > 0x7e:
			writeUTF16Escape(buf, r)
		case mode == escapeYAMLSafe && !yamlPrintable(r):
			fmt.Fprintf(buf, `\u%04x`, r) // never a surrogate: s is valid UTF-8
		default:
			buf.WriteRune(r)
		}
	}
	buf.WriteByte('"')
	return nil
}

// writeUTF16Escape writes r as \uXXXX, or as a surrogate pair beyond the
// Basic Multilingual Plane.
func writeUTF16Escape(buf *bytes.Buffer, r rune) {
	if r <= 0xffff {
		fmt.Fprintf(buf, `\u%04x`, r)
		return
	}
	r -= 0x10000
	fmt.Fprintf(buf, `\u%04x\u%04x`, 0xd800+(r>>10), 0xdc00+(r&0x3ff))
}

// yamlPrintable reports whether a double-quoted YAML scalar may hold r
// literally and read it back unchanged: YAML 1.2's c-printable set (§5.1),
// less the byte order mark and the characters libyaml, and so yaml.v3,
// takes as line breaks and folds into a space (NEL, U+2028, U+2029).
func yamlPrintable(r rune) bool {
	switch {
	case r == 0x85 || r == 0x2028 || r == 0x2029 || r == 0xfeff:
		return false
	case r == 0x09 || (r >= 0x20 && r <= 0x7e):
		return true
	case r >= 0xa0 && r <= 0xd7ff, r >= 0xe000 && r <= 0xfffd, r >= 0x10000 && r <= 0x10ffff:
		return true
	}
	return false
}
