package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sort"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/protocol"
)

// writeJSONRPCResult writes a success response. On a 2026-07-28 request the
// result gets the gateway's envelope first (resultType, the gateway's
// serverInfo in _meta, and on lists, reads and discover ttlMs and
// cacheScope; protocol.Envelope).
func writeJSONRPCResult(w http.ResponseWriter, id json.RawMessage, result interface{}) {
	if pw := asProtocolWriter(w); pw != nil {
		decorated, err := pw.decorate(result)
		if err != nil {
			writeJSONRPCError(w, id, ErrCodeInternal, "failed to marshal result")
			return
		}
		result = decorated
	}
	resp, err := NewResponse(id, result)
	if err != nil {
		writeJSONRPCError(w, id, ErrCodeInternal, "failed to marshal result")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// writeJSONRPCError writes an error response. An unknown method answers HTTP
// 404 on a 2026-07-28 request and 200 on a legacy one, where a 404 would read
// as a terminated session (MC.3). The 404 is the 2026-07-28 transport's
// answer to an RPC method the server does not implement, which only
// writeMethodNotFound writes with -32601 here; a chain denial that reuses
// -32601 (an unknown tool, resource or prompt, until M10's -32602) is not
// one, and writeRPCDenial writes it with its own status.
func writeJSONRPCError(w http.ResponseWriter, id json.RawMessage, code int, message string) {
	resp := NewErrorResponse(id, code, message)
	w.Header().Set("Content-Type", "application/json")
	if pw := asProtocolWriter(w); pw != nil && pw.pr.era.Stateless && code == ErrCodeMethodNotFound {
		w.WriteHeader(http.StatusNotFound)
	}
	json.NewEncoder(w).Encode(resp)
}

// --- Chain answers ---

// writeToolCall answers a tools/call: its denial, or its result with the
// deprecation note today's tools carry and the call's response meta. The
// result is cc.Result, the one source every hook reads and rewrites (M13
// fills it; a scanner's rewrite is what the client gets), passed through raw
// so structuredContent, resource links, embedded resources, annotations,
// _meta and unknown fields survive (MCA C26, C30). The upstream's
// io.futureagi/* _meta keys are dropped (the namespace is the gateway's), and
// with a guard set the bytes about to be sent are what the guard passed
// (guardSentResult).
func (s *Server) writeToolCall(w http.ResponseWriter, cc *CallContext) {
	if d := cc.Decision; d != nil {
		writeToolCallDenial(w, cc, d)
		return
	}
	var typed any
	if cc.legacy.toolResult != nil {
		typed = cc.legacy.toolResult
	}
	result, err := upstreamResult(cc.Result, typed)
	if err != nil {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, "failed to marshal result")
		return
	}
	if len(result) == 0 {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, "tool call produced no result")
		return
	}
	if d := s.guardSentResult(cc, result); d != nil {
		cc.Decision = d // the call's record sees the block (its finalizer runs after this)
		writeToolCallDenial(w, cc, d)
		return
	}
	if note := s.deprecationNote(cc.Tool.ExposedName); note != "" {
		result = protocol.AppendTextContent(result, note)
	}

	slog.Info("mcp tool call",
		"tool", cc.Tool.ExposedName,
		"server", cc.Upstream.ServerID,
		"duration", cc.Timings.UpstreamEnded.Sub(cc.Timings.UpstreamStarted),
		"is_error", cc.IsError,
	)

	writeResultWithMeta(w, cc.msg.ID, result, cc.ResponseMeta)
}

// upstreamResult is the upstream's result as the client may see it: raw, or
// typed marshalled when a hook kept no bytes (typed nil: none), with every
// io.futureagi/* key of its _meta dropped (protocol.StripGatewayMeta). It is
// empty when there is neither.
func upstreamResult(raw json.RawMessage, typed any) (json.RawMessage, error) {
	if len(raw) == 0 && typed != nil {
		out, err := json.Marshal(typed)
		if err != nil {
			return nil, err
		}
		raw = out
	}
	if len(raw) == 0 {
		return nil, nil
	}
	return protocol.StripGatewayMeta(raw), nil
}

