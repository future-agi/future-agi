package language

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"unicode"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// LanguageGuardrail detects language mismatches and blocks non-allowed languages.
type LanguageGuardrail struct {
	allowedScripts []string // e.g., ["latin", "common"]
}

// New creates a LanguageGuardrail from rule config.
func New(cfg map[string]interface{}) *LanguageGuardrail {
	g := &LanguageGuardrail{}
	if cfg != nil {
		if v, ok := cfg["allowed_scripts"]; ok {
			if list, ok := v.([]interface{}); ok {
				for _, item := range list {
					if s, ok := item.(string); ok {
						g.allowedScripts = append(g.allowedScripts, strings.ToLower(s))
					}
				}
			}
		}
	}
	return g
}

func (g *LanguageGuardrail) Name() string           { return "language-detection" }
func (g *LanguageGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check detects non-Latin/non-allowed scripts that might indicate language mismatch attacks.
func (g *LanguageGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil || input.Request == nil || len(g.allowedScripts) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	text := extractUserText(input)
	if text == "" {
		return &guardrails.CheckResult{Pass: true}
	}

	// Detect script distribution.
	scripts := detectScripts(text)
	var disallowed []string
	allowed := make(map[string]bool)
	for _, s := range g.allowedScripts {
		allowed[s] = true
	}

	for script, count := range scripts {
		if !allowed[script] && count > 5 {
			disallowed = append(disallowed, fmt.Sprintf("%s(%d)", script, count))
		}
	}

	if len(disallowed) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: fmt.Sprintf("Non-allowed scripts detected: %s", strings.Join(disallowed, ", ")),
		Details: map[string]interface{}{
			"scripts": scripts,
		},
	}
}

// detectScripts counts Unicode script categories in text.
func detectScripts(text string) map[string]int {
	scripts := make(map[string]int)
	for _, r := range text {
		if unicode.IsSpace(r) || unicode.IsPunct(r) || unicode.IsDigit(r) {
			continue
		}
		switch {
		case unicode.Is(unicode.Latin, r):
			scripts["latin"]++
		case unicode.Is(unicode.Cyrillic, r):
			scripts["cyrillic"]++
		case unicode.Is(unicode.Arabic, r):
			scripts["arabic"]++
		case unicode.Is(unicode.Han, r):
			scripts["han"]++
		case unicode.Is(unicode.Hiragana, r) || unicode.Is(unicode.Katakana, r):
			scripts["japanese"]++
		case unicode.Is(unicode.Hangul, r):
			scripts["korean"]++
		case unicode.Is(unicode.Devanagari, r):
			scripts["devanagari"]++
		case unicode.Is(unicode.Greek, r):
			scripts["greek"]++
		case unicode.Is(unicode.Hebrew, r):
			scripts["hebrew"]++
		case unicode.Is(unicode.Thai, r):
			scripts["thai"]++
		default:
			scripts["other"]++
		}
	}
	return scripts
}

// homoglyphPatterns detects Cyrillic/Greek characters that look like Latin (homoglyph attacks).
var homoglyphPattern = regexp.MustCompile(`[\x{0410}-\x{044F}\x{0391}-\x{03C9}]`)

// DetectHomoglyphs checks for mixed scripts that might be homoglyph attacks.
func DetectHomoglyphs(text string) bool {
	hasLatin := false
	hasLookalike := false
	for _, r := range text {
		if unicode.Is(unicode.Latin, r) {
			hasLatin = true
		}
		if unicode.Is(unicode.Cyrillic, r) || unicode.Is(unicode.Greek, r) {
			hasLookalike = true
		}
	}
	return hasLatin && hasLookalike
}

func extractUserText(input *guardrails.CheckInput) string {
	var parts []string
	for _, m := range input.Request.Messages {
		if m.Role != "user" {
			continue
		}
		if t, ok := extractContentText(m.Content); ok {
			parts = append(parts, t)
		}
	}
	return strings.Join(parts, " ")
}

func extractContentText(raw json.RawMessage) (string, bool) {
	if len(raw) == 0 {
		return "", false
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s, true
	}
	return "", false
}
