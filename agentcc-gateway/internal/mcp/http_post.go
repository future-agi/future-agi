package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
	"weak"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentbounds"
	"github.com/futureagi/agentcc-gateway/internal/mcp/contentpolicy"
	"github.com/futureagi/agentcc-gateway/internal/mcp/protocol"
)

// HandlePost handles POST on the MCP endpoint, the one JSON-RPC entry of
// both protocol eras. In order: the auth chokepoint, the
// HTTP rules (Origin 403, Content-Type 415, body cap 413), the content
// bounds (a refused body is -32700, with HTTP 400 on a 2026-07-28 request,
// and -32020 there for a repeated member a header mirrors: refusedBody), the
// JSON-RPC envelope, the request's era and its rules (-32021, -32022,
// -32020; all before routing, policy and metering), Accept (406), then the
// answer: 202 for a notification or a client's response, else the method's
// handler behind a writer that applies the era's response rules
// (respond.go).
func (s *Server) HandlePost(w http.ResponseWriter, r *http.Request) {
	req, ok := s.authenticateHTTP(w, admissionRoute(r))
	if !ok {
		return
	}
	defer releaseAdmissionIngress(req)
	set := s.protocolSettings()
	if !protocol.OriginOK(req.Header.Get("Origin"), req.Host, set.AllowedOrigins, set.OwnHosts) {
		writeHTTPRefusal(w, http.StatusForbidden, "origin_not_allowed", "Origin is not allowed for this endpoint")
		return
	}
	if !protocol.ContentTypeOK(req.Header) {
		writeHTTPRefusal(w, http.StatusUnsupportedMediaType, "unsupported_media_type", "Content-Type must be "+protocol.MediaJSON)
		return
	}
	clearDeadline, err := s.admissionBodyDeadline(w, req)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	maxBody := set.MaxBodyBytes
	if s.hostedAdmissionRequired() {
		maxBody = min(maxBody, 65536)
	}
	// A 2026-07-28 request names its revision in a header, so its era is
	// known before its body is read. What the bounds read of its body is kept
	// for the answer to a refused one (refusedBody).
	stateless := protocol.IsStateless(req.Header.Get(protocol.HeaderProtocolVersion))
	var read bytes.Buffer
	src := io.Reader(req.Body)
	if stateless && req.Body != nil {
		src = io.TeeReader(req.Body, &read)
	}
	body, err := contentpolicy.ReadObject(src, maxBody)
	clearDeadline()
	if err != nil {
		var bound *contentbounds.Error
		if errors.As(err, &bound) && bound.Code() == contentbounds.CodeReadBytes {
			writeHTTPRefusal(w, http.StatusRequestEntityTooLarge, "request_too_large",
				"request body exceeds "+strconv.FormatInt(contentpolicy.ByteLimit(maxBody), 10)+" bytes")
			return
		}
		if stateless {
			duplicate := bound != nil && bound.Code() == contentbounds.CodeDuplicateKey
			writeProtocolError(w, json.RawMessage("null"), refusedBody(req.Header, read.Bytes(), duplicate))
			return
		}
		writeJSONRPCError(w, nil, ErrCodeParse, "invalid or unbounded JSON request")
		return
	}

	// Parse the JSON-RPC message.
	var msg Message
	if err := json.Unmarshal(body, &msg); err != nil {
		writeJSONRPCError(w, nil, ErrCodeParse, "invalid JSON: "+err.Error())
		return
	}
	if msg.JSONRPC != "2.0" {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, "expected jsonrpc 2.0")
		return
	}

	if s.hostedAdmissionRequired() {
		s.handleAdmittedPost(w, req, &msg)
		return
	}
	era := protocol.Classify(msg.Method, msg.Params, req.Header)
	if perr := s.checkEra(req, &msg, era, set.Versions); perr != nil {
		writeProtocolError(w, msg.ID, perr)
		return
	}
	if !protocol.AcceptOK(req.Header, era.Stateless) {
		writeHTTPRefusal(w, http.StatusNotAcceptable, "not_acceptable",
			"Accept must list "+protocol.MediaJSON+" and "+protocol.MediaSSE)
		return
	}

	switch {
	case msg.Method == "" && msg.ID != nil:
		// A client's response to a server request: accepted (bug 20).
		w.WriteHeader(http.StatusAccepted)
		return
	case msg.Method == "":
		writeHTTPRefusal(w, http.StatusBadRequest, "invalid_request", "not a JSON-RPC request, notification or response")
		return
	case msg.IsNotification():
		acceptNotification(w, &msg)
		return
	}

	pr := &postRequest{era: era, method: msg.Method, settings: set}
	ctx, cancel := context.WithCancel(context.WithValue(req.Context(), postRequestKey{}, pr))
	defer cancel()
	req = servedRequest(req.Clone(ctx), pr)
	pw := newProtocolWriter(w, req, pr, s.serverInfo, cancel)
	defer pw.finish()
	s.dispatch(pw, req, &msg)
}

