package secrets

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type secretDetector struct {
	secretType string
	pattern    *regexp.Regexp
}

// SecretsGuardrail detects credentials and secrets in messages.
type SecretsGuardrail struct {
	detectors  []secretDetector
	maxSecrets int
}

// New creates a SecretsGuardrail from rule config.
func New(cfg map[string]interface{}) *SecretsGuardrail {
	g := &SecretsGuardrail{
		maxSecrets: 1,
	}

	if cfg != nil {
		if v, ok := cfg["max_secrets"]; ok {
			switch n := v.(type) {
			case int:
				g.maxSecrets = n
			case float64:
				g.maxSecrets = int(n)
			}
		}
	}
	if g.maxSecrets <= 0 {
		g.maxSecrets = 1
	}

	// Parse enabled types.
	var enabledSet map[string]bool
	if cfg != nil {
		if v, ok := cfg["types"]; ok {
			if list, ok := v.([]interface{}); ok && len(list) > 0 {
				enabledSet = make(map[string]bool, len(list))
				for _, item := range list {
					if s, ok := item.(string); ok {
						enabledSet[s] = true
					}
				}
			}
		}
	}

	g.detectors = buildDetectors(enabledSet)
	return g
}

func (g *SecretsGuardrail) Name() string           { return "secret-detection" }
func (g *SecretsGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check scans messages for secrets.
func (g *SecretsGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	text := extractAllText(input)
	if text == "" {
		return &guardrails.CheckResult{Pass: true}
	}

	var detections []secretMatch
	for _, d := range g.detectors {
		matches := d.pattern.FindAllString(text, -1)
		for _, m := range matches {
			detections = append(detections, secretMatch{
				SecretType: d.secretType,
				Redacted:   redact(m),
			})
		}
	}

	if len(detections) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	score := math.Min(1.0, float64(len(detections))/float64(g.maxSecrets))

	types := uniqueTypes(detections)
	msg := fmt.Sprintf("Secrets detected: %s (%d found)", strings.Join(types, ", "), len(detections))

	detailList := make([]interface{}, len(detections))
	for i, d := range detections {
		detailList[i] = map[string]interface{}{
			"type":     d.SecretType,
			"redacted": d.Redacted,
		}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   score,
		Message: msg,
		Details: map[string]interface{}{
			"secrets_found": len(detections),
			"detections":    detailList,
		},
	}
}

func redact(s string) string {
	if len(s) <= 8 {
		return "***"
	}
	return s[:4] + "..." + s[len(s)-4:]
}

type secretMatch struct {
	SecretType string
	Redacted   string
}

func uniqueTypes(detections []secretMatch) []string {
	seen := make(map[string]bool)
	var types []string
	for _, d := range detections {
		if !seen[d.SecretType] {
			seen[d.SecretType] = true
			types = append(types, d.SecretType)
		}
	}
	return types
}

func buildDetectors(enabled map[string]bool) []secretDetector {
	all := defaultPatterns()
	var detectors []secretDetector
	for secretType, pattern := range all {
		if enabled != nil && !enabled[secretType] {
			continue
		}
		detectors = append(detectors, secretDetector{
			secretType: secretType,
			pattern:    regexp.MustCompile(pattern),
		})
	}
	sort.Slice(detectors, func(i, j int) bool {
		return detectors[i].secretType < detectors[j].secretType
	})
	return detectors
}

func defaultPatterns() map[string]string {
	return map[string]string{
		"aws_access_key": `\bAKIA[0-9A-Z]{16}\b`,
		"aws_secret_key": `(?i)(?:aws_secret_access_key|secret_key)\s*[=:]\s*[A-Za-z0-9/+=]{40}`,
		"github_token":   `\bgh[psoart]_[A-Za-z0-9_]{36,}\b`,
		"gitlab_token":   `\bglpat\-[A-Za-z0-9\-]{20,}\b`,
		"slack_token":    `\bxox[bpsar]\-[A-Za-z0-9\-]+\b`,
		"stripe_key":     `\b[sp]k_(?:live|test)_[A-Za-z0-9]{20,}\b`,
		"openai_key":     `\bsk\-[A-Za-z0-9]{20,}\b`,
		"private_key":    `-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----`,
		"connection_string": `(?:mongodb|postgres|postgresql|mysql|redis|amqp)://[^\s]+:[^\s@]+@[^\s]+`,
		"jwt":            `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b`,
		"password_assign": `(?i)(?:password|passwd|pwd|secret)\s*[=:]\s*[^\s,;]{8,}`,
	}
}

func extractAllText(input *guardrails.CheckInput) string {
	var parts []string
	if input.Request != nil {
		for _, m := range input.Request.Messages {
			if t, ok := extractContentText(m.Content); ok {
				parts = append(parts, t)
			}
		}
	}
	if input.Response != nil {
		for _, c := range input.Response.Choices {
			if t, ok := extractContentText(c.Message.Content); ok {
				parts = append(parts, t)
			}
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
