package contentmod

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
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
}

// New creates a ContentModerationGuardrail from rule config.
func New(cfg map[string]interface{}) *ContentModerationGuardrail {
	g := &ContentModerationGuardrail{
		categoryThresh: 3,
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
	for name, phrases := range allCategories {
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

	return g
}

func (g *ContentModerationGuardrail) Name() string           { return "content-moderation" }
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