// guardSentResult runs the server's guard (SetGuard) over the bytes the
// client is about to receive: every string in them, member names and every
// duplicate included, each as one text part (protocol.Strings). The default
// scanner (hook_scan_args.go, M22's) checks only the typed decode's text
// parts, so without this the client could get text the guard never saw: in
// structuredContent, an embedded resource, a resource link, or a key whose
// case-variant duplicate the typed decode kept instead. It repeats the
// guard's pure post-check and is redundant once the scanner reads cc.Result
// itself (integrator request IR-5). Bytes that are not JSON are refused.
//
// Billing: a block here is not a step-18 block. It runs in the writer, after
// the whole chain, so step 19 (settleCredits, settleBudget) has already
// settled the call as a success, and the call is charged. A step-18 denial
// (scanResult) skips settlement, which is not an always step, and the
// reserve finalizer voids the hold. The settle hooks are no-ops at this PIN,
// so nobody is charged today; IR-5 must land before M18 and M19 fill
// settlement, or settlement must not run on a result this guard refuses.
func (s *Server) guardSentResult(cc *CallContext, result json.RawMessage) *Denial {
	if s.guard == nil {
		return nil
	}
	strs, err := protocol.Strings(result)
	reason := "the result is not JSON"
	if err == nil {
		view := &ToolCallResult{Content: make([]ContentPart, len(strs)), IsError: cc.IsError, Raw: result}
		for i, str := range strs {
			view.Content[i] = ContentPart{Type: "text", Text: str}
		}
		if reason = s.guard.CheckPost(cc.Tool.ExposedName, cc.Upstream.ServerID, view); reason == "" {
			return nil
		}
	}
	slog.Warn("mcp tool call blocked (post, sent result)",
		"tool", cc.Tool.ExposedName,
		"server", cc.Upstream.ServerID,
		"reason", reason,
	)
	return rpcDenial(ErrCodeInvalidRequest, "tool_result_blocked", "guardrail: "+reason)
}

// deprecationNote is the text a deprecated tool's result carries, or "".
func (s *Server) deprecationNote(tool string) string {
	vi, ok := s.registry.GetToolVersion(tool)
	if !ok || !vi.Deprecated {
		return ""
	}
	warning := fmt.Sprintf("DEPRECATED: tool %q is deprecated", tool)
	if vi.DeprecationMessage != "" {
		warning = fmt.Sprintf("DEPRECATED: %s", vi.DeprecationMessage)
	}
	if vi.ReplacedBy != "" {
		warning += fmt.Sprintf(". Use %q instead", vi.ReplacedBy)
	}
	return warning
}

// writeToolCallDenial answers a refused tools/call: in band as an isError
// result (MC.3), or as the protocol-level error the denial names.
func writeToolCallDenial(w http.ResponseWriter, cc *CallContext, d *Denial) {
	if !d.InBand() {
		writeRPCDenial(w, cc.msg.ID, d)
		return
	}
	res, err := inBandDenialResult(d, cc.ResponseMeta)
	if err != nil {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, "failed to marshal denial")
		return
	}
	writeJSONRPCResult(w, cc.msg.ID, res)
}

// writeDenialRPC answers a refused list or read: always a JSON-RPC error. A
// denial without a JSON-RPC code is -32600, with its error object as data.
func writeDenialRPC(w http.ResponseWriter, cc *CallContext) {
	d := *cc.Decision
	if d.RPCCode == 0 {
		obj, err := json.Marshal(d.Object())
		if err != nil {
			writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, "failed to marshal denial")
			return
		}
		d.RPCCode, d.Message, d.Data = ErrCodeInvalidRequest, d.Error(), obj
	}
	writeRPCDenial(w, cc.msg.ID, &d)
}

// writeRPCDenial writes a protocol-level denial: its JSON-RPC error, with the
// status, headers and data it sets (none: today's plain error, HTTP 200). It
// never takes writeJSONRPCError's unknown-method 404: the method was served,
// and the chain refused the call.
func writeRPCDenial(w http.ResponseWriter, id json.RawMessage, d *Denial) {
	for k, vs := range d.Header {
		for _, v := range vs {
			w.Header().Add(k, v)
		}
	}
	resp := NewErrorResponse(id, d.RPCCode, d.Message)
	resp.Error.Data = d.Data
	w.Header().Set("Content-Type", "application/json")
	if d.HTTPStatus != 0 {
		w.WriteHeader(d.HTTPStatus)
	}
	json.NewEncoder(w).Encode(resp)
}

