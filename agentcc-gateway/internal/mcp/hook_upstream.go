package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream/wire"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// Step 17 of the tools/call chain, and the upstream reads of resources/read
// and prompts/get (chain.go).
const (
	hookCallUpstream = "callUpstream"
	hookReadResource = "readResource"
	hookGetPrompt    = "getPrompt"
)

// MetaKeyUpstreamError carries an upstream's own JSON-RPC error, {code,
// message}, on an upstream_error result (MC.3).
const MetaKeyUpstreamError = "io.futureagi/upstream_error"

// callUpstream sends the call to the upstream server and keeps the result,
// raw, in cc.Result. The call is bounded by the server's timeout_ms, else
// tool_call_timeout, and by the caller's deadline; an upstream's JSON-RPC
// error keeps its code (MC.3 upstream_error, attribution upstream), a
// failure is attributed to the network or a timeout.
//
// Owner: M13 (decorated by M27). Refusal: upstream_error.
// Keep observeHook as its first statement.
func callUpstream(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCallUpstream)
	regTool := cc.legacy.tool
	if regTool == nil {
		return upstreamUnavailable(cc.Upstream.ServerID)
	}
	client, d := s.callClient(cc, regTool.Server)
	if d != nil {
		return d
	}
	args := cc.Args
	if len(args) == 0 && cc.legacy.arguments != nil {
		args, _ = json.Marshal(cc.legacy.arguments)
	}
	opts := s.upstreamCallOptions(cc, client, MethodToolsCall)

	// Dispatched once the request's bytes were written to the upstream (the
	// client's WithDispatchNotice), so a call that never left the gateway (a
	// failed dial, a refused connection) is neither reported nor metered as
	// executed (M15 IR-4, IC-R1-8). The notice may come from the transport's
	// goroutine: it lands in an atomic read once the call returns.
	var dispatched atomic.Bool
	ctx := WithDispatchNotice(cc.Context(), func() { dispatched.Store(true) })
	start := time.Now()
	cc.Timings.UpstreamStarted = start
	raw, err := client.CallToolRaw(ctx, regTool.OrigName, args, opts)
	duration := time.Since(start)
	cc.Timings.UpstreamEnded = start.Add(duration)
	cc.Dispatched = dispatched.Load()

	var result ToolCallResult
	if err == nil {
		err = decodeResult(raw, &result)
	}
	regTool.Stats.RecordCall(duration, err != nil || result.IsError)
	if err != nil {
		slog.Error("mcp tool call failed",
			"tool", cc.Tool.ExposedName,
			"server", regTool.Server,
			"duration", duration,
			// Raw upstream error text can contain PII even after credential scrubbing.
			"error_origin", upstreamAttribution(err),
		)
		return upstreamFailure(cc, "tool call failed", err)
	}
	cc.Executed = true
	cc.legacy.toolResult = &result
	cc.Result = raw
	cc.IsError = result.IsError
	return nil
}

// readResource reads a resource from its upstream server, raw.
//
// Owner: M13. Refusal: the upstream's own JSON-RPC error, else
// upstream_error. Keep observeHook as its first statement.
func readResource(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookReadResource)
	regRes := cc.legacy.resource
	if regRes == nil {
		return upstreamUnavailable("")
	}
	client, d := s.callClient(cc, regRes.Server)
	if d != nil {
		return d
	}
	raw, err := client.ReadResourceRaw(cc.Context(), cc.legacy.resourceURI, s.upstreamCallOptions(cc, client, MethodResourcesRead))
	var result ReadResourceResult
	if err == nil {
		err = decodeResult(raw, &result)
	}
	if err != nil {
		return protocolFailure(upstreamFailure(cc, "resources/read failed", err))
	}
	cc.legacy.readResult = &result
	cc.Result = raw
	return nil
}

