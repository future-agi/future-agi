package capability

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Plugin publishes a capability call's attempts and billing onto the request
// in the post phase, right after the cost plugin (post priority 1) and
// before the credits plugin and the parallel logging and tracing plugins.
// The cost plugin re-prices recorded attempts from a chat response, which a
// capability call has none of; publishing after it keeps the core's prices.
type Plugin struct{}

// NewPlugin returns the publisher.
func NewPlugin() *Plugin { return &Plugin{} }

// PluginPostPriority is the publisher's place among the sequential post
// plugins: after cost (1), before everything else.
const PluginPostPriority = 2

// Name is "capability".
func (*Plugin) Name() string { return "capability" }

// Priority places its (empty) pre phase after the credits reserve.
func (*Plugin) Priority() int { return 511 }

// PostPriority places its post phase right after the cost plugin.
func (*Plugin) PostPriority() int { return PluginPostPriority }

// ProcessRequest does nothing: a capability call holds its own credit, per
// attempt, inside Do.
func (*Plugin) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// ProcessResponse publishes the request's capability call, if it made one.
func (*Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	Publish(rc)
	return pipeline.ResultContinue()
}