// writeResultWithMeta writes result with meta merged into its _meta object.
// With no meta it is exactly writeJSONRPCResult.
func writeResultWithMeta(w http.ResponseWriter, id json.RawMessage, result any, meta map[string]json.RawMessage) {
	if len(meta) == 0 {
		writeJSONRPCResult(w, id, result)
		return
	}
	merged, err := mergeMeta(result, meta)
	if err != nil {
		writeJSONRPCError(w, id, ErrCodeInternal, "failed to marshal result")
		return
	}
	writeJSONRPCResult(w, id, merged)
}

// mergeMeta returns result as a JSON object whose _meta holds its own entries
// and meta's; meta wins on a shared key.
func mergeMeta(result any, meta map[string]json.RawMessage) (json.RawMessage, error) {
	raw, err := toJSON(result)
	if err != nil {
		return nil, err
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(raw, &obj); err != nil {
		return nil, err
	}
	if obj == nil {
		obj = map[string]json.RawMessage{}
	}
	existing := map[string]json.RawMessage{}
	if m, ok := obj["_meta"]; ok {
		if err := json.Unmarshal(m, &existing); err != nil || existing == nil {
			existing = map[string]json.RawMessage{}
		}
	}
	for k, v := range meta {
		existing[k] = v
	}
	m, err := json.Marshal(existing)
	if err != nil {
		return nil, err
	}
	obj["_meta"] = m
	return json.Marshal(obj)
}

// toJSON returns v as raw JSON: a json.RawMessage as it is, anything else
// marshalled.
func toJSON(v any) (json.RawMessage, error) {
	if raw, ok := v.(json.RawMessage); ok {
		return raw, nil
	}
	return json.Marshal(v)
}

// --- Lists: raw definitions, sealed cursors, cache scope ---

func (s *Server) writeToolsList(w http.ResponseWriter, cc *CallContext) {
	page, next, ok := listPage(s, w, cc, cc.ToolList, func(t Tool) string { return t.Name })
	if !ok {
		return
	}
	items := make([]json.RawMessage, len(page))
	for i, t := range page {
		items[i] = definition(t.Raw, t, t.Name, t.GatewayMeta)
	}
	writeResultWithMeta(w, cc.msg.ID, listResult("tools", items, next), cc.ResponseMeta)
}

func (s *Server) writeResourcesList(w http.ResponseWriter, cc *CallContext) {
	page, next, ok := listPage(s, w, cc, cc.ResourceList, func(r Resource) string { return r.URI })
	if !ok {
		return
	}
	items := make([]json.RawMessage, len(page))
	for i, r := range page {
		items[i] = definition(r.Raw, r, "", r.GatewayMeta)
	}
	writeResultWithMeta(w, cc.msg.ID, listResult("resources", items, next), cc.ResponseMeta)
}

func (s *Server) writePromptsList(w http.ResponseWriter, cc *CallContext) {
	page, next, ok := listPage(s, w, cc, cc.PromptList, func(p Prompt) string { return p.Name })
	if !ok {
		return
	}
	items := make([]json.RawMessage, len(page))
	for i, p := range page {
		items[i] = definition(p.Raw, p, p.Name, p.GatewayMeta)
	}
	writeResultWithMeta(w, cc.msg.ID, listResult("prompts", items, next), cc.ResponseMeta)
}

// definition is one list item: the upstream's raw definition, or the typed
// value when no raw bytes were kept, with only the gateway-owned fields
// edited (protocol.EditDefinition): the exposed name, and _meta's
// io.futureagi/* keys, which are the gateway's own (gateway) and never the
// upstream's.
func definition(raw json.RawMessage, typed any, name string, gateway map[string]json.RawMessage) json.RawMessage {
	if len(raw) == 0 {
		out, err := json.Marshal(typed)
		if err != nil {
			return json.RawMessage("null")
		}
		raw = out
	}
	return protocol.EditDefinition(raw, name, gateway)
}

// listResult is a list answer: the items under key, and nextCursor when the
// merged list goes on.
func listResult(key string, items []json.RawMessage, next string) map[string]any {
	res := map[string]any{key: items}
	if next != "" {
		res["nextCursor"] = next
	}
	return res
}

// listPage returns the page of a merged list a list request asks for, and
// the sealed cursor of the next page (MCA C28, I8). A list longer than one
// page is ordered by key, so its pages hold together; a single page keeps the
// order the list hooks gave it. A cursor that does not open (altered,
// expired, or sealed for another caller or method) is -32602; ok is false
// once that answer is written. It also fixes the answer's cache scope.
func listPage[T any](s *Server, w http.ResponseWriter, cc *CallContext, items []T, key func(T) string) (page []T, next string, ok bool) {
	setCacheScope(w, s.cacheScope(cc))
	set := s.protocolSettings()
	binding := cursorBinding(cc)
	offset := 0
	if token := requestCursor(cc.msg); token != "" {
		if set.Cursors == nil {
			writeJSONRPCError(w, cc.msg.ID, ErrCodeInvalidParams, "invalid cursor")
			return nil, "", false
		}
		c, err := protocol.OpenCursor(set.Cursors, token, cc.Method, binding)
		if err != nil {
			writeJSONRPCError(w, cc.msg.ID, ErrCodeInvalidParams, "invalid cursor")
			return nil, "", false
		}
		offset = c.Offset
	}
	size := set.PageSize
	if set.Cursors == nil {
		size = 0 // no codec, no cursors: one page
	}
	if offset > 0 || (size > 0 && len(items) > size) {
		items = append([]T(nil), items...)
		sort.SliceStable(items, func(i, j int) bool { return key(items[i]) < key(items[j]) })
	}
	offset = min(offset, len(items))
	end, more := protocol.Page(len(items), offset, size)
	if more {
		token, err := protocol.SealCursor(set.Cursors, protocol.Cursor{Method: cc.Method, Offset: end, Binding: binding})
		if err != nil {
			writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, "failed to seal cursor")
			return nil, "", false
		}
		next = token
	}
	return items[offset:end], next, true
}

