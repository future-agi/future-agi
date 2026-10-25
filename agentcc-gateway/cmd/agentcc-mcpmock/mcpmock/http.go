package mcpmock

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"time"
)

// maxBodyBytes bounds one POSTed message.
const maxBodyBytes = 16 << 20

// sseKeepAlive is how often an idle stream sends a ":" comment.
const sseKeepAlive = 15 * time.Second

// serveStreamable is the Streamable HTTP endpoint (--path): POST for every
// era it serves, GET and DELETE for legacy sessions.
func (m *Mock) serveStreamable(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodPost:
		m.servePost(w, r)
	case http.MethodGet:
		m.serveGet(w, r)
	case http.MethodDelete:
		m.serveDelete(w, r)
	default:
		w.Header().Set("Allow", "POST, GET, DELETE")
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// httpReceived starts the /__received entry for an HTTP request.
func (m *Mock) httpReceived(r *http.Request, transport string, msg *Message) Received {
	headers, auth, creds := m.headers.record(r.Header)
	rec := Received{Transport: transport, HTTPMethod: r.Method, Path: r.URL.Path,
		Headers: headers, Auth: auth, Credentials: creds}
	if msg != nil {
		rec.Method, rec.ID, rec.Meta = msg.Method, msg.ID, metaOf(msg.Params)
		rec.Name = requestName(msg.Method, msg.Params)
	}
	return rec
}

func (m *Mock) servePost(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes+1))
	if err != nil || len(body) > maxBodyBytes {
		m.state.record(Received{Transport: "http", HTTPMethod: r.Method, Path: r.URL.Path, Rejected: "body"})
		writeJSON(w, http.StatusRequestEntityTooLarge, response(nil, nil, rpcErr(0, CodeInvalidRequest, "body over 16 MiB or unreadable")))
		return
	}
	var msg Message
	if trimmed := bytes.TrimSpace(body); len(trimmed) > 0 && trimmed[0] == '[' {
		m.reject(w, r, nil, "batch", http.StatusBadRequest, rpcErr(0, CodeInvalidRequest, "JSON-RPC batches are not supported"))
		return
	}
	if err := json.Unmarshal(body, &msg); err != nil || msg.JSONRPC != "2.0" {
		m.reject(w, r, nil, "parse", http.StatusBadRequest, rpcErr(0, CodeParse, "want one JSON-RPC 2.0 message"))
		return
	}
	rec := m.httpReceived(r, "http", &msg)
	principal, ok := m.authorize(w, r)
	if !ok {
		rec.Rejected = "unauthorized"
		m.state.record(rec)
		return
	}
	rec.Principal = principal

	if sid := r.Header.Get("Mcp-Session-Id"); sid != "" && m.cfg.Era.legacyStreamable() {
		m.postLegacy(w, r, &msg, rec, sid)
		return
	}
	if msg.Method == "initialize" && m.cfg.Era.legacyStreamable() {
		m.postInitialize(w, &msg, rec)
		return
	}
	if !m.cfg.Era.Stateless {
		rec.Rejected = "no_session"
		m.state.record(rec)
		writeJSON(w, http.StatusBadRequest, response(msg.ID, nil,
			rpcErr(0, CodeInvalidRequest, "missing Mcp-Session-Id: send initialize first")))
		return
	}
	m.postStateless(w, r, &msg, rec)
}

func (m *Mock) reject(w http.ResponseWriter, r *http.Request, msg *Message, why string, status int, e *RPCError) {
	rec := m.httpReceived(r, "http", msg)
	rec.Rejected = why
	m.state.record(rec)
	var id json.RawMessage
	if msg != nil {
		id = msg.ID
	}
	writeJSON(w, status, response(id, nil, e))
}

func (m *Mock) postInitialize(w http.ResponseWriter, msg *Message, rec Received) {
	version, caps, result, rerr := m.initializeResult(msg.Params)
	rec.Era = version
	m.state.record(rec)
	if rerr != nil {
		writeJSON(w, http.StatusOK, response(msg.ID, nil, rerr))
		return
	}
	s := m.sessions.create(version, caps, false)
	w.Header().Set("Mcp-Session-Id", s.id)
	writeJSON(w, http.StatusOK, response(msg.ID, result, nil))
}

