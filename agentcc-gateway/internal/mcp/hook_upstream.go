package mcp

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Step 17 of the tools/call chain, and the upstream reads of resources/read
// and prompts/get (chain.go).
const (
	hookCallUpstream = "callUpstream"
	hookReadResource = "readResource"
	hookGetPrompt    = "getPrompt"
)

// callUpstream sends the call to the upstream server, bounded by
// tool_call_timeout, and keeps the result in cc.Result.
//
// Default: client.CallTool on the registered client. Refusal: upstream_error.
// Keep observeHook as its first statement.
func callUpstream(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCallUpstream)
	regTool := cc.legacy.tool
	if regTool == nil {
		return upstreamUnavailable(cc.Upstream.ServerID)
	}
	client, d := s.healthyClient(regTool.Server)
	if d != nil {
		return d
	}

	start := time.Now()
	ctx, cancel := context.WithTimeout(cc.Context(), s.toolTimeout)
	defer cancel()

	cc.Dispatched = true
	cc.Timings.UpstreamStarted = start
	result, err := client.CallTool(ctx, regTool.OrigName, cc.legacy.arguments)
	duration := time.Since(start)
	cc.Timings.UpstreamEnded = start.Add(duration)

	// Record stats.
	regTool.Stats.RecordCall(duration, err != nil || (result != nil && result.IsError))

	if err != nil {
		slog.Error("mcp tool call failed",
			"tool", cc.Tool.ExposedName,
			"server", regTool.Server,
			"duration", duration,
			"error", err,
		)
		return upstreamError("tool call failed: "+err.Error(), "upstream")
	}
	cc.Executed = true
	cc.legacy.toolResult = result
	cc.Result = result.Raw
	cc.IsError = result.IsError
	return nil
}

// readResource reads a resource from its upstream server.
//
// Default: client.ReadResource. Refusal: upstream_error.
// Keep observeHook as its first statement.
func readResource(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookReadResource)
	regRes := cc.legacy.resource
	if regRes == nil {
		return upstreamUnavailable("")
	}
	client, d := s.healthyClient(regRes.Server)
	if d != nil {
		return d
	}

	ctx, cancel := context.WithTimeout(cc.Context(), s.toolTimeout)
	defer cancel()

	result, err := client.ReadResource(ctx, cc.legacy.resourceURI)
	if err != nil {
		return upstreamError("resources/read failed: "+err.Error(), "upstream")
	}
	cc.legacy.readResult = result
	cc.Result = result.Raw
	return nil
}

// getPrompt gets a prompt from its upstream server, under its upstream name.
//
// Default: client.GetPrompt. Refusal: upstream_error.
// Keep observeHook as its first statement.
func getPrompt(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookGetPrompt)
	regPrompt := cc.legacy.prompt
	if regPrompt == nil {
		return upstreamUnavailable("")
	}
	client, d := s.healthyClient(regPrompt.Server)
	if d != nil {
		return d
	}

	// Forward with original (un-namespaced) prompt name.
	origName := regPrompt.Prompt.Name
	// Strip the server prefix to get original name.
	if idx := len(regPrompt.Server) + len(s.registry.Separator()); idx < len(origName) {
		origName = origName[idx:]
	}

	ctx, cancel := context.WithTimeout(cc.Context(), s.toolTimeout)
	defer cancel()

	result, err := client.GetPrompt(ctx, origName, cc.legacy.promptArgs)
	if err != nil {
		return upstreamError("prompts/get failed: "+err.Error(), "upstream")
	}
	cc.legacy.promptResult = result
	cc.Result = result.Raw
	return nil
}

// healthyClient returns the registered client of serverID, or the
// unavailable error when it is missing or unhealthy.
func (s *Server) healthyClient(serverID string) (*Client, *Denial) {
	s.clientsMu.RLock()
	client, ok := s.clients[serverID]
	s.clientsMu.RUnlock()
	if !ok || !client.Healthy() {
		return nil, upstreamUnavailable(serverID)
	}
	return client, nil
}

// upstreamUnavailable is the answer when the server's client is missing
// or unhealthy.
func upstreamUnavailable(serverID string) *Denial {
	return upstreamError(fmt.Sprintf("upstream server %q is unavailable", serverID), "network")
}

// upstreamError is an upstream_error with a plain JSON-RPC code and message
// (metadata.attribution: upstream, network or timeout).
func upstreamError(message, attribution string) *Denial {
	d := rpcDenial(ErrCodeInternal, "upstream_error", message)
	d.Type = models.ErrTypeUpstream
	d.Metadata = map[string]any{"attribution": attribution}
	return d
}