// requestCursor is a list request's params.cursor, or "".
func requestCursor(msg *Message) string {
	var p struct {
		Cursor string `json:"cursor"`
	}
	if msg == nil || len(msg.Params) == 0 || json.Unmarshal(msg.Params, &p) != nil {
		return ""
	}
	return p.Cursor
}

// cursorBinding names who may continue a listing: the principal and the
// virtual server, hashed.
func cursorBinding(cc *CallContext) string {
	p := cc.Principal
	return protocol.Binding(string(p.Kind), p.KeyID, p.OrgID, p.UserID, p.ClientID, cc.VirtualServer)
}

// cacheScope is a list or read answer's cache scope: public only on a gateway
// without per-key auth, for a request with no principal, tenant, virtual
// server or key tool list, where nothing can vary by caller; private
// whenever an access rule may filter (MCA C17).
func (s *Server) cacheScope(cc *CallContext) string {
	p := cc.Principal
	ctx := cc.Context()
	filtered := s.keyAuth != nil || p.Kind != "" || p.KeyID != "" || p.OrgID != "" || p.UserID != "" ||
		cc.OrgConfig != nil || cc.VirtualServer != "" ||
		ctx.Value(ctxKeyAllowedTools) != nil || ctx.Value(ctxKeyDeniedTools) != nil
	if filtered {
		return protocol.CacheScopePrivate
	}
	return protocol.CacheScopePublic
}

// --- Reads ---

// writeReadResource and writeGetPrompt pass the upstream's result through raw
// (cc.Result), as tools/call does; the typed result answers only for a hook
// that kept no bytes.
func (s *Server) writeReadResource(w http.ResponseWriter, cc *CallContext) {
	setCacheScope(w, s.cacheScope(cc))
	var typed any
	if cc.legacy.readResult != nil {
		typed = cc.legacy.readResult
	}
	writeRawResult(w, cc, typed)
}

func (s *Server) writeGetPrompt(w http.ResponseWriter, cc *CallContext) {
	var typed any
	if cc.legacy.promptResult != nil {
		typed = cc.legacy.promptResult
	}
	writeRawResult(w, cc, typed)
}

// writeRawResult passes an upstream result through (upstreamResult), with
// the call's response meta merged into its _meta.
func writeRawResult(w http.ResponseWriter, cc *CallContext, typed any) {
	result, err := upstreamResult(cc.Result, typed)
	if err != nil {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, "failed to marshal result")
		return
	}
	if len(result) == 0 {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, cc.Method+" produced no result")
		return
	}
	writeResultWithMeta(w, cc.msg.ID, result, cc.ResponseMeta)
}