func (m *Mock) postLegacy(w http.ResponseWriter, r *http.Request, msg *Message, rec Received, sid string) {
	s := m.sessions.get(sid)
	rec.SessionID = sid
	if s == nil || s.httpSSE {
		rec.Rejected = "session_not_found"
		m.state.record(rec)
		writeJSON(w, http.StatusNotFound, response(msg.ID, nil, rpcErr(0, CodeSessionNotFound, "session not found")))
		return
	}
	version, caps := s.negotiated()
	rec.Era = version
	if pv := r.Header.Get("MCP-Protocol-Version"); pv != "" && pv != version {
		rec.Rejected = "protocol_version"
		m.state.record(rec)
		writeJSON(w, http.StatusBadRequest, response(msg.ID, nil,
			rpcErr(0, CodeInvalidRequest, "MCP-Protocol-Version %q does not match the session's %q", pv, version)))
		return
	}
	m.state.record(rec)
	switch {
	case msg.isResponse():
		s.answer(msg)
		w.WriteHeader(http.StatusAccepted)
	case msg.isNotification():
		w.WriteHeader(http.StatusAccepted)
	case msg.Method == "initialize":
		writeJSON(w, http.StatusBadRequest, response(msg.ID, nil, rpcErr(0, CodeInvalidRequest, "the session is already initialized")))
	default:
		m.respond(w, r, &call{version: version, caps: caps}, msg, s)
	}
}

func (m *Mock) postStateless(w http.ResponseWriter, r *http.Request, msg *Message, rec Received) {
	rec.Era = V2026
	meta, rerr := checkMeta(msg.Params)
	if rerr != nil {
		rec.Rejected = "meta"
	} else if rerr = checkMirroredHeaders(r, msg, meta); rerr != nil {
		rec.Rejected = "headers"
	}
	m.state.record(rec)
	if rerr != nil {
		writeJSON(w, rerr.status, response(msg.ID, nil, rerr))
		return
	}
	switch {
	case msg.isNotification():
		w.WriteHeader(http.StatusAccepted)
	case msg.isResponse():
		writeJSON(w, http.StatusBadRequest, response(msg.ID, nil,
			rpcErr(0, CodeInvalidRequest, "a %s server sends no requests, so it takes no responses", V2026)))
	case msg.Method == "subscriptions/listen":
		m.serveListen(w, r, msg)
	default:
		m.respond(w, r, &call{version: V2026, stateless: true, meta: meta, caps: meta.Caps}, msg, nil)
	}
}

// checkMirroredHeaders enforces 2026-07-28's mirrored headers: a missing or
// mismatched MCP-Protocol-Version, Mcp-Method, or (on tools/call,
// resources/read and prompts/get) Mcp-Name is HTTP 400 with -32020.
func checkMirroredHeaders(r *http.Request, msg *Message, meta requestMeta) *RPCError {
	if got := r.Header.Get("MCP-Protocol-Version"); got != meta.Version {
		return rpcErr(400, CodeHeaderMismatch, "MCP-Protocol-Version %q does not match _meta %q", got, meta.Version)
	}
	if got := r.Header.Get("Mcp-Method"); got != msg.Method {
		return rpcErr(400, CodeHeaderMismatch, "Mcp-Method %q does not match the body's %q", got, msg.Method)
	}
	switch msg.Method {
	case "tools/call", "resources/read", "prompts/get":
		if got, want := r.Header.Get("Mcp-Name"), requestName(msg.Method, msg.Params); got != want {
			return rpcErr(400, CodeHeaderMismatch, "Mcp-Name %q does not match the body's %q", got, want)
		}
	}
	return nil
}

