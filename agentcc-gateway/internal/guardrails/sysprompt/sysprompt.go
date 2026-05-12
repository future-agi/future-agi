package sysprompt

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// SystemPromptGuardrail detects attempts to extract or override the system prompt.
type SystemPromptGuardrail struct {
	patterns []*regexp.Regexp
}

// New creates a SystemPromptGuardrail.
func New(cfg map[string]interface{}) *SystemPromptGuardrail {
	return &SystemPromptGuardrail{
		patterns: compileDefaults(),
	}
}

func (g *SystemPromptGuardrail) Name() string           { return "system-prompt-protection" }
func (g *SystemPromptGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check detects system prompt extraction attempts in user messages.
func (g *SystemPromptGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil || input.Request == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	var userTexts []string
	for _, m := range input.Request.Messages {
		if m.Role != "user" {
			continue
		}
		if t, ok := extractContentText(m.Content); ok {
			userTexts = append(userTexts, t)
		}
	}

	if len(userTexts) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	combined := strings.ToLower(strings.Join(userTexts, "\n"))
	matchCount := 0
	var matched []string
	for _, p := range g.patterns {
		if p.MatchString(combined) {
			matchCount++
			matched = append(matched, p.String())
		}
	}

	if matchCount == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	score := 1.0
	if matchCount == 1 {
		score = 0.7
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   score,
		Message: fmt.Sprintf("System prompt extraction attempt detected (%d patterns matched)", matchCount),
		Details: map[string]interface{}{
			"patterns_matched": matchCount,
		},
	}
}

func compileDefaults() []*regexp.Regexp {
	patterns := []string{
		`(?i)(?:show|reveal|display|print|output|repeat)\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?(?:prompt|instructions)`,
		`(?i)what\s+(?:are|is|were)\s+your\s+(?:system\s+)?(?:instructions|prompt|rules|guidelines)`,
		`(?i)(?:copy|paste|write)\s+(?:your\s+)?(?:entire\s+)?(?:system\s+)?(?:prompt|instructions)`,
		`(?i)beginning\s+of\s+(?:your\s+)?(?:conversation|prompt|context)`,
		`(?i)(?:above|initial|original|first)\s+(?:instructions?|prompt|message|text)`,
		`(?i)everything\s+(?:before|above)\s+(?:this|my)\s+message`,
		`(?i)(?:ignore|forget)\s+.*(?:repeat|show|output)`,
		`(?i)verbatim\s+(?:copy|repeat|reproduction)\s+of\s+(?:your\s+)?(?:instructions|prompt)`,
		`(?i)translate\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)\s+to`,
		`(?i)(?:encode|convert)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)\s+(?:to|in|as)`,
	}

	var compiled []*regexp.Regexp
	for _, p := range patterns {
		compiled = append(compiled, regexp.MustCompile(p))
	}
	return compiled
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