// getPrompt gets a prompt from its upstream server, under its upstream name.
//
// Owner: M13. Refusal: the upstream's own JSON-RPC error, else
// upstream_error. Keep observeHook as its first statement.
func getPrompt(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookGetPrompt)
	regPrompt := cc.legacy.prompt
	if regPrompt == nil {
		return upstreamUnavailable("")
	}
	client, d := s.callClient(cc, regPrompt.Server)
	if d != nil {
		return d
	}

	// Forward with original (un-namespaced) prompt name.
	origName := regPrompt.Prompt.Name
	// Strip the server prefix to get original name.
	if idx := len(regPrompt.Server) + len(s.registry.Separator()); idx < len(origName) {
		origName = origName[idx:]
	}
	var args json.RawMessage
	if cc.legacy.promptArgs != nil {
		args, _ = json.Marshal(cc.legacy.promptArgs)
	}
	raw, err := client.GetPromptRaw(cc.Context(), origName, args, s.upstreamCallOptions(cc, client, MethodPromptsGet))
	var result GetPromptResult
	if err == nil {
		err = decodeResult(raw, &result)
	}
	if err != nil {
		return protocolFailure(upstreamFailure(cc, "prompts/get failed", err))
	}
	cc.legacy.promptResult = &result
	cc.Result = raw
	return nil
}

// upstreamCallOptions are one call's CallOptions: tool_call_timeout (the
// client prefers the server's own timeout_ms), the caller's progressToken,
// the _meta DecorateUpstream adds (M27), and the client's headers the server
// forwards plus the Mcp-Param-* ones the client regenerates. Nothing else
// of the client's request (its _meta, its other headers) goes upstream; a
// per-call credential is M25's.
func (s *Server) upstreamCallOptions(cc *CallContext, client dispatchClient, method string) CallOptions {
	opts := CallOptions{Timeout: s.toolTimeout}
	if msg := cc.Message(); msg != nil {
		if tok, ok := wire.MetaOf(msg.Params)[wire.MetaProgressToken]; ok && len(tok) > 0 {
			opts.ProgressToken = string(tok)
		}
	}
	out := &Message{JSONRPC: "2.0", Method: method, Params: json.RawMessage(`{"_meta":{}}`)}
	s.Deps().DecorateUpstream(cc, out)
	if meta := wire.MetaOf(out.Params); len(meta) > 0 {
		opts.Meta = meta
	}
	if r := cc.Request(); r != nil {
		opts.ForwardHeaders = forwardedRequestHeaders(r.Header, client.CallSettings().ForwardHeaders)
	}
	return opts
}

// forwardedRequestHeaders are the client's headers a call hands the
// upstream client: the server's forward_headers and the Mcp-Param-* headers
// (which the client regenerates from the arguments, never copies).
func forwardedRequestHeaders(inbound http.Header, allow []string) http.Header {
	h := wire.Forwarded(inbound, allow)
	for name, vs := range inbound {
		if strings.HasPrefix(http.CanonicalHeaderKey(name), wire.HeaderParamPrefix) {
			h[http.CanonicalHeaderKey(name)] = append([]string(nil), vs...)
		}
	}
	return h
}

// healthyClient returns the registered (static) client of serverID, or
// today's unavailable error when it is missing or unhealthy. A call goes
// through callClient (pool_members.go), which uses it only on the legacy view.
func (s *Server) healthyClient(serverID string) (*Client, *Denial) {
	s.clientsMu.RLock()
	client, ok := s.clients[serverID]
	s.clientsMu.RUnlock()
	if !ok || !client.Healthy() {
		return nil, upstreamUnavailable(serverID)
	}
	return client, nil
}

// upstreamUnavailable is today's answer when the server's client is missing
// or unhealthy.
func upstreamUnavailable(serverID string) *Denial {
	return upstreamError(fmt.Sprintf("upstream server %q is unavailable", serverID), "network")
}

// upstreamError is an upstream_error with today's JSON-RPC code and message
// (MC.3 metadata.attribution: upstream, network or timeout).
func upstreamError(message, attribution string) *Denial {
	d := rpcDenial(ErrCodeInternal, "upstream_error", message)
	d.Type = models.ErrTypeUpstream
	d.Metadata = map[string]any{"attribution": attribution}
	return d
}

