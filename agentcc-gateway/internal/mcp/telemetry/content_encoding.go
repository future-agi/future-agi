package telemetry

import (
	"encoding/base64"
	"net/url"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

const maxEncodingDepth = 4

var encodedAtom = regexp.MustCompile(`[A-Za-z0-9+/_%.\-]+={0,2}`)
var base64Atom = regexp.MustCompile(`^[A-Za-z0-9+/_\-]+={0,2}$`)

// canonicalText only decodes bounded, recoverable textual representations.
// It never fetches a URL, decompresses a payload or interprets binary data.
// A remaining layer at the bound fails closed, rather than returning it unseen.
func canonicalText(text string) (string, bool) {
	if len(text) > maxTextBytes {
		return "", false
	}
	for i := 0; i < maxEncodingDepth; i++ {
		next, encoded, valid := decodeTextLayer(text)
		if !valid {
			return "", false
		}
		if !encoded {
			return text, true
		}
		text = next
	}
	_, encoded, valid := decodeTextLayer(text)
	return text, valid && !encoded
}

func decodeTextLayer(text string) (string, bool, bool) {
	if strings.Contains(text, "%") {
		decoded, err := url.PathUnescape(text)
		if err != nil {
			return "", false, false
		}
		if decoded != text {
			return decoded, true, utf8.ValidString(decoded)
		}
	}
	if len(text) < 4 || !base64Atom.MatchString(text) {
		return text, false, true
	}
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawStdEncoding, base64.URLEncoding, base64.RawURLEncoding} {
		decoded, err := enc.Strict().DecodeString(text)
		if err != nil || !utf8.Valid(decoded) || len(decoded) == 0 {
			continue
		}
		printable := true
		for _, r := range string(decoded) {
			if !unicode.IsPrint(r) && r != '\r' && r != '\n' && r != '\t' {
				printable = false
				break
			}
		}
		if printable {
			return string(decoded), true, true
		}
	}
	return text, false, true
}

// Preserve an encoded safe value verbatim; replace an affected envelope whole
// instead of accidentally copying an unexamined encoded suffix to the span.
func scrubEncodedAtoms(text string, depth int) string {
	return encodedAtom.ReplaceAllStringFunc(text, func(atom string) string {
		decoded, ok := canonicalText(atom)
		if !ok {
			return redactedContent
		}
		if decoded != atom && scrubText(decoded, depth+1) != decoded {
			return redactedContent
		}
		return atom
	})
}
