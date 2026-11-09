package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/mcp/telemetry"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream/wire"
)

// hookStartSpan is step 0 of every chain (chain.go).
const hookStartSpan = "startSpan"

// Register first so tracing reads settled costs after every other finalizer.
// This milestone traces tools/call only; other MCP methods remain unchanged.
func startSpan(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookStartSpan)
	if cc.Method == MethodToolsCall {
		if finish := s.Deps().Tracer.Start(cc); finish != nil {
			cc.Defer(FinalizerSpan, finish)
		}
	}
	return nil
}

type noTracer struct{}

func (noTracer) Start(*CallContext) func() { return func() {} }

type toolTracer struct{ opts telemetry.Options }
type toolTraceKey struct{}

// NewToolTracer borrows an emitter. It never creates or closes an exporter.
func NewToolTracer(opts telemetry.Options) Tracer { return &toolTracer{opts: opts} }

func (t *toolTracer) Start(cc *CallContext) func() {
	if t.opts.Export == nil || (t.opts.Sample != nil && !t.opts.Sample(cc.RequestID)) {
		return func() {}
	}
	var meta, header string
	if msg := cc.Message(); msg != nil {
		_ = json.Unmarshal(wire.MetaOf(msg.Params)[metaKeyTraceparent], &meta)
	}
	if req := cc.Request(); req != nil {
		header = req.Header.Get(headerTraceparent)
	}
	trace := telemetry.Start(t.opts, telemetry.Parent(meta, header), cc.Timings.Started)
	cc.ctx = context.WithValue(cc.Context(), toolTraceKey{}, trace)
	var once sync.Once
	return func() {
		once.Do(func() {
			// Reuse M15's classification, content resolver, redactor and retention.
			in := meter.Input{HasResult: cc.Executed || len(cc.Result) > 0, IsError: cc.IsError, Cancelled: errors.Is(cc.Context().Err(), context.Canceled), TimedOut: errors.Is(cc.Context().Err(), context.DeadlineExceeded)}
			if d := cc.Decision; d != nil {
				in.Denied = true
				in.Code = d.Code
				if m, ok := d.Metadata.(map[string]any); ok {
					in.Attribution, _ = m["attribution"].(string)
				}
			}
			class := meter.Classify(in)
			mode := recordContentMode(cc)
			r := meter.Default().Redactor(cc.Principal.OrgID)
			attrs := map[string]any{
				"gen_ai.operation.name": "execute_tool", "gen_ai.span.kind": "tool",
				"mcp.outcome": class.Outcome, "agentcc.billing_mode": callBillingMode(cc),
				"mcp.billed_cost_micros": cc.Billing.BilledMicros, "mcp.upstream_cost_micros": cc.Billing.UpstreamMicros,
				"agentcc.cost_usd":  float64(cc.Billing.BilledMicros) / 1e6,
				"mcp.markup_micros": cc.Billing.MarkupMicros, "mcp.dispatched": cc.Dispatched,
				"mcp.content_mode": mode, "mcp.session.id": sessionHash(cc.SessionID),
				"mcp.method.name": MethodToolsCall,
			}
			tool := cc.Tool.UpstreamName
			if tool == "" {
				tool = meter.UnresolvedTool
			}
			// Only these bounded scalar dimensions are approved; never copy headers,
			// URLs, session IDs, response metadata, error text or caller baggage.
			for k, v := range map[string]string{
				"gen_ai.tool.name": tool, "gen_ai.tool.call.id": cc.ToolCallID,
				"agentcc.mcp.server_id": cc.Upstream.ServerID, "agentcc.mcp.virtual_server": cc.VirtualServer,
				"agent.id": cc.Agent.AgentID, "agent.run_id": cc.Agent.RunID,
				"agent.step": string(cc.Agent.Step), "error.type": class.ErrorClass,
				"mcp.protocol.version": cc.ProtocolVersion,
				"agentcc.org_id":       cc.Principal.OrgID, "agentcc.api_key_id": cc.Principal.KeyID,
				"mcp.error.code": class.ErrorCode, "mcp.error.origin": class.ErrorOrigin,
			} {
				if v != "" {
					attrs[k] = meter.Column(telemetry.SanitizeText(v, r), 256)
				}
			}
			if t.opts.IncludeBodies && cc.OrgConfig.TraceContentEnabled() {
				if body := telemetry.Content(cc.Args, mode, r); body != "" {
					attrs["gen_ai.tool.call.arguments"] = body
				}
				if body := telemetry.Content(cc.Result, mode, r); body != "" {
					attrs["gen_ai.tool.call.result"] = body
				}
			}
			trace.Finish(attrs, class.Outcome != meter.OutcomeSuccess, cc.Timings.UpstreamStarted, cc.Timings.UpstreamEnded)
		})
	}
}

// DecorateToolTrace runs at the existing upstream attempt boundary, shared
// by HTTP and stdio. It does not copy any inbound _meta into the request.
func DecorateToolTrace(cc *CallContext, msg *Message) {
	if cc.Method != MethodToolsCall || msg.Method != MethodToolsCall {
		return
	}
	trace, ok := cc.Context().Value(toolTraceKey{}).(*telemetry.Trace)
	if !ok {
		return
	}
	parent := trace.Upstream()
	if parent == "" {
		return
	}
	raw, _ := json.Marshal(parent)
	if params, err := wire.SetMeta(msg.Params, map[string]json.RawMessage{metaKeyTraceparent: raw}); err == nil {
		msg.Params = params
	}
}