// checkEra applies the rules of the request's era: the versions served, then
// on a stateless request its mirrored headers against the body.
func (s *Server) checkEra(r *http.Request, msg *Message, era protocol.Era, served []string) *protocol.Error {
	if perr := era.Validate(served); perr != nil {
		return perr
	}
	if !era.Stateless {
		return nil
	}
	return protocol.CheckHeaders(r.Header, msg.Method, msg.Params, era.Meta, func(name string) (json.RawMessage, bool) {
		return s.toolInputSchema(r, msg, name)
	})
}

// toolInputSchema is the inputSchema of the tool a stateless tools/call
// names, which marks the arguments mirrored as Mcp-Param-* headers: the tool
// authorizeTool resolves for that name (CheckHeaders reads the name as the
// chain does), so the headers are checked against the tool that will run.
//
// It is resolved through the caller's own view: callToolOf runs the chain's own steps up to and
// including authorizeTool for the caller of r. A tool that view hides or
// refuses returns false, and the call goes on to the chain, which answers it
// as it answers an unknown tool (or with its own refusal), so the header check
// tells a caller nothing the chain does not. A virtual server's exposed names
// (<server>__<tool>) are found the way the chain finds them, so their mirrored
// arguments are checked too. r is the request, msg its message.
func (s *Server) toolInputSchema(r *http.Request, msg *Message, name string) (json.RawMessage, bool) {
	rt := s.callToolOf(r, msg, name)
	if rt == nil {
		return nil, false
	}
	return rt.Tool.InputSchema, true
}

// callToolOf resolves the tool a tools/call named name resolves to for the
// caller of r, exactly as the chain will: the steps of toolsCallChain up to and
// including authorizeTool run on a throwaway call context, without the span
// (step 0), the record (step 2a) or any hook observer, so the lookup is neither
// traced, metered nor seen as a call. It returns nil when a step refuses the
// call (an unknown, hidden, quarantined or denied tool, or a refused caller).
func (s *Server) callToolOf(r *http.Request, msg *Message, name string) *RegisteredTool {
	if r == nil || msg == nil {
		return nil
	}
	cc := s.newCallContext(r, msg, nil)
	cc.observe = nil
	defer cc.runFinalizers()
	cc.Tool.ExposedName = name
	cc.Args = rawArguments(msg.Params)
	var params ToolCallParams
	if json.Unmarshal(msg.Params, &params) == nil {
		cc.legacy.arguments = params.Arguments
	}
	for _, st := range toolsCallChain {
		switch st.name {
		case hookStartSpan, hookRegisterRecord:
			continue
		}
		if d := st.run(s, cc); d != nil {
			return nil
		}
		if st.name == hookAuthorizeTool {
			return cc.legacy.tool
		}
	}
	return nil
}

