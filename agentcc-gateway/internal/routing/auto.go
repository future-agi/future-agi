package routing

import (
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

// Auto routing: a request for `model: "auto"` is sent to the model that best
// fits it among the models its key can reach.
//
// The choice is deterministic and explained. A model qualifies when it
// supports what the request needs (tools, structured output, images, context
// length, output length) and is inside the org's price caps, and its
// provider's circuit is not open. Qualifying models are placed on a tier
// scale by their price relative to each other: a vendor's price is its own
// statement of where a model sits in its lineup. The request's complexity
// (ScoreComplexity) and the org's preference set the tier it should get:
// simple prompts go to the economical end, complex ones to the frontier.
// Provider latency and error rate break near-ties.

// Auto preferences.
const (
	AutoPreferCost     = "cost"
	AutoPreferBalanced = "balanced"
	AutoPreferQuality  = "quality"
)

// AutoCandidate is a model the request's key can reach.
type AutoCandidate struct {
	Model      string             // the model name resolution takes
	ProviderID string             // the provider that would serve it
	Info       *modeldb.ModelInfo // nil when nothing is known about the model
	Health     *ProviderHealth    // nil when the provider has no history
}

// AutoPolicy is the org's tuning, merged with the request's override.
type AutoPolicy struct {
	Preference       string
	MaxInputPerMTok  float64
	MaxOutputPerMTok float64
	// Require is the capabilities the request requires of the model (the
	// agent context's require: tools, json_schema, vision, reasoning, audio,
	// long_context). A candidate without one of them is set aside.
	Require []string
}

// AutoLongContextTokens is the context window, in input tokens, a model needs
// to meet require long_context.
const AutoLongContextTokens = 128000

// supportsRequired reports whether info has capability (one of
// models.AgentRequireCaps): tools is function calling, json_schema structured
// output, vision image input, reasoning reasoning, audio audio input or
// output, and long_context a context window of at least
// AutoLongContextTokens.
func supportsRequired(info *modeldb.ModelInfo, capability string) bool {
	c := info.Capabilities
	switch capability {
	case "tools":
		return c.FunctionCalling
	case "json_schema":
		return c.ResponseSchema
	case "vision":
		return c.Vision
	case "reasoning":
		return c.Reasoning
	case "audio":
		return c.AudioInput || c.AudioOutput
	case "long_context":
		return info.MaxInputTokens >= AutoLongContextTokens
	default:
		return false
	}
}

// AutoRejection says why a candidate was not eligible.
type AutoRejection struct {
	Model  string `json:"model"`
	Reason string `json:"reason"`
}

// AutoRanked is an eligible candidate and its score.
type AutoRanked struct {
	Model       string  `json:"model"`
	ProviderID  string  `json:"provider"`
	Tier        float64 `json:"tier"`  // 0 = most economical, 1 = frontier, among the eligible
	Score       float64 `json:"score"` // higher is better
	EstCostUSD  float64 `json:"est_cost_usd"`
	LatencyMs   int64   `json:"latency_ms,omitempty"`
	SuccessRate float64 `json:"success_rate,omitempty"`
}

// AutoDecision is the outcome and the account of it.
type AutoDecision struct {
	Model      string          `json:"model"`
	ProviderID string          `json:"provider"`
	TaskTier   string          `json:"task_tier"` // simple, standard or complex
	Complexity int             `json:"complexity"`
	Preference string          `json:"preference"`
	Needs      []string        `json:"needs,omitempty"`
	Required   []string        `json:"required,omitempty"`
	Considered int             `json:"considered"`
	Ranked     []AutoRanked    `json:"ranked"`
	Rejected   []AutoRejection `json:"rejected,omitempty"`
	Fallbacks  []string        `json:"fallbacks,omitempty"`
}

// Reason is a one-line account of the decision for headers and logs.
func (d *AutoDecision) Reason() string {
	needs := "no special needs"
	if len(d.Needs) > 0 {
		needs = "needs " + strings.Join(d.Needs, ", ")
	}
	if len(d.Required) > 0 {
		needs += ", requires " + strings.Join(d.Required, ", ")
	}
	return fmt.Sprintf("%s task (complexity %d), %s, %s preference: %d of %d models eligible",
		d.TaskTier, d.Complexity, needs, d.Preference, len(d.Ranked), d.Considered)
}

// NoAutoCandidateError is returned when no reachable model fits the request.
type NoAutoCandidateError struct {
	Needs []string
	// Required is the capabilities the request required (AutoPolicy.Require).
	Required []string
	Rejected []AutoRejection
}

func (e *NoAutoCandidateError) Error() string {
	if len(e.Rejected) == 0 {
		if len(e.Required) > 0 {
			return "auto routing found no model this key can use that has the required " + strings.Join(e.Required, ", ")
		}
		return "auto routing found no model this key can use"
	}
	needs := ""
	if len(e.Needs) > 0 {
		needs = " that " + strings.Join(e.Needs, ", ")
	}
	if len(e.Required) > 0 {
		return fmt.Sprintf("auto routing found no model with the required %s among the %d this key can use",
			strings.Join(e.Required, ", "), len(e.Rejected))
	}
	return fmt.Sprintf("auto routing found no model%s among the %d this key can use", needs, len(e.Rejected))
}

// Complexity thresholds between task tiers, calibrated on the request shapes
// in TestAutoTaskTierOfRealisticRequests: a one-line question is simple, a
// multi-turn chat or an extraction is standard, and tool-using agents, long
// grounded context and analysis are complex. ScoreComplexity rarely passes
// 40 on real traffic.
const (
	autoSimpleMax   = 10
	autoStandardMax = 20
)

// autoDefaultOutputTokens is assumed for cost estimates when the request does
// not cap its output.
const autoDefaultOutputTokens = 500

// maxAutoFallbacks is how many runners-up become the fallback chain.
const maxAutoFallbacks = 2

// ChooseAuto picks the model for req among candidates.
func ChooseAuto(req *models.ChatCompletionRequest, candidates []AutoCandidate, policy AutoPolicy) (*AutoDecision, error) {
	pref := policy.Preference
	if pref != AutoPreferCost && pref != AutoPreferQuality {
		pref = AutoPreferBalanced
	}
	complexity := ScoreComplexity(req)
	d := &AutoDecision{
		TaskTier:   taskTier(complexity),
		Complexity: complexity,
		Preference: pref,
		Needs:      requestNeeds(req),
		Required:   policy.Require,
		Considered: len(candidates),
	}

	inTokens := tokenizer.EstimateTokens(req.Messages)
	outTokens := requestedOutputTokens(req)

	type eligible struct {
		AutoRanked
		blendedPerMTok float64
	}
	var pool []eligible
	seen := make(map[string]bool, len(candidates))
	for _, c := range candidates {
		key := c.ProviderID + "\x00" + c.Model
		if seen[key] {
			continue
		}
		seen[key] = true
		if reason := ineligible(c, req, policy, inTokens, outTokens); reason != "" {
			d.Rejected = append(d.Rejected, AutoRejection{Model: c.Model, Reason: reason})
			continue
		}
		p := c.Info.Pricing
		e := eligible{
			AutoRanked: AutoRanked{
				Model:      c.Model,
				ProviderID: c.ProviderID,
				EstCostUSD: float64(inTokens)*p.InputPerToken + float64(outTokens)*p.OutputPerToken,
			},
			blendedPerMTok: (3*p.InputPerToken + p.OutputPerToken) / 4 * 1_000_000,
		}
		if c.Health != nil {
			e.LatencyMs = c.Health.LatencyEWMAMs
			if c.Health.RequestCount > 0 {
				e.SuccessRate = c.Health.SuccessRate
			}
		}
		pool = append(pool, e)
	}
	if len(pool) == 0 {
		return nil, &NoAutoCandidateError{Needs: d.Needs, Required: d.Required, Rejected: d.Rejected}
	}

	// Place each model on the tier scale by log price among the eligible.
	lo, hi := math.Inf(1), math.Inf(-1)
	for _, e := range pool {
		lp := math.Log(math.Max(e.blendedPerMTok, 1e-6))
		lo, hi = math.Min(lo, lp), math.Max(hi, lp)
	}
	var maxLatency int64
	for _, e := range pool {
		if e.LatencyMs > maxLatency {
			maxLatency = e.LatencyMs
		}
	}
	target := tierTarget(d.TaskTier, pref)
	for i := range pool {
		e := &pool[i]
		e.Tier = 0.5
		if hi > lo {
			e.Tier = (math.Log(math.Max(e.blendedPerMTok, 1e-6)) - lo) / (hi - lo)
		}
		score := 1 - math.Abs(e.Tier-target)
		if maxLatency > 0 {
			score -= 0.1 * float64(e.LatencyMs) / float64(maxLatency)
		}
		if e.SuccessRate > 0 && e.SuccessRate < 0.95 {
			score -= 0.5 * (0.95 - e.SuccessRate)
		}
		e.Tier = round3(e.Tier)
		e.Score = round3(score)
	}
	sort.SliceStable(pool, func(i, j int) bool {
		if pool[i].Score != pool[j].Score {
			return pool[i].Score > pool[j].Score
		}
		if pool[i].EstCostUSD != pool[j].EstCostUSD {
			return pool[i].EstCostUSD < pool[j].EstCostUSD
		}
		return pool[i].Model < pool[j].Model
	})

	d.Ranked = make([]AutoRanked, len(pool))
	for i, e := range pool {
		d.Ranked[i] = e.AutoRanked
	}
	d.Model = pool[0].Model
	d.ProviderID = pool[0].ProviderID
	for _, e := range pool[1:] {
		if len(d.Fallbacks) == maxAutoFallbacks {
			break
		}
		d.Fallbacks = append(d.Fallbacks, e.Model)
	}
	return d, nil
}

// ineligible returns why c cannot serve req, or "".
func ineligible(c AutoCandidate, req *models.ChatCompletionRequest, policy AutoPolicy, inTokens, outTokens int) string {
	info := c.Info
	if info == nil {
		return "no price or capability data"
	}
	if info.Mode != "" && info.Mode != modeldb.ModeChat {
		return "not a chat model"
	}
	if !info.Pricing.HasPricing() {
		return "no price data"
	}
	if reason, ok := info.Capabilities.ValidateRequest(req); !ok {
		return reason
	}
	if wantsReasoning(req) && !info.Capabilities.Reasoning {
		return "model does not support reasoning"
	}
	for _, capability := range policy.Require {
		if !supportsRequired(info, capability) {
			return "lacks required " + capability
		}
	}
	if info.MaxInputTokens > 0 && inTokens > info.MaxInputTokens {
		return fmt.Sprintf("prompt of ~%d tokens exceeds its %d-token context", inTokens, info.MaxInputTokens)
	}
	if req.MaxTokens != nil && info.MaxOutputTokens > 0 && *req.MaxTokens > info.MaxOutputTokens {
		return fmt.Sprintf("max_tokens %d exceeds its %d-token output limit", *req.MaxTokens, info.MaxOutputTokens)
	}
	if policy.MaxInputPerMTok > 0 && info.Pricing.InputPerMTok() > policy.MaxInputPerMTok {
		return fmt.Sprintf("input price $%.2f/M is above the cap", info.Pricing.InputPerMTok())
	}
	if policy.MaxOutputPerMTok > 0 && info.Pricing.OutputPerMTok() > policy.MaxOutputPerMTok {
		return fmt.Sprintf("output price $%.2f/M is above the cap", info.Pricing.OutputPerMTok())
	}
	if c.Health != nil && c.Health.CircuitState == "open" {
		return "provider circuit is open"
	}
	return ""
}

func taskTier(complexity int) string {
	switch {
	case complexity <= autoSimpleMax:
		return "simple"
	case complexity <= autoStandardMax:
		return "standard"
	default:
		return "complex"
	}
}

// tierTarget is where on the tier scale a task should land.
func tierTarget(taskTier, pref string) float64 {
	t := map[string]float64{"simple": 0.1, "standard": 0.5, "complex": 0.9}[taskTier]
	switch pref {
	case AutoPreferCost:
		t -= 0.4
	case AutoPreferQuality:
		t += 0.4
	}
	return math.Max(0, math.Min(1, t))
}

func requestNeeds(req *models.ChatCompletionRequest) []string {
	var needs []string
	if len(req.Tools) > 0 {
		needs = append(needs, "tools")
	}
	if req.ResponseFormat != nil && req.ResponseFormat.Type == "json_schema" {
		needs = append(needs, "structured output")
	}
	probe := modeldb.CapabilityFlags{FunctionCalling: true, ResponseSchema: true}
	if _, ok := probe.ValidateRequest(req); !ok {
		needs = append(needs, "image input")
	}
	if wantsReasoning(req) {
		needs = append(needs, "reasoning")
	}
	return needs
}

func wantsReasoning(req *models.ChatCompletionRequest) bool {
	if req.Extra == nil {
		return false
	}
	_, effort := req.Extra["reasoning_effort"]
	_, reasoning := req.Extra["reasoning"]
	return effort || reasoning
}

func requestedOutputTokens(req *models.ChatCompletionRequest) int {
	if req.MaxCompletionTokens != nil && *req.MaxCompletionTokens > 0 {
		return *req.MaxCompletionTokens
	}
	if req.MaxTokens != nil && *req.MaxTokens > 0 {
		return *req.MaxTokens
	}
	return autoDefaultOutputTokens
}

func round3(f float64) float64 { return math.Round(f*1000) / 1000 }
