package privacy

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
)

// minDerivedSpelling is the shortest derived spelling worth matching.
const minDerivedSpelling = 8

// ScrubExactCredential removes registered, known credentials, not guessed key
// shapes. Use it at error construction, for every owner. It leaves unrelated
// provider diagnostics (including another owner's text) intact. Callers bind
// credentials to the request which actually dispatched, never a refreshed token
// or a mutable provider-global last-request field.
//
// G1 includes JSON escaping, percent encoding, hex, and both base64 alphabets at
// all three byte alignments. For an embedded base64 value only sextets wholly
// determined by the credential are matched; the adjacent prefix/suffix bytes
// need not be known. No successful response body is processed here.
func ScrubExactCredential(text string, credentials ...string) string {
	if text == "" {
		return text
	}
	registered := map[string]bool{}
	for _, secret := range credentials {
		if secret != "" {
			registered[secret] = true
		}
	}
	patterns := map[string]bool{}
	// A derived spelling (hex, base64 fragment, escaped form) shorter than
	// minDerivedSpelling is ordinary text, not evidence of a credential: the
	// one-byte value "k" has base64 fragments "a" and "r". Only the registered
	// value itself is removed at any length.
	add := func(s string) {
		if s != "" && (len(s) >= minDerivedSpelling || registered[s]) {
			patterns[s] = true
		}
	}
	var percentPatterns []string
	for _, secret := range credentials {
		if secret == "" {
			continue
		}
		add(secret)
		add(hex.EncodeToString([]byte(secret)))
		add(strings.ToUpper(hex.EncodeToString([]byte(secret))))
		// Ordinary JSON escaping, including HTML-safe JSON and escaped slashes.
		quoted, _ := json.Marshal(secret)
		forms := []string{string(quoted[1 : len(quoted)-1]), strconv.Quote(secret)[1 : len(strconv.Quote(secret))-1]}
		var unicode strings.Builder
		for _, r := range secret {
			for _, u := range utf16.Encode([]rune{r}) {
				unicode.WriteString("\\u")
				h := strconv.FormatUint(uint64(u), 16)
				unicode.WriteString(strings.Repeat("0", 4-len(h)))
				unicode.WriteString(h)
			}
		}
		forms = append(forms, unicode.String())
		for _, s := range forms {
			add(s)
			add(strings.ReplaceAll(s, "/", "\\/"))
			// Error protocols sometimes wrap a JSON error payload as a string.
			outer, _ := json.Marshal(s)
			add(string(outer[1 : len(outer)-1]))
		}
		for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.URLEncoding} {
			add(enc.EncodeToString([]byte(secret)))
			add(enc.WithPadding(base64.NoPadding).EncodeToString([]byte(secret)))
			for offset := 0; offset < 3; offset++ {
				padded := append(make([]byte, offset), []byte(secret)...)
				encoded := enc.EncodeToString(padded)
				start, end := (offset*8+5)/6, (len(padded)*8)/6
				if end > start {
					add(encoded[start:end])
				}
			}
		}
		// Hex digit case in each percent triplet is independent. Literal letters
		// outside a triplet remain case-sensitive; do not mask unrelated text.
		var full strings.Builder
		for _, b := range []byte(secret) {
			full.WriteString("%")
			h := strings.ToUpper(hex.EncodeToString([]byte{b}))
			full.WriteString(h)
		}
		for _, encoded := range []string{url.QueryEscape(secret), url.PathEscape(secret), full.String()} {
			if !strings.Contains(encoded, "%") {
				add(encoded)
				continue
			}
			if len(encoded) < minDerivedSpelling {
				continue
			}
			var pattern strings.Builder
			for i := 0; i < len(encoded); i++ {
				if encoded[i] == '%' && i+2 < len(encoded) {
					pattern.WriteByte('%')
					for _, b := range []byte(encoded[i+1 : i+3]) {
						if b >= 'A' && b <= 'F' || b >= 'a' && b <= 'f' {
							pattern.WriteByte('[')
							pattern.WriteString(strings.ToLower(string(b)) + strings.ToUpper(string(b)))
							pattern.WriteByte(']')
						} else {
							pattern.WriteString(regexp.QuoteMeta(string(b)))
						}
					}
					i += 2
				} else {
					pattern.WriteString(regexp.QuoteMeta(string(encoded[i])))
				}
			}
			percentPatterns = append(percentPatterns, pattern.String())
		}
	}
	if len(patterns) == 0 {
		return text
	}
	// Longer variants first: raw bytes may be a substring of an escaped spelling.
	ordered := make([]string, 0, len(patterns))
	for p := range patterns {
		ordered = append(ordered, p)
	}
	sort.Slice(ordered, func(i, j int) bool {
		if len(ordered[i]) != len(ordered[j]) {
			return len(ordered[i]) > len(ordered[j])
		}
		return ordered[i] < ordered[j]
	})
	pairs := make([]string, 0, len(ordered)*2)
	for _, p := range ordered {
		pairs = append(pairs, p, "[REDACTED]")
	}
	if len(percentPatterns) > 0 {
		text = regexp.MustCompile(strings.Join(percentPatterns, "|")).ReplaceAllString(text, "[REDACTED]")
	}
	return strings.NewReplacer(pairs...).Replace(text)
}
