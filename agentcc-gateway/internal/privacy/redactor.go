package privacy

import (
	"fmt"
	"log/slog"
	"regexp"
)

// Redaction mode constants.
const (
	ModeNone     = "none"
	ModePatterns = "patterns"
	ModeFull     = "full"
)

// Pattern is a compiled redaction pattern.
type Pattern struct {
	Name  string
	Regex *regexp.Regexp
}

// Redactor strips or masks sensitive data from log content.
type Redactor struct {
	mode     string
	patterns []Pattern
}

// New creates a Redactor from config.
func New(mode string, patterns []PatternConfig) *Redactor {
	if mode == "" {
		mode = ModeNone
	}

	r := &Redactor{mode: mode}

	for _, p := range patterns {
		re, err := regexp.Compile(p.Pattern)
		if err != nil {
			slog.Warn("privacy: invalid redaction pattern, skipping",
				"name", p.Name, "pattern", p.Pattern, "error", err)
			continue
		}
		r.patterns = append(r.patterns, Pattern{Name: p.Name, Regex: re})
	}

	return r
}

// PatternConfig is a redaction pattern from config.
type PatternConfig struct {
	Name    string `yaml:"name" json:"name"`
	Pattern string `yaml:"pattern" json:"pattern"`
}

// Mode returns the redaction mode.
func (r *Redactor) Mode() string {
	return r.mode
}

// PatternCount returns the number of compiled patterns.
func (r *Redactor) PatternCount() int {
	return len(r.patterns)
}

// Redact applies the configured redaction mode to a content string.
// Returns the redacted content. The original string is never modified.
func (r *Redactor) Redact(content string) string {
	if content == "" {
		return content
	}

	switch r.mode {
	case ModeFull:
		return "[REDACTED]"
	case ModePatterns:
		return r.redactPatterns(content)
	default:
		return content
	}
}

// RedactForMode applies a specific mode, overriding the global mode.
// Used for per-key privacy settings that may be stricter.
func (r *Redactor) RedactForMode(content, mode string) string {
	if content == "" {
		return content
	}

	// Use the stricter of global mode and requested mode.
	effectiveMode := r.stricterMode(mode)

	switch effectiveMode {
	case ModeFull:
		return "[REDACTED]"
	case ModePatterns:
		return r.redactPatterns(content)
	default:
		return content
	}
}

func (r *Redactor) redactPatterns(content string) string {
	result := content
	for _, p := range r.patterns {
		replacement := fmt.Sprintf("[REDACTED:%s]", p.Name)
		result = p.Regex.ReplaceAllString(result, replacement)
	}
	return result
}

// stricterMode returns whichever mode is more restrictive.
// Full > Patterns > None.
func (r *Redactor) stricterMode(mode string) string {
	if r.mode == ModeFull || mode == ModeFull {
		return ModeFull
	}
	if r.mode == ModePatterns || mode == ModePatterns {
		return ModePatterns
	}
	return ModeNone
}

// ShouldRedact returns true if any redaction will be applied.
func (r *Redactor) ShouldRedact() bool {
	return r.mode != ModeNone
}
