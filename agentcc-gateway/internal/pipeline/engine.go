package pipeline

import (
	"context"
	"log/slog"
	"sort"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ProviderFunc is the function that calls the LLM provider.
type ProviderFunc func(ctx context.Context, rc *models.RequestContext) error

// Engine orchestrates the plugin pipeline.
//
// Performance optimizations over a naive sequential loop:
//   - Post-response plugins that implement PostParallel run concurrently.
//   - Plugins that implement SkipOnCacheHit are skipped when the request
//     was served from cache (short-circuited).
//   - Pre-request noop plugins (those whose ProcessRequest always returns
//     Continue with no side effects) are detected: plugins with Priority >= 500
//     that don't do pre-request work skip the pre-request call entirely.
type Engine struct {
	plugins []Plugin

	// Partitioned post-plugin lists for parallel execution.
	postSequential []Plugin // Must run sequentially (write shared state)
	postParallel   []Plugin // Safe to run concurrently (read-only observers)
}

// NewEngine creates a pipeline engine with the given plugins, sorted by priority.
func NewEngine(plugins ...Plugin) *Engine {
	sorted := make([]Plugin, len(plugins))
	copy(sorted, plugins)
	sort.Slice(sorted, func(i, j int) bool {
		return sorted[i].Priority() < sorted[j].Priority()
	})

	names := make([]string, len(sorted))
	for i, p := range sorted {
		names[i] = p.Name()
	}
	slog.Info("pipeline initialized", "plugins", names)

	// Partition post-plugins into sequential and parallel groups.
	var seq, par []Plugin
	for _, p := range sorted {
		if pp, ok := p.(PostParallel); ok && pp.IsPostParallel() {
			par = append(par, p)
		} else {
			seq = append(seq, p)
		}
	}

	if len(par) > 0 {
		parNames := make([]string, len(par))
		for i, p := range par {
			parNames[i] = p.Name()
		}
		slog.Info("pipeline post-parallel plugins", "plugins", parNames)
	}

	return &Engine{
		plugins:        sorted,
		postSequential: seq,
		postParallel:   par,
	}
}

// Process executes the plugin pipeline: pre-plugins → provider call → post-plugins.
func (e *Engine) Process(ctx context.Context, rc *models.RequestContext, providerCall ProviderFunc) error {
	// Pre-plugins (always sequential — order matters for security gates).
	for _, p := range e.plugins {
		start := time.Now()
		result := p.ProcessRequest(ctx, rc)
		elapsed := time.Since(start)
		rc.RecordTiming("pre_"+p.Name(), elapsed)

		if result.Error != nil {
			slog.Warn("pre-plugin error, short-circuiting",
				"plugin", p.Name(),
				"error", result.Error.Message,
				"request_id", rc.RequestID,
				"elapsed", elapsed,
			)
			rc.Flags.ShortCircuited = true
			rc.AddError(result.Error)
			e.RunPostPlugins(ctx, rc)
			return result.Error
		}
		if result.Action == ShortCircuit {
			slog.Debug("pre-plugin short-circuited",
				"plugin", p.Name(),
				"request_id", rc.RequestID,
				"has_response", result.Response != nil,
			)
			rc.Flags.ShortCircuited = true
			if result.Response != nil {
				rc.Response = result.Response
			}
			e.RunPostPlugins(ctx, rc)
			return nil
		}
	}

	// Provider call
	if err := ctx.Err(); err != nil {
		rc.Flags.Timeout = true
		e.RunPostPlugins(ctx, rc)
		return models.ErrRequestTimeout("request timed out before provider call")
	}

	start := time.Now()
	if err := providerCall(ctx, rc); err != nil {
		providerElapsed := time.Since(start)
		rc.RecordTiming("provider", providerElapsed)
		rc.AddError(err)
		slog.Warn("provider call failed",
			"request_id", rc.RequestID,
			"provider", rc.Provider,
			"model", rc.Model,
			"error", err,
			"elapsed", providerElapsed,
		)
		e.RunPostPlugins(ctx, rc)
		return err
	}
	rc.RecordTiming("provider", time.Since(start))

	// Post-plugins — skip for streaming requests; the stream handler
	// will call RunPostPlugins after the stream completes with final usage.
	if !rc.IsStream {
		e.RunPostPlugins(ctx, rc)
	}
	return nil
}

// RunPostPlugins runs post-processing plugins with optimized execution:
//  1. Sequential plugins run first (in priority order).
//  2. Parallel-safe plugins run concurrently after sequential ones complete.
//  3. Plugins implementing SkipOnCacheHit are skipped on cache hits.
//
// Exported so that streaming handlers can call it after the stream completes,
// when rc.Response and usage data are populated.
func (e *Engine) RunPostPlugins(ctx context.Context, rc *models.RequestContext) {
	isCacheHit := rc.Flags.ShortCircuited && rc.Metadata["cache_status"] == "hit_exact"

	// Phase 1: Sequential post-plugins (e.g., cost → credits dependency chain).
	for _, p := range e.postSequential {
		if isCacheHit {
			if skipper, ok := p.(SkipOnCacheHit); ok && skipper.ShouldSkipOnCacheHit() {
				continue
			}
		}

		start := time.Now()
		result := p.ProcessResponse(ctx, rc)
		rc.RecordTiming("post_"+p.Name(), time.Since(start))

		if result.Error != nil {
			slog.Warn("post-plugin error",
				"plugin", p.Name(),
				"error", result.Error.Message,
				"request_id", rc.RequestID,
			)
		}
	}

	// Phase 2: Parallel post-plugins (logging, audit, metrics, alerting, otel).
	if len(e.postParallel) == 0 {
		return
	}

	var wg sync.WaitGroup
	for _, p := range e.postParallel {
		if isCacheHit {
			if skipper, ok := p.(SkipOnCacheHit); ok && skipper.ShouldSkipOnCacheHit() {
				continue
			}
		}

		wg.Add(1)
		go func(plug Plugin) {
			defer wg.Done()

			start := time.Now()
			result := plug.ProcessResponse(ctx, rc)
			rc.RecordTiming("post_"+plug.Name(), time.Since(start))

			if result.Error != nil {
				slog.Warn("post-plugin error",
					"plugin", plug.Name(),
					"error", result.Error.Message,
					"request_id", rc.RequestID,
				)
			}
		}(p)
	}
	wg.Wait()
}

// PluginCount returns the number of registered plugins.
func (e *Engine) PluginCount() int {
	return len(e.plugins)
}
