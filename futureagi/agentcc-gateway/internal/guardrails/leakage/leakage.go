package leakage

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// LeakageGuardrail detects data leakage patterns and code injection in responses.
type LeakageGuardrail struct {
	patterns []leakagePattern
}

type leakagePattern struct {
	name  string
	regex *regexp.Regexp
}

// New creates a LeakageGuardrail.
func New(cfg map[string]interface{}) *LeakageGuardrail {
	return &LeakageGuardrail{
		patterns: defaultPatterns(),
	}
}

func (g *LeakageGuardrail) Name() string            { return "data-leakage-prevention" }
func (g *LeakageGuardrail) Stage() guardrails.Stage { return guardrails.StagePost }

// Check detects data leakage and code injection patterns in responses.
func (g *LeakageGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil || input.Response == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	var texts []string
	for _, c := range input.Response.Choices {
		if t, ok := extractContentText(c.Message.Content); ok {
			texts = append(texts, t)
		}
	}

	if len(texts) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	combined := strings.Join(texts, "\n")
	var matched []string

	for _, p := range g.patterns {
		if p.regex.MatchString(combined) {
			matched = append(matched, p.name)
		}
	}

	if len(matched) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	score := 1.0
	if len(matched) == 1 {
		score = 0.7
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   score,
		Message: fmt.Sprintf("Data leakage detected: %s", strings.Join(matched, ", ")),
		Details: map[string]interface{}{
			"patterns": matched,
		},
	}
}

func defaultPatterns() []leakagePattern {
	defs := []struct {
		name    string
		pattern string
	}{
		// SQL injection in responses.
		{"sql_injection", `(?i)(?:DROP\s+TABLE|DELETE\s+FROM|INSERT\s+INTO|UPDATE\s+.+\s+SET|SELECT\s+.+\s+FROM\s+.+\s+WHERE)\s+`},
		// Shell commands in responses.
		{"shell_injection", `(?:rm\s+-rf\s+/|sudo\s+rm|chmod\s+777|curl\s+.+\|\s*(?:bash|sh)|wget\s+.+\|\s*(?:bash|sh))`},
		// Script injection (XSS patterns).
		{"xss_injection", `(?i)<script[^>]*>|javascript:|on(?:load|error|click)\s*=`},
		// File path traversal.
		{"path_traversal", `(?:\.\./){3,}|/etc/(?:passwd|shadow|hosts)|/proc/self`},
		// Internal system info leakage.
		{"system_info_leak", `(?i)(?:internal\s+server\s+(?:error|ip)|stack\s+trace|exception\s+at|debug\s+mode\s+enabled)`},
		// Environment variable leakage.
		{"env_leak", `(?i)(?:DATABASE_URL|DB_PASSWORD|SECRET_KEY|API_SECRET|PRIVATE_KEY)\s*[=:]\s*\S+`},
	}

	var patterns []leakagePattern
	for _, d := range defs {
		patterns = append(patterns, leakagePattern{
			name:  d.name,
			regex: regexp.MustCompile(d.pattern),
		})
	}
	return patterns
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
