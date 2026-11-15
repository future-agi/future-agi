package contentmod

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type categoryChecker struct {
	name    string
	weight  float64
	phrases []string // lowercase
}

// ContentModerationGuardrail detects harmful content across multiple categories.
type ContentModerationGuardrail struct {
	categories     []categoryChecker
	categoryThresh int // matches per category to reach score 1.0
	configErr      error
}

// New creates a ContentModerationGuardrail from rule config.
func New(cfg map[string]interface{}) *ContentModerationGuardrail {
	g := &ContentModerationGuardrail{
		categoryThresh: 3,
		configErr:      validateConfig(cfg),
	}

	if cfg != nil {
		if v, ok := cfg["category_threshold"]; ok {
			switch n := v.(type) {
			case int:
				g.categoryThresh = n
			case float64:
				g.categoryThresh = int(n)
			}
		}
	}
	if g.categoryThresh <= 0 {
		g.categoryThresh = 3
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

	// Parse weights.
	weights := map[string]float64{}
	if cfg != nil {
		if v, ok := cfg["weights"]; ok {
			if m, ok := v.(map[string]interface{}); ok {
				for k, wv := range m {
					if f, ok := wv.(float64); ok {
						weights[k] = f
					}
				}
			}
		}
	}

	// Build category checkers.
	allCategories := defaultCategories()
	// Keep numeric accumulation deterministic and aligned with selected-config
	// validation in the control plane. Map iteration can otherwise move a strict
	// score > threshold boundary by one ULP. The scoring rules are unchanged.
	for _, name := range []string{"toxicity", "hate_speech", "violence", "sexual", "self_harm"} {
		phrases := allCategories[name]
		if enabledSet != nil && !enabledSet[name] {
			continue
		}
		w := 1.0
		if wv, ok := weights[name]; ok && wv > 0 {
			w = wv
		}
		g.categories = append(g.categories, categoryChecker{
			name:    name,
			weight:  w,
			phrases: phrases,
		})
	}

	if g.configErr == nil {
		var totalWeight float64
		for _, cat := range g.categories {
			totalWeight += cat.weight
		}
		if math.IsInf(totalWeight, 0) || math.IsNaN(totalWeight) {
			g.configErr = fmt.Errorf("content-moderation effective weights must have a finite sum")
		}
	}
	return g
}

// ValidateConfig reports constructor-time configuration errors for selected policies,
// without changing legacy Check behavior or retaining mutable caller config.
func (g *ContentModerationGuardrail) ValidateConfig() error { return g.configErr }

func validateConfig(cfg map[string]interface{}) error {
	catalog := defaultCategories()
	if value, present := cfg["categories"]; present {
		list, ok := value.([]interface{})
		if !ok {
			return fmt.Errorf("content-moderation categories must be an array of supported category names")
		}
		for _, item := range list {
			name, ok := item.(string)
			if !ok {
				return fmt.Errorf("content-moderation categories must contain only supported category names")
			}
			if _, supported := catalog[name]; !supported {
				return fmt.Errorf("content-moderation categories contains an unsupported category name")
			}
		}
	}
	if value, present := cfg["category_threshold"]; present {
		valid := false
		switch n := value.(type) {
		case int:
			valid = n > 0
		case float64:
			// The exclusive upper bound avoids float64(MaxInt) rounding up to
			// 2^63. Check before conversion; the legacy constructor still has its
			// historical fallback, but selected callers must not use truncation.
			valid = !math.IsNaN(n) && !math.IsInf(n, 0) && n > 0 &&
				math.Trunc(n) == n && n < math.Ldexp(1, strconv.IntSize-1)
		}
		if !valid {
			return fmt.Errorf("content-moderation category_threshold must be a positive integral count representable as an int")
		}
	}
	if value, present := cfg["weights"]; present {
		weights, ok := value.(map[string]interface{})
		if !ok {
			return fmt.Errorf("content-moderation weights must be an object")
		}
		for name, value := range weights {
			if _, known := catalog[name]; !known {
				continue // Unknown weight keys are ignored by the constructor.
			}
			weight, ok := value.(float64)
			if !ok || math.IsNaN(weight) || math.IsInf(weight, 0) {
				return fmt.Errorf("content-moderation known category weights must be finite numbers")
			}
			// Preserve actual constructor semantics: positive numbers override
			// the weight; finite zero/negative numbers leave the default 1.
		}
	}
	// An empty category list intentionally keeps all default categories enabled.
	return nil
}

// ValidateSelectedThreshold checks reachability against the selected caller's
// actual outer confidence threshold. Zero is used as-is, not replaced by a
// default. It is deliberately not part of legacy org/static Check execution.
func (g *ContentModerationGuardrail) ValidateSelectedThreshold(threshold float64) error {
	if g.configErr != nil {
		return g.configErr
	}
	if math.IsNaN(threshold) || math.IsInf(threshold, 0) || threshold < 0 || threshold > 1 {
		return fmt.Errorf("content-moderation confidence threshold must be between zero and one")
	}
	// Each catalog phrase is counted at most once. Use the same order, weights,
	// per-category cap and dominance boost as Check, without inspecting text.
	var weightedSum, totalWeight, maxScore float64
	for _, cat := range g.categories {
		score := math.Min(1, float64(len(cat.phrases))/float64(g.categoryThresh))
		weightedSum += score * cat.weight
		totalWeight += cat.weight
		maxScore = math.Max(maxScore, score)
	}
	aggregate := weightedSum / totalWeight
	if maxScore >= 0.9 && aggregate < maxScore {
		aggregate = maxScore
	}
	// shouldTrigger uses strict greater-than; equality is unreachable too.
	if math.IsNaN(aggregate) || math.IsInf(aggregate, 0) || aggregate <= threshold {
		return fmt.Errorf("content-moderation configuration cannot exceed the selected confidence threshold")
	}
	return nil
}

func (g *ContentModerationGuardrail) Name() string            { return "content-moderation" }
func (g *ContentModerationGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check evaluates messages for harmful content.
func (g *ContentModerationGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	text := g.extractAllText(input)
	if text == "" {
		return &guardrails.CheckResult{Pass: true}
	}

	lower := strings.ToLower(text)

	type catResult struct {
		name    string
		score   float64
		matches int
	}

	var results []catResult
	var totalWeight float64
	var weightedSum float64
	var maxScore float64

	for _, cat := range g.categories {
		select {
		case <-ctx.Done():
			return &guardrails.CheckResult{Pass: true}
		default:
		}

		matches := 0
		for _, phrase := range cat.phrases {
			if strings.Contains(lower, phrase) {
				matches++
			}
		}

		score := math.Min(1.0, float64(matches)/float64(g.categoryThresh))
		results = append(results, catResult{name: cat.name, score: score, matches: matches})

		weightedSum += score * cat.weight
		totalWeight += cat.weight
		if score > maxScore {
			maxScore = score
		}
	}

	if totalWeight == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	aggregate := weightedSum / totalWeight

	// Single-category dominance: if any category >= 0.9, boost aggregate.
	if maxScore >= 0.9 && aggregate < maxScore {
		aggregate = maxScore
	}

	if aggregate == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	// Build details.
	catDetails := make(map[string]interface{})
	var triggered []string
	for _, r := range results {
		if r.score > 0 {
			catDetails[r.name] = map[string]interface{}{
				"score":   r.score,
				"matches": r.matches,
			}
			triggered = append(triggered, r.name)
		}
	}

	msg := fmt.Sprintf("Content moderation triggered: %s (score: %.2f)", strings.Join(triggered, ", "), aggregate)

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   aggregate,
		Message: msg,
		Details: map[string]interface{}{
			"categories":      catDetails,
			"aggregate_score": aggregate,
		},
	}
}

func (g *ContentModerationGuardrail) extractAllText(input *guardrails.CheckInput) string {
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

// defaultCategories returns keyword/phrase lists for each moderation category.
// Phrases are lowercase. Intentionally conservative to reduce false positives.
func defaultCategories() map[string][]string {
	return map[string][]string{
		"toxicity": {
			"you are stupid",
			"you're an idiot",
			"shut up",
			"you suck",
			"you are worthless",
			"you're pathetic",
			"go away loser",
			"piece of garbage",
			"waste of space",
			"nobody likes you",
			"you disgust me",
			"you're a moron",
			"dumb as a rock",
			"complete idiot",
			"total loser",
		},
		"hate_speech": {
			"kill all",
			"death to all",
			"go back to your country",
			"they should be exterminated",
			"inferior race",
			"subhuman",
			"ethnic cleansing",
			"white supremacy",
			"white power",
			"racial purity",
			"gas the",
			"hang them all",
			"they deserve to die",
			"master race",
			"racial superiority",
		},
		"violence": {
			"i will kill you",
			"i'm going to murder",
			"i want to stab",
			"i'll beat you to death",
			"blow up the building",
			"how to make a bomb",
			"how to make explosives",
			"shoot everyone",
			"mass shooting",
			"going to hurt you",
			"torture them",
			"slit their throat",
			"burn them alive",
			"how to poison someone",
			"school shooting",
		},
		"sexual": {
			"explicit sexual content",
			"graphic sexual description",
			"write me erotica",
			"sexual acts with minors",
			"child exploitation",
			"underage sexual",
			"non-consensual sexual",
			"sexual assault instructions",
			"revenge porn",
			"deepfake porn",
			"sexual abuse",
			"child pornography",
			"child abuse material",
		},
		"self_harm": {
			"how to kill myself",
			"how to commit suicide",
			"best way to die",
			"methods of suicide",
			"i want to end my life",
			"painless way to die",
			"how to cut myself",
			"self-harm techniques",
			"how to overdose",
			"suicide methods",
			"i want to hurt myself",
			"ways to harm yourself",
			"pro-ana tips",
			"pro-mia tips",
			"starvation diet",
		},
	}
}
