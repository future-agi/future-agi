package routing

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/rand"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// MirrorProvider is the interface needed by the mirror to call a target provider.
type MirrorProvider interface {
	ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error)
}

// MirrorProviderLookup resolves a provider by ID for mirroring.
type MirrorProviderLookup func(providerID string) (MirrorProvider, bool)

// MirrorRule is a resolved mirroring rule.
type MirrorRule struct {
	SourceModel    string
	TargetProvider string
	TargetModel    string
	SampleRate     float64
	ExperimentID   string
}

// ProductionInfo carries production-side data needed to build a ShadowResult.
// Passed to ExecuteAsync so the goroutine can build the complete comparison.
type ProductionInfo struct {
	RequestID  string
	Model      string
	Response   string // first 2000 chars of response text
	LatencyMs  int64
	Tokens     int // total tokens
	StatusCode int
}

// Mirror manages traffic mirroring for shadow testing.
type Mirror struct {
	rules  []MirrorRule
	lookup MirrorProviderLookup
	Store  *ShadowStore // nil when capture is disabled
}

// NewMirror creates a mirror from config rules.
func NewMirror(cfg config.MirrorConfig, lookup MirrorProviderLookup) *Mirror {
	if !cfg.Enabled || len(cfg.Rules) == 0 {
		return nil
	}

	rules := make([]MirrorRule, len(cfg.Rules))
	for i, r := range cfg.Rules {
		rate := r.SampleRate
		if rate <= 0 {
			rate = 0
		}
		if rate > 1 {
			rate = 1
		}
		rules[i] = MirrorRule{
			SourceModel:    r.SourceModel,
			TargetProvider: r.TargetProvider,
			TargetModel:    r.TargetModel,
			SampleRate:     rate,
			ExperimentID:   r.ExperimentID,
		}
	}

	return &Mirror{
		rules:  rules,
		lookup: lookup,
	}
}

// ShouldMirror checks if a request for the given model should be mirrored.
// Returns the matching rule and true if mirroring should occur.
func (m *Mirror) ShouldMirror(model string) (*MirrorRule, bool) {
	if m == nil {
		return nil, false
	}

	for i := range m.rules {
		r := &m.rules[i]
		// Match model: exact or wildcard "*".
		if r.SourceModel != "*" && r.SourceModel != model {
			continue
		}
		// Sample rate check.
		if r.SampleRate <= 0 {
			continue
		}
		if r.SampleRate < 1.0 && rand.Float64() >= r.SampleRate {
			continue
		}
		return r, true
	}
	return nil, false
}

// ExecuteAsync sends a mirrored request to the target provider asynchronously.
// This is fire-and-forget — errors are logged but never returned.
// When Store is non-nil, the shadow response is captured and stored along with
// the production info for later comparison.
func (m *Mirror) ExecuteAsync(req *models.ChatCompletionRequest, sourceProvider, sourceModel string, prod *ProductionInfo) {
	if m == nil || req == nil {
		return
	}

	rule, ok := m.ShouldMirror(req.Model)
	if !ok {
		return
	}

	// Look up the target provider.
	provider, found := m.lookup(rule.TargetProvider)
	if !found {
		slog.Warn("mirror target provider not found",
			"target_provider", rule.TargetProvider,
			"source_model", sourceModel,
		)
		return
	}

	// Copy the request to avoid mutation.
	mirrorReq := *req
	if rule.TargetModel != "" {
		mirrorReq.Model = rule.TargetModel
	}
	mirrorReq.Stream = false // Never stream mirrors.

	// Capture references for the goroutine closure.
	store := m.Store
	experimentID := rule.ExperimentID
	promptHash := hashPrompt(req)

	go func() {
		defer func() {
			if r := recover(); r != nil {
				slog.Error("mirror goroutine panicked",
					"panic", r,
					"target_provider", rule.TargetProvider,
					"target_model", mirrorReq.Model,
				)
			}
		}()

		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()

		start := time.Now()
		resp, err := provider.ChatCompletion(ctx, &mirrorReq)
		elapsed := time.Since(start)

		if err != nil {
			slog.Debug("mirror request failed",
				"target_provider", rule.TargetProvider,
				"target_model", mirrorReq.Model,
				"source_provider", sourceProvider,
				"source_model", sourceModel,
				"error", err,
				"elapsed_ms", elapsed.Milliseconds(),
			)

			// Store the failed shadow result if capture is enabled.
			if store != nil && prod != nil {
				store.Add(ShadowResult{
					RequestID:       prod.RequestID,
					ExperimentID:    experimentID,
					SourceModel:     sourceModel,
					ShadowModel:     mirrorReq.Model,
					SourceResponse:  prod.Response,
					ShadowResponse:  "",
					SourceLatencyMs: prod.LatencyMs,
					ShadowLatencyMs: elapsed.Milliseconds(),
					SourceTokens:    prod.Tokens,
					ShadowTokens:    0,
					SourceStatus:    prod.StatusCode,
					ShadowStatus:    0,
					ShadowError:     err.Error(),
					PromptHash:      promptHash,
					CreatedAt:       time.Now(),
				})
			}
		} else {
			slog.Debug("mirror request completed",
				"target_provider", rule.TargetProvider,
				"target_model", mirrorReq.Model,
				"source_provider", sourceProvider,
				"source_model", sourceModel,
				"elapsed_ms", elapsed.Milliseconds(),
			)

			// Store the successful shadow result if capture is enabled.
			if store != nil && prod != nil {
				shadowTokens := 0
				if resp != nil && resp.Usage != nil {
					shadowTokens = resp.Usage.TotalTokens
				}

				store.Add(ShadowResult{
					RequestID:       prod.RequestID,
					ExperimentID:    experimentID,
					SourceModel:     sourceModel,
					ShadowModel:     mirrorReq.Model,
					SourceResponse:  prod.Response,
					ShadowResponse:  ExtractResponseText(resp),
					SourceLatencyMs: prod.LatencyMs,
					ShadowLatencyMs: elapsed.Milliseconds(),
					SourceTokens:    prod.Tokens,
					ShadowTokens:    shadowTokens,
					SourceStatus:    prod.StatusCode,
					ShadowStatus:    200,
					PromptHash:      promptHash,
					CreatedAt:       time.Now(),
				})
			}
		}
	}()
}

// ExtractResponseText extracts the text content from a chat completion response.
// Content is json.RawMessage — could be a JSON string or an array of content parts.
// Truncates to 2000 characters.
func ExtractResponseText(resp *models.ChatCompletionResponse) string {
	if resp == nil || len(resp.Choices) == 0 {
		return ""
	}
	raw := resp.Choices[0].Message.Content
	if len(raw) == 0 {
		return ""
	}

	// Try to unmarshal as a plain string first (most common case).
	var text string
	if err := json.Unmarshal(raw, &text); err == nil {
		if len(text) > 2000 {
			text = text[:2000]
		}
		return text
	}

	// Fallback: return the raw JSON (truncated).
	s := string(raw)
	if len(s) > 2000 {
		s = s[:2000]
	}
	return s
}

// hashPrompt creates a short hash of the prompt messages for dedup/matching.
func hashPrompt(req *models.ChatCompletionRequest) string {
	if req == nil || len(req.Messages) == 0 {
		return ""
	}
	data, err := json.Marshal(req.Messages)
	if err != nil {
		return ""
	}
	h := sha256.Sum256(data)
	return fmt.Sprintf("%x", h[:8])
}
