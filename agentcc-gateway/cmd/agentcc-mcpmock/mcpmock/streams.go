package mcpmock

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"time"
)

// serveListen is 2026-07-28's subscriptions/listen: one long-lived POST
// stream carrying the notifications the client opted into (params
// "notifications", a list of method names; empty means all), each tagged with
// the stream's subscriptionId. It opens with a ": listening <id>" comment, so
// a client knows it is subscribed. The param and tag shapes are not checked
// against the published schema.
func (m *Mock) serveListen(w http.ResponseWriter, r *http.Request, msg *Message) {
	if !acceptsSSE(r) {
		writeJSON(w, http.StatusNotAcceptable, response(msg.ID, nil,
			rpcErr(0, CodeInvalidRequest, "subscriptions/listen is a stream: Accept must list text/event-stream")))
		return
	}
	var p struct {
		Notifications []string `json:"notifications"`
	}
	_ = json.Unmarshal(msg.Params, &p)
	want := map[string]bool{}
	for _, n := range p.Notifications {
		want[n] = true
	}
	subID := randomID()[:12]
	ch := make(chan *Message, 64)
	remove := m.listen(func(n *Message) {
		if len(want) > 0 && !want[n.Method] {
			return
		}
		select {
		case ch <- n:
		default:
		}
	})
	defer remove()
	sw := newSSEWriter(w)
	if sw.comment("listening "+subID) != nil {
		return
	}
	tick := time.NewTicker(sseKeepAlive)
	defer tick.Stop()
	for {
		select {
		case n := <-ch:
			if sw.send(tagged(n, subID)) != nil {
				return
			}
		case <-tick.C:
			if sw.comment("keepalive") != nil {
				return
			}
		case <-r.Context().Done():
			return
		}
	}
}

func tagged(n *Message, subID string) *Message {
	params := map[string]any{}
	_ = json.Unmarshal(n.Params, &params)
	params["subscriptionId"] = subID
	return notification(n.Method, params)
}

// serveSSEStream is 2024-11-05's GET /sse: the stream opens with an
// "endpoint" event naming /messages?sessionId=<id>, then carries every answer
// and notification for that session as "message" events. The session ends
// with the stream.
func (m *Mock) serveSSEStream(w http.ResponseWriter, r *http.Request) {
	rec := m.httpReceived(r, "sse", nil)
	principal, ok := m.authorize(w, r)
	if !ok {
		rec.Rejected = "unauthorized"
		m.state.record(rec)
		return
	}
	rec.Principal = principal
	s := m.sessions.create("", nil, true)
	defer m.sessions.remove(s.id)
	rec.SessionID = s.id
	m.state.record(rec)
	_, live, detach := s.attach(-1)
	defer detach()
	sw := newSSEWriter(w)
	if sw.named("endpoint", "/messages?sessionId="+s.id) != nil {
		return
	}
	tick := time.NewTicker(sseKeepAlive)
	defer tick.Stop()
	for {
		select {
		case ev := <-live:
			if sw.named("message", string(ev.data)) != nil {
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

// serveSSEMessage is 2024-11-05's POST /messages?sessionId=<id>: 202 at once,
// with the answer sent on the session's /sse stream.
func (m *Mock) serveSSEMessage(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes))
	var msg Message
	if err != nil || json.Unmarshal(body, &msg) != nil || msg.JSONRPC != "2.0" {
		m.reject(w, r, nil, "parse", http.StatusBadRequest, rpcErr(0, CodeParse, "want one JSON-RPC 2.0 message"))
		return
	}
	rec := m.httpReceived(r, "sse", &msg)
	principal, ok := m.authorize(w, r)
	if !ok {
		rec.Rejected = "unauthorized"
		m.state.record(rec)
		return
	}
	rec.Principal = principal
	sid := r.URL.Query().Get("sessionId")
	rec.SessionID = sid
	s := m.sessions.get(sid)
	if s == nil || !s.httpSSE {
		rec.Rejected = "session_not_found"
		m.state.record(rec)
		w.WriteHeader(http.StatusNotFound)
		return
	}
	stream := sessionStream{sess: s}
	if msg.Method == "initialize" {
		version, caps, result, rerr := m.initializeResult(msg.Params)
		if rerr == nil {
			s.setNegotiated(version, caps)
		}
		rec.Era = version
		m.state.record(rec)
		w.WriteHeader(http.StatusAccepted)
		_ = stream.send(response(msg.ID, result, rerr))
		return
	}
	version, caps := s.negotiated()
	rec.Era = version
	m.state.record(rec)
	w.WriteHeader(http.StatusAccepted)
	switch {
	case msg.isResponse():
		s.answer(&msg)
		return
	case msg.isNotification():
		return
	}
	// The answer outlives this POST, so it runs on a context that ends with
	// the session (its /sse stream), not with the request.
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		select {
		case <-s.done:
		case <-ctx.Done():
		}
		cancel()
	}()
	go func() {
		defer cancel()
		c := &call{version: version, caps: caps, stream: stream}
		result, rerr := m.handle(ctx, c, &msg)
		if stream.send(response(msg.ID, result, rerr)) == nil && rerr == nil && ctx.Err() == nil {
			m.state.complete(c.charge)
		}
	}()
}