// --- The response writer of one POST ---

// GatewayServerName is the serverInfo name the gateway answers 2026-07-28
// results and server/discover with.
const GatewayServerName = "Future AGI gateway"

// streamableMethods are the methods whose answer may arrive as an SSE stream:
// the ones that call an upstream and can run long or report progress.
var streamableMethods = map[string]bool{
	MethodToolsCall:     true,
	MethodResourcesRead: true,
	MethodPromptsGet:    true,
}

// protocolWriter is the http.ResponseWriter the method handlers of one POST
// write through (HandlePost). It applies the request's era to what they
// write (the 2026 envelope, the unknown-method status), and when the client
// accepts SSE it turns a call that runs past StreamAfter, or that has a
// notification to relay, into a per-request SSE stream: M1x's writer with
// keep-alives, the call's own answer as the last event, and a client that
// goes away cancels the call.
type protocolWriter struct {
	w          http.ResponseWriter
	r          *http.Request
	pr         *postRequest
	serverInfo json.RawMessage
	cancel     context.CancelFunc

	// hdr is the header the handlers set; it reaches w when they write, so
	// the stream switch never races a handler on w's header map.
	hdr http.Header
	// cacheScope is the answer's cache scope, set by the list and read
	// writers, which see the call (setCacheScope).
	cacheScope string

	mu          sync.Mutex
	wroteHeader bool
	mayStream   bool
	sse         *SSEWriter // non-nil once the response is a stream
	pending     []byte     // the handler's answer, written after streaming began
	timer       *time.Timer
}

func newProtocolWriter(w http.ResponseWriter, r *http.Request, pr *postRequest, info Implementation, cancel context.CancelFunc) *protocolWriter {
	pw := &protocolWriter{w: w, r: r, pr: pr, cancel: cancel, hdr: http.Header{}}
	pw.serverInfo, _ = json.Marshal(Implementation{Name: GatewayServerName, Version: info.Version})
	pw.mayStream = protocol.AcceptsSSE(r.Header) && streamableMethods[pr.method]
	pr.writer = pw
	if pw.mayStream {
		pw.timer = time.AfterFunc(pr.settings.StreamAfter, pw.startStream)
	}
	return pw
}

// asProtocolWriter returns w as the POST's protocol writer, or nil.
func asProtocolWriter(w http.ResponseWriter) *protocolWriter {
	pw, _ := w.(*protocolWriter)
	return pw
}

// setCacheScope fixes the cache scope of the answer w is about to write.
func setCacheScope(w http.ResponseWriter, scope string) {
	if pw := asProtocolWriter(w); pw != nil {
		pw.cacheScope = scope
	}
}

// decorate applies the 2026-07-28 envelope to a result. A legacy result is
// returned unchanged, except that one passed through raw from an upstream
// loses a 2026-07-28 upstream's envelope (protocol.StripEnvelope, at the W3b
// merge): the gateway's own legacy results carry none.
func (pw *protocolWriter) decorate(result any) (any, error) {
	if !pw.pr.era.Stateless {
		if raw, ok := result.(json.RawMessage); ok {
			return protocol.StripEnvelope(raw), nil
		}
		return result, nil
	}
	raw, err := toJSON(result)
	if err != nil {
		return nil, err
	}
	env := protocol.Envelope{
		ServerInfo:  pw.serverInfo,
		Cacheable:   protocol.Cacheable(pw.pr.method),
		TTL:         pw.pr.settings.ListTTL,
		UpstreamTTL: pw.pr.method == MethodResourcesRead,
		CacheScope:  pw.cacheScope,
	}
	return env.Decorate(raw), nil
}

func (pw *protocolWriter) Header() http.Header { return pw.hdr }

func (pw *protocolWriter) WriteHeader(code int) {
	pw.mu.Lock()
	defer pw.mu.Unlock()
	if pw.sse != nil || pw.wroteHeader {
		return
	}
	pw.writeHeaderLocked(code)
}

