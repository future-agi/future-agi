package hallucination

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// HallucinationGuardrail performs heuristic checks for potential hallucinations
// in LLM responses. This is a lightweight check — for proper factuality verification,
// use Feature 3.13a (Future AGI Guardrail Models).
type HallucinationGuardrail struct {
	patterns []*regexp.Regexp
}

// New creates a HallucinationGuardrail.
func New(cfg map[string]interface{}) *HallucinationGuardrail {
	return &HallucinationGuardrail{
		patterns: compileDefaults(),
	}
}

func (g *HallucinationGuardrail) Name() string           { return "hallucination-detection" }
func (g *HallucinationGuardrail) Stage() guardrails.Stage { return guardrails.StagePost }

// Check looks for hallucination indicators in LLM responses.
func (g *HallucinationGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
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

	combined := strings.ToLower(strings.Join(texts, "\n"))
	matchCount := 0
	var indicators []string

	for _, p := range g.patterns {
		if p.MatchString(combined) {
			matchCount++
			indicators = append(indicators, p.String())
		}
	}

	if matchCount == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	// Score based on indicator count.
	score := float64(matchCount) * 0.2
	if score > 1.0 {
		score = 1.0
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   score,
		Message: fmt.Sprintf("Potential hallucination indicators found (%d)", matchCount),
		Details: map[string]interface{}{
			"indicator_count": matchCount,
			"indicators":      indicators,
		},
	}
}

func compileDefaults() []*regexp.Regexp {
	// Heuristic patterns that suggest the model is fabricating information.
	patterns := []string{
		`(?i)as\s+(?:an?\s+)?ai\s+(?:language\s+)?model,?\s+i\s+(?:don't|do\s+not|cannot)\s+have`,
		`(?i)i\s+(?:apologize|am\s+sorry).{0,30}(?:i\s+(?:don't|cannot)\s+(?:actually|really))`,
		`(?i)(?:according\s+to\s+(?:my|the)\s+(?:training|knowledge)\s+(?:cutoff|data))`,
		`(?i)i\s+(?:made|fabricated|invented)\s+(?:that|this)\s+(?:up|information)`,
		`(?i)(?:that|this)\s+(?:information|data|statistic)\s+(?:is|was|may\s+be)\s+(?:incorrect|fabricated|made\s+up)`,
		`(?i)i\s+should\s+(?:note|mention|clarify)\s+that\s+i\s+(?:may\s+have|might\s+have|could\s+have)\s+(?:hallucinated|made\s+up)`,
		`(?i)(?:fictional|hypothetical|imaginary)\s+(?:study|paper|research|source|citation|reference)`,
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