// acceptNotification answers a notification (a message without an id): 202
// with no body for every notifications/* method, which the gateway accepts
// and ignores unless its handler acts on it (bug 20). A request method sent
// without an id is refused, since nothing could carry its answer.
func acceptNotification(w http.ResponseWriter, msg *Message) {
	if !strings.HasPrefix(msg.Method, "notifications/") {
		writeHTTPRefusal(w, http.StatusBadRequest, "invalid_request", "request "+msg.Method+" needs an id")
		return
	}
	w.WriteHeader(http.StatusAccepted)
}

// --- The request's protocol state ---

// postRequest is what HandlePost decided about one request. It rides in the
// request's context, so the method handlers, the response writer and later
// lanes read one answer: requestEra for the era, statelessSession for the
// session of a stateless request.
type postRequest struct {
	era      protocol.Era
	method   string
	settings protocol.Settings
	// session is a stateless request's own session: never stored, never
	// named by an id.
	session *Session
	// writer is the request's response writer (respond.go), which relays the
	// call's notifications.
	writer *protocolWriter
}

type postRequestKey struct{}

func postRequestOf(ctx context.Context) *postRequest {
	pr, _ := ctx.Value(postRequestKey{}).(*postRequest)
	return pr
}

// requestEra is the protocol revision HandlePost serves r under: 2026-07-28
// for a stateless request, else the legacy request's MCP-Protocol-Version,
// else 2025-03-26 (the transport spec's assumption without the header). It
// is "" for a request HandlePost did not serve. CallContext.Era takes its
// value from here (chain.go).
func requestEra(r *http.Request) string {
	if r == nil {
		return ""
	}
	if pr := postRequestOf(r.Context()); pr != nil {
		return pr.era.Served()
	}
	return ""
}

// statelessSession returns the session a stateless request runs under: a
// request-scoped one that no store holds and no id names, so the chain sees
// no session id and no other request can reach it. ok is false for a legacy
// request, whose session validateSession looks up. validateSession's first
// statement calls it (method_initialize.go).
func (s *Server) statelessSession(r *http.Request) (*Session, bool) {
	pr := postRequestOf(r.Context())
	if pr == nil || !pr.era.Stateless || pr.session == nil {
		return nil, false
	}
	return pr.session, true
}

// servedRequest returns the request the method handlers see: r (a clone
// carrying pr in its context) and, on a stateless request, without
// Mcp-Session-Id and Last-Event-ID, which 2026-07-28 ignores, and with the
// request's own session built from its _meta.
func servedRequest(r *http.Request, pr *postRequest) *http.Request {
	if !pr.era.Stateless {
		return r
	}
	r.Header.Del(protocol.HeaderSessionID)
	r.Header.Del(protocol.HeaderLastEventID)
	pr.session = newStatelessSession(pr.era.Meta)
	return r
}

// newStatelessSession builds a stateless request's session from its _meta.
func newStatelessSession(m protocol.Meta) *Session {
	sess := &Session{CreatedAt: time.Now()}
	_ = json.Unmarshal(m.ClientInfo, &sess.ClientInfo)
	_ = json.Unmarshal(m.ClientCapabilities, &sess.Capabilities)
	sess.Touch()
	return sess
}

// --- Protocol settings ---

// protocolSettingsByServer holds each server's protocol settings
// (SetProtocol). The Server struct is frozen (server.go), so they
// live beside it, keyed by a weak pointer whose entry a cleanup removes once
// the server is unreachable.
var protocolSettingsByServer sync.Map // weak.Pointer[Server] → *protocol.Settings

