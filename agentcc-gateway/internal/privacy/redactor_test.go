package privacy

import (
	"strings"
	"testing"
)

func TestNew(t *testing.T) {
	r := New("full", nil)
	if r.Mode() != ModeFull {
		t.Fatalf("expected full mode, got %s", r.Mode())
	}
}

func TestNew_DefaultMode(t *testing.T) {
	r := New("", nil)
	if r.Mode() != ModeNone {
		t.Fatalf("expected none mode for empty string, got %s", r.Mode())
	}
}

func TestRedact_Full(t *testing.T) {
	r := New(ModeFull, nil)
	result := r.Redact("some sensitive content")
	if result != "[REDACTED]" {
		t.Fatalf("expected [REDACTED], got %s", result)
	}
}

func TestRedact_None(t *testing.T) {
	r := New(ModeNone, nil)
	input := "some content"
	result := r.Redact(input)
	if result != input {
		t.Fatalf("expected unchanged content, got %s", result)
	}
}

func TestRedact_Empty(t *testing.T) {
	r := New(ModeFull, nil)
	result := r.Redact("")
	if result != "" {
		t.Fatalf("expected empty, got %s", result)
	}
}

func TestRedact_Patterns_Email(t *testing.T) {
	r := New(ModePatterns, []PatternConfig{
		{Name: "email", Pattern: `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}`},
	})

	input := "Contact me at user@example.com for details"
	result := r.Redact(input)

	if !strings.Contains(result, "[REDACTED:email]") {
		t.Fatalf("expected email redacted, got %s", result)
	}
	if strings.Contains(result, "user@example.com") {
		t.Fatalf("email should not appear in result: %s", result)
	}
}

func TestRedact_Patterns_SSN(t *testing.T) {
	r := New(ModePatterns, []PatternConfig{
		{Name: "ssn", Pattern: `\b\d{3}-\d{2}-\d{4}\b`},
	})

	input := "My SSN is 123-45-6789 and"
	result := r.Redact(input)

	if !strings.Contains(result, "[REDACTED:ssn]") {
		t.Fatalf("expected SSN redacted, got %s", result)
	}
	if strings.Contains(result, "123-45-6789") {
		t.Fatal("SSN should not appear in result")
	}
}

func TestRedact_Patterns_Multiple(t *testing.T) {
	r := New(ModePatterns, []PatternConfig{
		{Name: "email", Pattern: `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}`},
		{Name: "ssn", Pattern: `\b\d{3}-\d{2}-\d{4}\b`},
	})

	input := "Email: test@example.com, SSN: 123-45-6789"
	result := r.Redact(input)

	if !strings.Contains(result, "[REDACTED:email]") {
		t.Fatalf("expected email redacted, got %s", result)
	}
	if !strings.Contains(result, "[REDACTED:ssn]") {
		t.Fatalf("expected SSN redacted, got %s", result)
	}
}

func TestRedact_Patterns_NoMatch(t *testing.T) {
	r := New(ModePatterns, []PatternConfig{
		{Name: "ssn", Pattern: `\b\d{3}-\d{2}-\d{4}\b`},
	})

	input := "No sensitive data here"
	result := r.Redact(input)

	if result != input {
		t.Fatalf("expected unchanged content when no match, got %s", result)
	}
}

func TestRedact_InvalidPattern_Skipped(t *testing.T) {
	r := New(ModePatterns, []PatternConfig{
		{Name: "bad", Pattern: `[invalid`},
		{Name: "good", Pattern: `secret`},
	})

	if r.PatternCount() != 1 {
		t.Fatalf("expected 1 valid pattern, got %d", r.PatternCount())
	}

	result := r.Redact("this is a secret")
	if !strings.Contains(result, "[REDACTED:good]") {
		t.Fatalf("expected good pattern to work, got %s", result)
	}
}

func TestRedactForMode_Override(t *testing.T) {
	// Global mode is patterns, but per-key says full.
	r := New(ModePatterns, []PatternConfig{
		{Name: "email", Pattern: `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}`},
	})

	result := r.RedactForMode("Contact user@example.com", ModeFull)
	if result != "[REDACTED]" {
		t.Fatalf("expected full redaction from per-key override, got %s", result)
	}
}

func TestRedactForMode_GlobalStricter(t *testing.T) {
	// Global mode is full, per-key says patterns — full should win.
	r := New(ModeFull, nil)

	result := r.RedactForMode("some content", ModePatterns)
	if result != "[REDACTED]" {
		t.Fatalf("expected full redaction (global stricter), got %s", result)
	}
}

func TestRedactForMode_NoneOverride(t *testing.T) {
	// Global mode is patterns, per-key says none — patterns should still apply.
	r := New(ModePatterns, []PatternConfig{
		{Name: "test", Pattern: `secret`},
	})

	result := r.RedactForMode("this is secret data", ModeNone)
	if !strings.Contains(result, "[REDACTED:test]") {
		t.Fatalf("expected pattern redaction (global stricter than none), got %s", result)
	}
}

func TestShouldRedact(t *testing.T) {
	if New(ModeNone, nil).ShouldRedact() {
		t.Fatal("none mode should not redact")
	}
	if !New(ModeFull, nil).ShouldRedact() {
		t.Fatal("full mode should redact")
	}
	if !New(ModePatterns, nil).ShouldRedact() {
		t.Fatal("patterns mode should redact")
	}
}

func TestStricterMode(t *testing.T) {
	r := New(ModeNone, nil)

	tests := []struct {
		global, perKey, want string
	}{
		{ModeNone, ModeNone, ModeNone},
		{ModeNone, ModePatterns, ModePatterns},
		{ModeNone, ModeFull, ModeFull},
		{ModePatterns, ModeNone, ModePatterns},
		{ModePatterns, ModePatterns, ModePatterns},
		{ModePatterns, ModeFull, ModeFull},
		{ModeFull, ModeNone, ModeFull},
		{ModeFull, ModePatterns, ModeFull},
		{ModeFull, ModeFull, ModeFull},
	}

	for _, tt := range tests {
		r.mode = tt.global
		got := r.stricterMode(tt.perKey)
		if got != tt.want {
			t.Errorf("stricter(%s, %s) = %s, want %s", tt.global, tt.perKey, got, tt.want)
		}
	}
}

func TestPatternCount(t *testing.T) {
	r := New(ModePatterns, []PatternConfig{
		{Name: "a", Pattern: `foo`},
		{Name: "b", Pattern: `bar`},
	})
	if r.PatternCount() != 2 {
		t.Fatalf("expected 2, got %d", r.PatternCount())
	}
}
