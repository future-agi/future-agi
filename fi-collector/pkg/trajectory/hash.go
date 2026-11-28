package trajectory

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"unicode/utf8"
)

// HashItem returns the 32-char lowercase hex encoding of the first 16 bytes
// (128 bits) of SHA-256 over the exact bytes of content. No canonicalisation
// happens: two serialisations of the same message that differ in key order,
// whitespace or escapes are different items, so every row stored under one
// key holds byte-identical content.
func HashItem(content string) string {
	return sha256Prefix(content)
}

// PayloadChecksum is the envelope's per-side "x" field: the same 128-bit
// SHA-256 prefix as HashItem, over the exact original side text.
func PayloadChecksum(payload string) string {
	return sha256Prefix(payload)
}

func sha256Prefix(s string) string {
	h := sha256.New()
	_, _ = io.WriteString(h, s)
	var sum [sha256.Size]byte
	return hex.EncodeToString(h.Sum(sum[:0])[:16])
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