// SetProtocol installs the server's protocol settings: the revisions served,
// the body cap, the allowed origins and the list-cursor codec, from
// mcp.protocol, mcp.max_body_bytes, mcp.allowed_origins and the MCP state
// keys (wiring_mcp_protocol.go). Unset fields keep their defaults.
func (s *Server) SetProtocol(set protocol.Settings) {
	set = set.WithDefaults()
	if set.Cursors == nil {
		set.Cursors = devCursorCodec()
	}
	key := weak.Make(s)
	if _, loaded := protocolSettingsByServer.Swap(key, &set); !loaded {
		runtime.AddCleanup(s, func(k weak.Pointer[Server]) { protocolSettingsByServer.Delete(k) }, key)
	}
}

// protocolSettings returns the server's protocol settings, the defaults when
// SetProtocol was not called (a server built outside the gateway's wiring).
func (s *Server) protocolSettings() protocol.Settings {
	if v, ok := protocolSettingsByServer.Load(weak.Make(s)); ok {
		return *v.(*protocol.Settings)
	}
	set := protocol.Settings{}.WithDefaults()
	set.Cursors = devCursorCodec()
	return set
}

// devCursorCodec seals cursors for a server the wiring did not configure,
// under the process's ephemeral key (the dev_insecure_state codec). A nil
// codec, when even that cannot be built, turns pagination off.
var devCursorCodec = sync.OnceValue(func() protocol.Sealer {
	c, err := NewStateCodec(nil, true)
	if err != nil {
		return nil
	}
	return c
})

// --- Refusals before dispatch ---

// writeHTTPRefusal answers a request refused by an HTTP rule: the status,
// with a JSON-RPC error whose data names the refusal (the protocol-level
// answers). The request was not dispatched, so the id is null.
func writeHTTPRefusal(w http.ResponseWriter, status int, code, message string) {
	data, _ := json.Marshal(map[string]string{"code": code})
	resp := NewErrorResponse(json.RawMessage("null"), ErrCodeInvalidRequest, message)
	resp.Error.Data = data
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(resp)
}

// writeProtocolError answers a request refused by its era's rules.
func writeProtocolError(w http.ResponseWriter, id json.RawMessage, perr *protocol.Error) {
	resp := NewErrorResponse(id, perr.Code, perr.Message)
	resp.Error.Data = perr.Data
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(perr.HTTPStatus)
	json.NewEncoder(w).Encode(resp)
}

// refusedBody is the answer to a 2026-07-28 request whose body the content
// bounds refused, read being what they read of it: HTTP 400, as the
// transport's Server Validation requires, with -32020 (HeaderMismatch) when
// the refusal is a member sent twice that a header mirrors
// (protocol.RepeatsMirrored), else with -32700. duplicate is true when the
// bounds refused a repeated member name. The body was never read whole, so
// the method is the one its Mcp-Method header names. A legacy request's
// refused body is still answered with HTTP 200 and -32700.
func refusedBody(h http.Header, read []byte, duplicate bool) *protocol.Error {
	method, _ := protocol.DecodeHeaderValue(h.Get(protocol.HeaderMethod))
	if duplicate && protocol.RepeatsMirrored(read, method) {
		return protocol.HeaderMismatch("the request body carries a member a header mirrors more than once; send it once")
	}
	return &protocol.Error{HTTPStatus: http.StatusBadRequest, Code: ErrCodeParse, Message: "invalid or unbounded JSON request"}
}

// refuseStatelessStream answers GET or DELETE on the MCP endpoint with 405
// when the request names no session: only a legacy session has a GET stream
// or an end (2026-07-28 is POST only). It reports whether it answered. the session manager's
// HandleSSE and HandleDelete call it right after authenticateHTTP.
func refuseStatelessStream(w http.ResponseWriter, r *http.Request) bool {
	if r.Header.Get(protocol.HeaderSessionID) != "" {
		return false
	}
	w.Header().Set("Allow", http.MethodPost)
	writeHTTPRefusal(w, http.StatusMethodNotAllowed, "method_not_allowed",
		r.Method+" needs an "+protocol.HeaderSessionID+"; a "+protocol.Version2026+" client uses POST only")
	return true
}