// respond answers a request as JSON, or as an SSE stream when behavior.sse is
// armed (or a legacy tools/call must elicit). A client whose Accept lacks
// text/event-stream gets 406 instead of a stream. The call's cost-log entry
// is completed only when the result was written to a client still there.
func (m *Mock) respond(w http.ResponseWriter, r *http.Request, c *call, msg *Message, s *session) {
	b := m.state.Behavior()
	wantSSE := b.SSE || (msg.Method == "tools/call" && !c.stateless && b.Elicit && acceptsSSE(r))
	if wantSSE && !acceptsSSE(r) {
		writeJSON(w, http.StatusNotAcceptable, response(msg.ID, nil,
			rpcErr(0, CodeInvalidRequest, "the answer is an SSE stream: Accept must list text/event-stream")))
		return
	}
	if !wantSSE {
		result, rerr := m.handle(r.Context(), c, msg)
		status := http.StatusOK
		if rerr != nil && rerr.status != 0 {
			status = rerr.status
		}
		if writeJSON(w, status, response(msg.ID, result, rerr)) == nil && rerr == nil && r.Context().Err() == nil {
			m.state.complete(c.charge)
		}
		return
	}
	sw := newSSEWriter(w)
	c.stream = &postStream{sw: sw, sess: s}
	result, rerr := m.handle(r.Context(), c, msg)
	if sw.send(response(msg.ID, result, rerr)) == nil && rerr == nil && r.Context().Err() == nil {
		m.state.complete(c.charge)
	}
}

// serveGet is a legacy session's GET stream: the events after Last-Event-ID
// first, then live ones, until the client leaves or the session ends.
func (m *Mock) serveGet(w http.ResponseWriter, r *http.Request) {
	s, rec, ok := m.legacySessionRequest(w, r)
	if !ok {
		return
	}
	if !acceptsSSE(r) {
		rec.Rejected = "accept"
		m.state.record(rec)
		w.WriteHeader(http.StatusNotAcceptable)
		return
	}
	m.state.record(rec)
	last := int64(-1)
	if h := r.Header.Get("Last-Event-ID"); h != "" {
		if n, err := strconv.ParseInt(h, 10, 64); err == nil {
			last = n
		}
	}
	backlog, live, detach := s.attach(last)
	defer detach()
	sw := newSSEWriter(w)
	for _, ev := range backlog {
		if sw.event(ev.id, ev.data) != nil {
			return
		}
	}
	tick := time.NewTicker(sseKeepAlive)
	defer tick.Stop()
	for {
		select {
		case ev := <-live:
			if sw.event(ev.id, ev.data) != nil {
				return
			}
		case <-tick.C:
			if sw.comment("keepalive") != nil {
				return
			}
		case <-s.done:
			return
		case <-r.Context().Done():
			return
		}
	}
}

// serveDelete ends a legacy session; a later request on it gets 404.
func (m *Mock) serveDelete(w http.ResponseWriter, r *http.Request) {
	s, rec, ok := m.legacySessionRequest(w, r)
	if !ok {
		return
	}
	m.state.record(rec)
	m.sessions.remove(s.id)
	w.WriteHeader(http.StatusNoContent)
}

// legacySessionRequest authorizes a GET or DELETE and finds its session: 405
// when the request names no legacy session (or the mock has none), 404 when
// the session is unknown or ended.
func (m *Mock) legacySessionRequest(w http.ResponseWriter, r *http.Request) (*session, Received, bool) {
	rec := m.httpReceived(r, "http", nil)
	sid := r.Header.Get("Mcp-Session-Id")
	rec.SessionID = sid
	principal, ok := m.authorize(w, r)
	if !ok {
		rec.Rejected = "unauthorized"
		m.state.record(rec)
		return nil, rec, false
	}
	rec.Principal = principal
	if sid == "" || !m.cfg.Era.legacyStreamable() {
		rec.Rejected = "method"
		m.state.record(rec)
		w.Header().Set("Allow", "POST")
		w.WriteHeader(http.StatusMethodNotAllowed)
		return nil, rec, false
	}
	s := m.sessions.get(sid)
	if s == nil || s.httpSSE {
		rec.Rejected = "session_not_found"
		m.state.record(rec)
		w.WriteHeader(http.StatusNotFound)
		return nil, rec, false
	}
	rec.Era, _ = s.negotiated()
	return s, rec, true
}

func writeJSON(w http.ResponseWriter, status int, v any) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, err = w.Write(append(data, '\n'))
	return err
}
