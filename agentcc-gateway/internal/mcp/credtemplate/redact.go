package credtemplate

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"strconv"
	"strings"
)

// Every type here that holds a value prints, encodes and logs without it:
// String, fmt (every verb, %#v included), JSON and slog all give the
// redacted description. TestTemplateNeverLogged covers each path.

func (p Part) String() string {
	s := string(p.In) + " " + p.Name + "=" + redactedMark
	if p.Constant {
		s += " (constant)"
	}
	return s
}

// Format prints String for every verb.
func (p Part) Format(f fmt.State, _ rune) { _, _ = io.WriteString(f, p.String()) }

// MarshalJSON encodes the part without its value.
func (p Part) MarshalJSON() ([]byte, error) {
	return json.Marshal(map[string]any{"in": p.In, "name": p.Name, "value": redactedMark, "constant": p.Constant})
}

// LogValue logs String.
func (p Part) LogValue() slog.Value { return slog.StringValue(p.String()) }

func (p SecretPart) String() string { return p.In + " " + p.Name + "=" + redactedMark }

// Format prints String for every verb.
func (p SecretPart) Format(f fmt.State, _ rune) { _, _ = io.WriteString(f, p.String()) }

// MarshalJSON encodes the part without its value.
func (p SecretPart) MarshalJSON() ([]byte, error) {
	return json.Marshal(map[string]string{"in": p.In, "name": p.Name, "value": redactedMark})
}

// LogValue logs String.
func (p SecretPart) LogValue() slog.Value { return slog.StringValue(p.String()) }

func (s Secret) String() string {
	return "credtemplate.Secret{type=" + s.Type + " header=" + s.Header + " in=" + s.In + " name=" + s.Name +
		" value=" + redactedMark + " parts=" + strconv.Itoa(len(s.Parts)) + "}"
}

// Format prints String for every verb.
func (s Secret) Format(f fmt.State, _ rune) { _, _ = io.WriteString(f, s.String()) }

// MarshalJSON encodes the secret without its values.
func (s Secret) MarshalJSON() ([]byte, error) {
	return json.Marshal(map[string]any{"type": s.Type, "header": s.Header, "in": s.In, "name": s.Name,
		"value": redactedMark, "parts": s.Parts})
}

// LogValue logs String.
func (s Secret) LogValue() slog.Value { return slog.StringValue(s.String()) }

func (t Template) String() string {
	parts := make([]string, len(t.parts))
	for i, p := range t.parts {
		parts[i] = p.String()
	}
	return "credtemplate.Template{origin=" + t.origin + " parts=[" + strings.Join(parts, ", ") + "]}"
}

// Format prints String for every verb, for a *Template and a Template.
func (t Template) Format(f fmt.State, _ rune) { _, _ = io.WriteString(f, t.String()) }

// MarshalJSON encodes the template without its values.
func (t Template) MarshalJSON() ([]byte, error) {
	return json.Marshal(map[string]any{"origin": t.origin, "parts": t.parts})
}

// LogValue logs String.
func (t Template) LogValue() slog.Value { return slog.StringValue(t.String()) }
