package validation

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"regexp"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// ValidationGuardrail enforces structural rules: regex patterns, length limits.
type ValidationGuardrail struct {
	denyPatterns    []*regexp.Regexp
	requirePatterns []*regexp.Regexp
	maxMsgLen       int
	maxTotalLen     int
	minMsgLen       int
}

// New creates a ValidationGuardrail from rule config.
func New(cfg map[string]interface{}) *ValidationGuardrail {
	g := &ValidationGuardrail{}

	if cfg == nil {
		return g
	}

	// Parse deny patterns.
	if v, ok := cfg["deny_patterns"]; ok {
		if list, ok := v.([]interface{}); ok {
			for _, item := range list {
				if s, ok := item.(string); ok {
					re, err := regexp.Compile(s)
					if err != nil {
						slog.Warn("invalid deny pattern, skipping", "pattern", s, "error", err)
						continue
					}
					g.denyPatterns = append(g.denyPatterns, re)
				}
			}
		}
	}

	// Parse require patterns.
	if v, ok := cfg["require_patterns"]; ok {
		if list, ok := v.([]interface{}); ok {
			for _, item := range list {
				if s, ok := item.(string); ok {
					re, err := regexp.Compile(s)
					if err != nil {
						slog.Warn("invalid require pattern, skipping", "pattern", s, "error", err)
						continue
					}
					g.requirePatterns = append(g.requirePatterns, re)
				}
			}
		}
	}

	g.maxMsgLen = parseIntConfig(cfg, "max_message_length")
	g.maxTotalLen = parseIntConfig(cfg, "max_total_length")
	g.minMsgLen = parseIntConfig(cfg, "min_message_length")

	return g
}

func (g *ValidationGuardrail) Name() string           { return "input-validation" }
func (g *ValidationGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check validates messages against configured rules.
func (g *ValidationGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	texts := extractAllTexts(input)
	if len(texts) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	var violations []string

	// Check per-message length.
	if g.maxMsgLen > 0 {
		for i, t := range texts {
			if len(t) > g.maxMsgLen {
				violations = append(violations, fmt.Sprintf("message %d exceeds max length (%d > %d)", i, len(t), g.maxMsgLen))
			}
		}
	}

	// Check min message length.
	if g.minMsgLen > 0 {
		for i, t := range texts {
			if len(t) < g.minMsgLen {
				violations = append(violations, fmt.Sprintf("message %d below min length (%d < %d)", i, len(t), g.minMsgLen))
			}
		}
	}

	// Check total length.
	if g.maxTotalLen > 0 {
		total := 0
		for _, t := range texts {
			total += len(t)
		}
		if total > g.maxTotalLen {
			violations = append(violations, fmt.Sprintf("total length exceeds max (%d > %d)", total, g.maxTotalLen))
		}
	}

	combined := strings.Join(texts, "\n")

	// Check deny patterns.
	for _, re := range g.denyPatterns {
		if re.MatchString(combined) {
			violations = append(violations, fmt.Sprintf("matched deny pattern: %s", re.String()))
		}
	}

	// Check require patterns.
	for _, re := range g.requirePatterns {
		if !re.MatchString(combined) {
			violations = append(violations, fmt.Sprintf("failed require pattern: %s", re.String()))
		}
	}

	if len(violations) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: fmt.Sprintf("Validation failed: %s", strings.Join(violations, "; ")),
		Details: map[string]interface{}{
			"violations":      violations,
			"violation_count": len(violations),
		},
	}
}

func extractAllTexts(input *guardrails.CheckInput) []string {
	var texts []string
	if input.Request != nil {
		for _, m := range input.Request.Messages {
			if t, ok := extractContentText(m.Content); ok {
				texts = append(texts, t)
			}
		}
	}
	if input.Response != nil {
		for _, c := range input.Response.Choices {
			if t, ok := extractContentText(c.Message.Content); ok {
				texts = append(texts, t)
			}
		}
	}
	return texts
}

func extractContentText(raw json.RawMessage) (string, bool) {
	if len(raw) == 0 {
		return "", false
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s, true
	}
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &parts); err == nil {
		var texts []string
		for _, p := range parts {
			if p.Type == "text" && p.Text != "" {
				texts = append(texts, p.Text)
			}
		}
		if len(texts) > 0 {
			return strings.Join(texts, " "), true
		}
	}
	return "", false
}

func parseIntConfig(cfg map[string]interface{}, key string) int {
	if v, ok := cfg[key]; ok {
		switch n := v.(type) {
		case int:
			return n
		case float64:
			return int(n)
		}
	}
	return 0
}
