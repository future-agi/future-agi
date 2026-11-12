package mcp

import (
	"strings"
	"unicode"
	"unicode/utf8"
)

// MaxRefusalMessageRunes bounds the sentence a credential service gives a
// caller on a refusal (the message of mcp-upstream-token-v1 and
// mcp-vendor-credential-v1 $defs/Refusal, maxLength 300).
const MaxRefusalMessageRunes = 300

// RefusalMessage is raw, a credential service's sentence for the caller, as
// plain text: invalid UTF-8 replaced, each run of whitespace one space,
// control and format characters (unicode Cc and Cf: NUL, bidi overrides,
// zero-width characters) dropped, trimmed, and cut to
// MaxRefusalMessageRunes characters. "" when nothing is left.
func RefusalMessage(raw string) string {
	plain := strings.Map(func(r rune) rune {
		switch {
		case unicode.IsSpace(r):
			return ' '
		case unicode.In(r, unicode.Cc, unicode.Cf):
			return -1
		}
		return r
	}, strings.ToValidUTF8(raw, string(utf8.RuneError)))
	plain = strings.Join(strings.Fields(plain), " ")
	if utf8.RuneCountInString(plain) > MaxRefusalMessageRunes {
		plain = strings.TrimSpace(string([]rune(plain)[:MaxRefusalMessageRunes]))
	}
	return plain
}

// CredentialHeld refuses a call whose credential the credential service
// holds back by policy (Microsoft's hold, microsoft_coming_soon): the call is
// not made, exactly as CredentialUnavailable, and the refusal says why in the
// service's own sentence (RefusalMessage of message), or in
// CredentialUnavailable's words when it gave none. It carries no connect
// link: the hold stops a sign-in from starting too.
func CredentialHeld(message string) *Denial {
	d := CredentialUnavailable()
	if text := RefusalMessage(message); text != "" {
		d.Message = text
	}
	return d
}
