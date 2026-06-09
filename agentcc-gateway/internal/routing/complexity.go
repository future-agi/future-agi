package routing

import (
	"sort"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Default weights for complexity signal scoring.
var defaultComplexityWeights = map[string]float64{
	"token_count":          0.15,
	"message_count":        0.10,
	"system_prompt_length": 0.10,
	"tool_count":           0.15,
	"multimodal":           0.15,
	"keyword_heuristics":   0.15,
	"structured_output":    0.10,
	"max_tokens":           0.10,
}

// Reasoning keywords that indicate complex prompts.
var complexityKeywords = []string{
	"analyze", "compare", "evaluate", "reason",
	"step by step", "chain of thought", "think through",
	"explain why", "prove", "derive", "synthesize", "critique",
}

// complexityTier is a sorted, compiled tier entry.
type complexityTier struct {
	Name     string
	MaxScore int
	Model    string
	Provider string
}

// ComplexityResult holds the analysis result.
type ComplexityResult struct {
	Score    int
	Tier     string
	Model    string
	Provider string
}

// ComplexityAnalyzer scores prompts and maps them to model tiers.
// Immutable after creation — safe for concurrent use.
type ComplexityAnalyzer struct {
	weights      map[string]float64
	tiers        []complexityTier // sorted by MaxScore ascending
	defaultModel string
	defaultProv  string
	defaultTier  string
}

// NewComplexityAnalyzer creates an analyzer from config.
func NewComplexityAnalyzer(cfg config.ComplexityConfig) *ComplexityAnalyzer {
	if !cfg.Enabled || len(cfg.Tiers) == 0 {
		return nil
	}

	// Build weights with defaults.
	weights := make(map[string]float64, len(defaultComplexityWeights))
	for k, v := range defaultComplexityWeights {
		weights[k] = v
	}
	for k, v := range cfg.Weights {
		weights[k] = v
	}

	// Build and sort tiers.
	tiers := make([]complexityTier, 0, len(cfg.Tiers))
	for name, tc := range cfg.Tiers {
		tiers = append(tiers, complexityTier{
			Name:     name,
			MaxScore: tc.MaxScore,
			Model:    tc.Model,
			Provider: tc.Provider,
		})
	}
	sort.Slice(tiers, func(i, j int) bool {
		return tiers[i].MaxScore < tiers[j].MaxScore
	})

	// Find default tier.
	defaultTier := cfg.DefaultTier
	if defaultTier == "" {
		defaultTier = "moderate"
	}
	var defaultModel, defaultProv string
	for _, t := range tiers {
		if t.Name == defaultTier {
			defaultModel = t.Model
			defaultProv = t.Provider
			break
		}
	}
	// If default tier not found, use the middle tier.
	if defaultModel == "" && len(tiers) > 0 {
		mid := len(tiers) / 2
		defaultModel = tiers[mid].Model
		defaultProv = tiers[mid].Provider
		defaultTier = tiers[mid].Name
	}

	return &ComplexityAnalyzer{
		weights:      weights,
		tiers:        tiers,
		defaultModel: defaultModel,
		defaultProv:  defaultProv,
		defaultTier:  defaultTier,
	}
}

// IsEnabled returns true if the analyzer is configured.
func (a *ComplexityAnalyzer) IsEnabled() bool {
	return a != nil && len(a.tiers) > 0
}

// Analyze scores a request and returns the appropriate tier.
func (a *ComplexityAnalyzer) Analyze(req *models.ChatCompletionRequest) ComplexityResult {
	if req == nil || len(req.Messages) == 0 {
		return ComplexityResult{Score: 0, Tier: a.defaultTier, Model: a.defaultModel, Provider: a.defaultProv}
	}

	// Compute weighted score.
	var totalScore float64
	totalScore += a.weights["token_count"] * float64(scoreTokenCount(req.Messages))
	totalScore += a.weights["message_count"] * float64(scoreMessageCount(len(req.Messages)))
	totalScore += a.weights["system_prompt_length"] * float64(scoreSystemPrompt(req.Messages))
	totalScore += a.weights["tool_count"] * float64(scoreToolCount(len(req.Tools)))
	totalScore += a.weights["multimodal"] * float64(scoreMultimodal(req.Messages))
	totalScore += a.weights["keyword_heuristics"] * float64(scoreKeywords(req.Messages))
	totalScore += a.weights["structured_output"] * float64(scoreStructuredOutput(req))
	totalScore += a.weights["max_tokens"] * float64(scoreMaxTokens(req.MaxTokens))

	score := int(totalScore)
	if score < 0 {
		score = 0
	}
	if score > 100 {
		score = 100
	}

	// Map score to tier.
	for _, t := range a.tiers {
		if score <= t.MaxScore {
			return ComplexityResult{Score: score, Tier: t.Name, Model: t.Model, Provider: t.Provider}
		}
	}

	// Score exceeds all tiers — use the last (highest) tier.
	last := a.tiers[len(a.tiers)-1]
	return ComplexityResult{Score: score, Tier: last.Name, Model: last.Model, Provider: last.Provider}
}

// --- Signal scoring functions (all return 0–100) ---

// scoreTokenCount estimates content length from raw JSON message content.
func scoreTokenCount(messages []models.Message) int {
	totalLen := 0
	for _, m := range messages {
		totalLen += len(m.Content) // json.RawMessage length is a good proxy
	}
	switch {
	case totalLen < 200:
		return 0
	case totalLen < 1000:
		return 20
	case totalLen < 5000:
		return 50
	case totalLen < 20000:
		return 75
	default:
		return 100
	}
}

func scoreMessageCount(n int) int {
	switch {
	case n <= 1:
		return 0
	case n <= 5:
		return 25
	case n <= 10:
		return 50
	case n <= 20:
		return 75
	default:
		return 100
	}
}

func scoreSystemPrompt(messages []models.Message) int {
	for _, m := range messages {
		if m.Role == "system" {
			l := len(m.Content)
			switch {
			case l < 100:
				return 10
			case l < 500:
				return 30
			case l < 2000:
				return 60
			default:
				return 90
			}
		}
	}
	return 0
}

func scoreToolCount(n int) int {
	switch {
	case n == 0:
		return 0
	case n <= 2:
		return 25
	case n <= 5:
		return 50
	case n <= 10:
		return 75
	default:
		return 100
	}
}

func scoreMultimodal(messages []models.Message) int {
	for _, m := range messages {
		// Content is json.RawMessage — check raw JSON string for multimodal markers.
		raw := string(m.Content)
		if strings.Contains(raw, "image_url") || strings.Contains(raw, "input_audio") {
			return 80
		}
	}
	return 0
}

func scoreKeywords(messages []models.Message) int {
	hits := 0
	for _, m := range messages {
		if m.Role != "user" {
			continue
		}
		// Content is json.RawMessage — convert to lowercase string for keyword search.
		lower := strings.ToLower(string(m.Content))
		for _, kw := range complexityKeywords {
			if strings.Contains(lower, kw) {
				hits++
			}
		}
	}
	switch {
	case hits == 0:
		return 0
	case hits == 1:
		return 20
	case hits <= 3:
		return 50
	case hits <= 6:
		return 75
	default:
		return 100
	}
}

func scoreStructuredOutput(req *models.ChatCompletionRequest) int {
	if req.ResponseFormat == nil {
		return 0
	}
	// ResponseFormat is a struct with Type field.
	switch req.ResponseFormat.Type {
	case "json_schema":
		return 70
	case "json_object":
		return 50
	default:
		return 0
	}
}

func scoreMaxTokens(maxTokens *int) int {
	if maxTokens == nil {
		return 30
	}
	mt := *maxTokens
	switch {
	case mt <= 0:
		return 30
	case mt < 500:
		return 20
	case mt < 2000:
		return 40
	case mt < 4000:
		return 60
	default:
		return 80
	}
}

// SetComplexityMetadata stores complexity analysis results on the request context.
func SetComplexityMetadata(rc *models.RequestContext, result ComplexityResult, originalModel string) {
	rc.SetMetadata("complexity_score", strconv.Itoa(result.Score))
	rc.SetMetadata("complexity_tier", result.Tier)
	rc.SetMetadata("complexity_original_model", originalModel)
}
