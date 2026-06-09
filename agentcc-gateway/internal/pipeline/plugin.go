package pipeline

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Action represents what the pipeline should do after a plugin runs.
type Action int

const (
	// Continue tells the pipeline to proceed to the next plugin.
	Continue Action = iota
	// ShortCircuit tells the pipeline to skip the provider call (e.g., cache hit, rate limit).
	ShortCircuit
)

// PluginResult is returned by a plugin after processing.
type PluginResult struct {
	Action   Action
	Response *models.ChatCompletionResponse
	Error    *models.APIError
}

// ResultContinue returns a result that continues the pipeline.
func ResultContinue() PluginResult {
	return PluginResult{Action: Continue}
}

// ResultShortCircuit returns a result that stops the pipeline with a response.
func ResultShortCircuit(resp *models.ChatCompletionResponse) PluginResult {
	return PluginResult{Action: ShortCircuit, Response: resp}
}

// ResultError returns a result that stops the pipeline with an error.
func ResultError(err *models.APIError) PluginResult {
	return PluginResult{Action: ShortCircuit, Error: err}
}

// Plugin is the interface that all pipeline plugins implement.
type Plugin interface {
	// Name returns the plugin's identifier.
	Name() string

	// Priority returns the execution order (lower runs first).
	Priority() int

	// ProcessRequest is called before the provider call.
	// Return ShortCircuit to skip the provider call.
	ProcessRequest(ctx context.Context, rc *models.RequestContext) PluginResult

	// ProcessResponse is called after the provider call (or after short-circuit).
	// Post-plugins always run, even if the provider returned an error.
	ProcessResponse(ctx context.Context, rc *models.RequestContext) PluginResult
}

// PostParallel is an optional interface plugins can implement to indicate their
// ProcessResponse can safely run concurrently with other parallel-safe plugins.
// Plugins that only read from RequestContext (logging, metrics, audit, alerting)
// should implement this. Plugins that write shared state (cost→credits chain)
// must NOT implement this.
type PostParallel interface {
	// IsPostParallel returns true if ProcessResponse is safe to run concurrently.
	IsPostParallel() bool
}

// SkipOnCacheHit is an optional interface plugins can implement to indicate
// their ProcessResponse should be skipped when the request was served from cache.
// Cost calculation and credits deduction are meaningless on cache hits.
type SkipOnCacheHit interface {
	// ShouldSkipOnCacheHit returns true if this plugin's post-processing
	// should be skipped when rc.Flags.ShortCircuited is true from a cache hit.
	ShouldSkipOnCacheHit() bool
}