func (pw *protocolWriter) Write(b []byte) (int, error) {
	pw.mu.Lock()
	defer pw.mu.Unlock()
	if pw.sse != nil {
		pw.pending = append(pw.pending, b...)
		return len(b), nil
	}
	if !pw.wroteHeader {
		pw.writeHeaderLocked(http.StatusOK)
	}
	return pw.w.Write(b)
}

// Flush sends what was written so far (http.Flusher), so a handler can
// stream through the writer itself.
func (pw *protocolWriter) Flush() {
	pw.mu.Lock()
	defer pw.mu.Unlock()
	if pw.sse != nil {
		return
	}
	if !pw.wroteHeader {
		pw.writeHeaderLocked(http.StatusOK)
	}
	_ = http.NewResponseController(pw.w).Flush()
}

// Unwrap gives http.ResponseController the underlying writer.
func (pw *protocolWriter) Unwrap() http.ResponseWriter { return pw.w }

// writeHeaderLocked sends the handlers' header and the status: from here on
// the answer is plain JSON. The caller holds mu.
func (pw *protocolWriter) writeHeaderLocked(code int) {
	pw.stopTimerLocked()
	pw.mayStream = false
	dst := pw.w.Header()
	for k, vs := range pw.hdr {
		dst[k] = vs
	}
	pw.w.WriteHeader(code)
	pw.wroteHeader = true
}

func (pw *protocolWriter) stopTimerLocked() {
	if pw.timer != nil {
		pw.timer.Stop()
	}
}

// startStream turns the answer into an SSE stream, unless something was
// written already or the call cannot stream.
func (pw *protocolWriter) startStream() {
	pw.mu.Lock()
	defer pw.mu.Unlock()
	pw.startStreamLocked()
}

// startStreamLocked reports whether the answer is now a stream. The caller
// holds mu.
func (pw *protocolWriter) startStreamLocked() bool {
	if pw.sse != nil {
		return true
	}
	if pw.wroteHeader || !pw.mayStream {
		return false
	}
	sse, err := NewSSEWriter(pw.w, pw.r, pw.pr.settings.KeepAlive)
	if err != nil {
		pw.mayStream = false
		return false
	}
	pw.sse, pw.wroteHeader = sse, true
	// Closing the stream cancels the call (2026-07-28 transport: a closed
	// response stream is a cancellation).
	go func() {
		<-sse.Done()
		pw.cancel()
	}()
	return true
}

// relay sends one notification on the call's stream, starting the stream
// first. It reports whether the notification went out.
func (pw *protocolWriter) relay(n json.RawMessage) bool {
	pw.mu.Lock()
	if !pw.startStreamLocked() {
		pw.mu.Unlock()
		return false
	}
	sse := pw.sse
	pw.mu.Unlock()
	return sse.Event("", "", n) == nil
}

// finish ends the answer once the handler returned: a stream gets the
// handler's answer as its last event and is closed; an answer nothing was
// written for gets the handlers' header with 200, as net/http would.
func (pw *protocolWriter) finish() {
	pw.mu.Lock()
	pw.stopTimerLocked()
	pw.mayStream = false
	sse, pending := pw.sse, pw.pending
	if sse == nil && !pw.wroteHeader {
		pw.writeHeaderLocked(http.StatusOK)
	}
	pw.mu.Unlock()
	if sse == nil {
		return
	}
	if len(pending) > 0 {
		_ = sse.Event("", "", bytes.TrimRight(pending, "\n"))
	}
	sse.Close()
}

// relayNotification sends an upstream notification that belongs to one call
// (progress, a log message) to that call's client, on the call's response
// stream, when the client accepts SSE. ctx is the call's context
// (CallContext.Context). A log message on a 2026-07-28 request goes out only
// when the request's _meta named a logLevel, and only at or above it; on a
// legacy request log messages follow logging/setLevel, which the gateway does
// not serve, so they stay dropped. It reports whether the notification went
// out. The upstream client (M13) calls it.
func relayNotification(ctx context.Context, n *Message) bool {
	pr := postRequestOf(ctx)
	if pr == nil || pr.writer == nil || n == nil {
		return false
	}
	raw, err := json.Marshal(n)
	if err != nil {
		return false
	}
	if n.Method == MethodLogMessage && (!pr.era.Stateless || !protocol.RelayLog(pr.era.Meta.LogLevel, raw)) {
		return false
	}
	return pr.writer.relay(raw)
}
