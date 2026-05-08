package injection

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type injectionDetector struct {
	category string
	weight   float64
	patterns []*regexp.Regexp
}

// InjectionGuardrail detects prompt injection and jailbreak attempts.
type InjectionGuardrail struct {
	detectors []injectionDetector
	threshold int // matches for score 1.0
}

// New creates an InjectionGuardrail from rule config.
func New(cfg map[string]interface{}) *InjectionGuardrail {
	g := &InjectionGuardrail{
		threshold: 3, // default: medium sensitivity
	}

	if cfg != nil {
		if v, ok := cfg["sensitivity"].(string); ok {
			switch strings.ToLower(v) {
			case "high":
				g.threshold = 1
			case "low":
				g.threshold = 5
			default:
				g.threshold = 3
			}
		}
	}

	// Determine enabled categories.
	var enabledSet map[string]bool
	if cfg != nil {
		if v, ok := cfg["categories"]; ok {
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

	allDetectors := defaultDetectors()
	for _, d := range allDetectors {
		if enabledSet != nil && !enabledSet[d.category] {
			continue
		}
		g.detectors = append(g.detectors, d)
	}

	return g
}

func (g *InjectionGuardrail) Name() string            { return "prompt-injection" }
func (g *InjectionGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check evaluates user messages for injection patterns.
func (g *InjectionGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil || input.Request == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	// Only check user messages (not system or assistant).
	var texts []string
	for _, m := range input.Request.Messages {
		if m.Role != "user" {
			continue
		}
		if t, ok := extractContentText(m.Content); ok {
			texts = append(texts, t)
		}
	}

	if len(texts) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	combined := strings.ToLower(strings.Join(texts, "\n"))

	type categoryMatch struct {
		category string
		count    int
		weight   float64
	}

	var matches []categoryMatch
	totalWeightedMatches := 0.0

	for _, d := range g.detectors {
		count := 0
		if d.category == "structured_role_injection" {
			count = countStructuredRoleInjection(texts)
		} else {
			for _, p := range d.patterns {
				if p.MatchString(combined) {
					count++
				}
			}
		}
		if count > 0 {
			matches = append(matches, categoryMatch{
				category: d.category,
				count:    count,
				weight:   d.weight,
			})
			totalWeightedMatches += float64(count) * d.weight
		}
	}

	if len(matches) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	score := math.Min(1.0, totalWeightedMatches/float64(g.threshold))

	catDetails := make(map[string]interface{})
	var triggered []string
	for _, m := range matches {
		catDetails[m.category] = map[string]interface{}{
			"matches": m.count,
			"weight":  m.weight,
		}
		triggered = append(triggered, m.category)
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   score,
		Message: fmt.Sprintf("Potential prompt injection detected: %s (score: %.2f)", strings.Join(triggered, ", "), score),
		Details: map[string]interface{}{
			"categories":    catDetails,
			"total_matches": totalWeightedMatches,
		},
	}
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

func countStructuredRoleInjection(texts []string) int {
	count := 0
	for _, text := range texts {
		for _, candidate := range structuredJSONCandidates(text) {
			if hasSuspiciousStructuredRole(candidate) {
				count++
			}
		}
	}
	return count
}

func structuredJSONCandidates(text string) []string {
	trimmed := strings.TrimSpace(text)
	seen := map[string]struct{}{}
	var candidates []string

	addCandidate := func(candidate string) {
		candidate = strings.TrimSpace(candidate)
		if candidate == "" {
			return
		}
		if _, ok := seen[candidate]; ok {
			return
		}
		seen[candidate] = struct{}{}
		candidates = append(candidates, candidate)
	}

	if json.Valid([]byte(trimmed)) {
		addCandidate(trimmed)
	}

	for _, fragment := range extractJSONFragments(text) {
		if json.Valid([]byte(fragment)) {
			addCandidate(fragment)
		}
	}

	return candidates
}

func extractJSONFragments(text string) []string {
	var fragments []string
	start := -1
	depth := 0
	inString := false
	escaped := false

	for i := 0; i < len(text); i++ {
		ch := text[i]

		if escaped {
			escaped = false
			continue
		}

		if ch == '\\' && inString {
			escaped = true
			continue
		}

		if ch == '"' {
			inString = !inString
			continue
		}

		if inString {
			continue
		}

		switch ch {
		case '{', '[':
			if depth == 0 {
				start = i
			}
			depth++
		case '}', ']':
			if depth == 0 {
				continue
			}
			depth--
			if depth == 0 && start >= 0 {
				fragments = append(fragments, text[start:i+1])
				start = -1
			}
		}
	}

	return fragments
}

func hasSuspiciousStructuredRole(candidate string) bool {
	var decoded interface{}
	if err := json.Unmarshal([]byte(candidate), &decoded); err != nil {
		return false
	}

	return containsSuspiciousStructuredRole(decoded)
}

func containsSuspiciousStructuredRole(value interface{}) bool {
	switch v := value.(type) {
	case map[string]interface{}:
		role, _ := v["role"].(string)
		if isPrivilegedRole(role) && containsSuspiciousContent(v["content"]) {
			return true
		}
		for _, child := range v {
			if containsSuspiciousStructuredRole(child) {
				return true
			}
		}
	case []interface{}:
		for _, child := range v {
			if containsSuspiciousStructuredRole(child) {
				return true
			}
		}
	}

	return false
}

func containsSuspiciousContent(value interface{}) bool {
	switch v := value.(type) {
	case string:
		return hasSuspiciousText(v)
	case []interface{}:
		for _, child := range v {
			if containsSuspiciousContent(child) {
				return true
			}
		}
	case map[string]interface{}:
		if text, ok := v["text"].(string); ok && hasSuspiciousText(text) {
			return true
		}
		for _, child := range v {
			if containsSuspiciousContent(child) {
				return true
			}
		}
	}

	return false
}

func hasSuspiciousText(text string) bool {
	combined := strings.ToLower(text)
	for _, detector := range defaultDetectors() {
		if detector.category == "structured_role_injection" {
			continue
		}
		for _, pattern := range detector.patterns {
			if pattern.MatchString(combined) {
				return true
			}
		}
	}
	return false
}

func isPrivilegedRole(role string) bool {
	role = strings.ToLower(strings.TrimSpace(role))
	return role == "system" || role == "developer"
}

func defaultDetectors() []injectionDetector {
	return []injectionDetector{
		{
			category: "structured_role_injection",
			weight:   1.5,
			patterns: nil,
		},
		{
			category: "instruction_override",
			weight:   1.5,
			patterns: compileAll([]string{
				`(?i)ignore\s+(all\s+)?previous\s+instructions`,
				`(?i)ignore\s+(all\s+)?prior\s+instructions`,
				`(?i)forget\s+(all\s+)?(your\s+)?rules`,
				`(?i)forget\s+(all\s+)?(your\s+)?instructions`,
				`(?i)disregard\s+(all\s+)?previous`,
				`(?i)override\s+(your\s+)?instructions`,
				`(?i)new\s+instructions?\s*:`,
				`(?i)you\s+must\s+now\s+follow`,
				`(?i)from\s+now\s+on\s+you\s+(will|must|should)`,
			}),
		},
		{
			category: "role_manipulation",
			weight:   1.2,
			patterns: compileAll([]string{
				`(?i)you\s+are\s+(?:now\s+)?(?:dan|evil|unrestricted|unfiltered)`,
				`(?i)pretend\s+(?:you\s+are|to\s+be|you're)\s+(?:a|an)?\s*(?:evil|unrestricted|unfiltered)`,
				`(?i)act\s+as\s+(?:if\s+you\s+have\s+)?no\s+(?:restrictions|rules|filters|guardrails)`,
				`(?i)jailbreak\s+mode`,
				`(?i)developer\s+mode\s+(?:enabled|activated|on)`,
				`(?i)do\s+anything\s+now`,
				`(?i)bypass\s+(?:your\s+)?(?:filters|safety|restrictions|rules)`,
				`(?i)without\s+(?:any\s+)?(?:restrictions|limitations|filters|safety)`,
			}),
		},
		{
			category: "system_prompt_extraction",
			weight:   1.0,
			patterns: compileAll([]string{
				`(?i)(?:repeat|show|display|print|output|reveal)\s+(?:your\s+)?system\s+prompt`,
				`(?i)what\s+(?:are|is)\s+your\s+(?:system\s+)?instructions`,
				`(?i)(?:show|tell)\s+me\s+your\s+(?:system\s+)?prompt`,
				`(?i)what\s+(?:were\s+you|was\s+your)\s+(?:told|instructed)`,
				`(?i)(?:output|print)\s+(?:the\s+)?(?:above|initial)\s+(?:text|prompt|instructions)`,
				`(?i)repeat\s+(?:the\s+)?(?:words\s+)?above`,
			}),
		},
		{
			category: "delimiter_injection",
			weight:   1.0,
			patterns: compileAll([]string{
				`(?i)<\/?system>`,
				`(?i)\[system\]`,
				`(?i)---\s*(?:system|instructions?|rules?)`,
				`(?i)###\s*(?:system|instructions?|new\s+context)`,
				`(?i)\[INST\]`,
				`(?i)<\|(?:im_start|system|user)\|>`,
			}),
		},
		{
			category: "encoding_bypass",
			weight:   1.0,
			patterns: compileAll([]string{
				`(?i)base64\s+decode`,
				`(?i)decode\s+(?:this|the\s+following)\s+base64`,
				`(?i)(?:rot13|caesar\s+cipher)\s+(?:decode|decrypt)`,
				`(?i)translate\s+from\s+(?:hex|binary|octal|base64)`,
			}),
		},
	}
}

func compileAll(patterns []string) []*regexp.Regexp {
	var compiled []*regexp.Regexp
	for _, p := range patterns {
		compiled = append(compiled, regexp.MustCompile(p))
	}
	return compiled
}