// upstreamFailure is MC.3's in-band upstream_error for a call that failed:
// an upstream JSON-RPC error keeps its code in metadata.upstream_code and in
// _meta["io.futureagi/upstream_error"]; anything else is attributed to the
// network or a timeout. Messages are scrubbed of credentials.
func upstreamFailure(cc *CallContext, what string, err error) *Denial {
	var denial *Denial
	if errors.As(err, &denial) {
		return denial
	}
	attribution := upstreamAttribution(err)
	meta := map[string]any{"attribution": attribution}
	message := what + ": " + scrubbed(err)
	var ue *UpstreamError
	if errors.As(err, &ue) {
		meta["upstream_code"] = ue.Code
		message = "upstream error " + strconv.Itoa(ue.Code) + ": " + privacy.RedactCredentials(ue.Message, scrubMarker)
		cc.SetResponseMeta(MetaKeyUpstreamError, map[string]any{"code": ue.Code, "message": privacy.RedactCredentials(ue.Message, scrubMarker)})
	}
	return &Denial{Type: models.ErrTypeUpstream, Code: "upstream_error", Message: message, Metadata: meta}
}

// protocolFailure answers a failed read or prompt as a JSON-RPC error: the
// upstream's own code when it sent one, else -32603.
func protocolFailure(d *Denial) *Denial {
	// Application denials retain their actionable ErrorObject in writeDenialRPC.
	// Only upstream failures use the upstream JSON-RPC code conversion.
	if d.Code != "upstream_error" {
		return d
	}
	d.RPCCode = ErrCodeInternal
	if meta, ok := d.Metadata.(map[string]any); ok {
		if code, ok := meta["upstream_code"].(int); ok {
			d.RPCCode = code
		}
	}
	return d
}

// upstreamAttribution is MC.3's attribution of a failed call.
func upstreamAttribution(err error) string {
	var ue *UpstreamError
	var se *HTTPStatusError
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		return "timeout"
	case errors.As(err, &ue), errors.As(err, &se), errors.Is(err, ErrResultTooLarge),
		errors.Is(err, wire.ErrNoAnswer), errors.Is(err, errSessionNotFound), errors.Is(err, errUndecodable):
		return "upstream"
	}
	return "network"
}

// decodeResult decodes an upstream result; one that does not decode is the
// upstream's fault.
func decodeResult(raw json.RawMessage, v any) error {
	if err := json.Unmarshal(raw, v); err != nil {
		return fmt.Errorf("%w: %w", errUndecodable, err)
	}
	return nil
}

func scrubMarker(string) string { return "[redacted]" }

// scrubbed is err's message with credentials removed (an upstream URL can
// carry one in its query).
func scrubbed(err error) string { return privacy.RedactCredentials(err.Error(), scrubMarker) }

// StaticClientConfig is the ClientConfig of the YAML server id: its own keys
// (the era, the call settings and the stdio environment included) and
// mcp.max_result_bytes. The operator's mcp.stdio defaults are the pool's to
// add (M9's StaticPoolClientConfig, which every static client is built
// through: BuildStaticPool at route setup and the tenancy's static pool).
func StaticClientConfig(id string, sc config.MCPServerConfig, cfg config.MCPConfig) ClientConfig {
	return ClientConfig{
		ServerID:      id,
		URL:           sc.URL,
		Command:       sc.Command,
		Args:          sc.Args,
		TransportType: sc.Transport,
		Auth:          AuthConfig{Type: sc.Auth.Type, Token: sc.Auth.Token, Header: sc.Auth.Header, Key: sc.Auth.Key},
		ToolsCacheTTL: sc.ToolsCacheTTL,
		Era:           sc.Era,
		Call: CallSettings{
			Timeout:        time.Duration(sc.TimeoutMS) * time.Millisecond,
			ForwardHeaders: sc.ForwardHeaders,
			MaxResultBytes: cfg.MaxResultBytes,
		},
		Env:         sc.Env,
		EnvFromHost: sc.EnvFromHost,
	}
}
