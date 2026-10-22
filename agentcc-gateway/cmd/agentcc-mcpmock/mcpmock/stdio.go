package mcpmock

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"sync"
)

// errStdinClosed ends a request the mock sent the client (an elicitation)
// once stdin has closed: its answer cannot arrive.
var errStdinClosed = errors.New("stdin closed")

// stdioPeer is the one client of a stdio mock: newline-delimited JSON-RPC
// written to stdout, and the requests the mock sent it (elicitations) waiting
// for answers on stdin.
type stdioPeer struct {
	mu      sync.Mutex
	out     io.Writer
	pendMu  sync.Mutex
	pending map[string]chan *Message
	inDone  chan struct{} // closed when stdin closes
}

func (p *stdioPeer) send(msg *Message) error {
	data, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	_, err = p.out.Write(append(data, '\n'))
	return err
}

func (p *stdioPeer) ask(ctx context.Context, req *Message) (*Message, error) {
	ch := make(chan *Message, 1)
	p.pendMu.Lock()
	p.pending[string(req.ID)] = ch
	p.pendMu.Unlock()
	defer func() {
		p.pendMu.Lock()
		delete(p.pending, string(req.ID))
		p.pendMu.Unlock()
	}()
	if err := p.send(req); err != nil {
		return nil, err
	}
	select {
	case resp := <-ch:
		return resp, nil
	case <-p.inDone:
		return nil, errStdinClosed
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (p *stdioPeer) answer(msg *Message) {
	p.pendMu.Lock()
	ch, ok := p.pending[string(msg.ID)]
	p.pendMu.Unlock()
	if ok {
		ch <- msg
	}
}

// stdioConn is the per-process protocol state: the legacy handshake, if one
// ran, and the in-flight requests notifications/cancelled can stop.
type stdioConn struct {
	mu       sync.Mutex
	version  string
	caps     json.RawMessage
	inflight map[string]context.CancelFunc
}

// ServeStdio serves newline-delimited JSON-RPC from in to out until in
// closes, then waits for the requests still running; a call waiting on the
// client's answer to an elicitation fails at once, since that answer would
// come on in. Server notifications (list_changed from /__control/tools,
// notify) are written to out as well.
func (m *Mock) ServeStdio(in io.Reader, out io.Writer) error {
	peer := &stdioPeer{out: out, pending: map[string]chan *Message{}, inDone: make(chan struct{})}
	remove := m.listen(func(n *Message) { _ = peer.send(n) })
	defer remove()
	conn := &stdioConn{inflight: map[string]context.CancelFunc{}}
	sc := bufio.NewScanner(in)
	sc.Buffer(make([]byte, 0, 64<<10), maxBodyBytes)
	var wg sync.WaitGroup
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var msg Message
		if err := json.Unmarshal(line, &msg); err != nil || msg.JSONRPC != "2.0" {
			m.state.record(Received{Transport: "stdio", Rejected: "parse"})
			_ = peer.send(response(nil, nil, rpcErr(0, CodeParse, "want one JSON-RPC 2.0 message per line")))
			continue
		}
		m.stdioMessage(&wg, conn, peer, &msg)
	}
	close(peer.inDone)
	wg.Wait()
	return sc.Err()
}

func (m *Mock) stdioMessage(wg *sync.WaitGroup, conn *stdioConn, peer *stdioPeer, msg *Message) {
	rec := Received{Transport: "stdio", Method: msg.Method, ID: msg.ID,
		Name: requestName(msg.Method, msg.Params), Meta: metaOf(msg.Params)}
	switch {
	case msg.isResponse():
		m.state.record(rec)
		peer.answer(msg)
		return
	case msg.Method == "initialize" && len(m.cfg.Era.Legacy) > 0:
		version, caps, result, rerr := m.initializeResult(msg.Params)
		if rerr == nil {
			conn.mu.Lock()
			conn.version, conn.caps = version, caps
			conn.mu.Unlock()
		}
		rec.Era = version
		m.state.record(rec)
		_ = peer.send(response(msg.ID, result, rerr))
		return
	}
	c, rerr := m.stdioCall(conn, msg)
	rec.Era = c.version
	m.state.record(rec)
	if msg.isNotification() {
		if msg.Method == "notifications/cancelled" {
			conn.cancel(msg.Params)
		}
		return
	}
	if rerr != nil {
		_ = peer.send(response(msg.ID, nil, rerr))
		return
	}
	c.stream = peer
	ctx, cancel := context.WithCancel(context.Background())
	conn.mu.Lock()
	conn.inflight[string(msg.ID)] = cancel
	conn.mu.Unlock()
	wg.Add(1)
	go func() {
		defer wg.Done()
		defer func() {
			conn.mu.Lock()
			delete(conn.inflight, string(msg.ID))
			conn.mu.Unlock()
			cancel()
		}()
		result, rerr := m.handle(ctx, c, msg)
		if peer.send(response(msg.ID, result, rerr)) == nil && rerr == nil && ctx.Err() == nil {
			m.state.complete(c.charge)
		}
	}()
}

// stdioCall picks the era a stdio request is served under: 2026 when it
// carries 2026 _meta (or no handshake ran and the mock speaks 2026), else the
// legacy session the handshake opened.
func (m *Mock) stdioCall(conn *stdioConn, msg *Message) (*call, *RPCError) {
	conn.mu.Lock()
	version, caps := conn.version, conn.caps
	conn.mu.Unlock()
	if m.cfg.Era.Stateless && (version == "" || hasMetaVersion(msg.Params)) {
		meta, rerr := checkMeta(msg.Params)
		return &call{version: V2026, stateless: true, meta: meta, caps: meta.Caps}, rerr
	}
	if version == "" {
		return &call{}, rpcErr(0, CodeInvalidRequest, "send initialize first")
	}
	return &call{version: version, caps: caps}, nil
}

func hasMetaVersion(params json.RawMessage) bool {
	var p struct {
		Meta map[string]json.RawMessage `json:"_meta"`
	}
	_ = json.Unmarshal(params, &p)
	_, ok := p.Meta[metaProtocolVersion]
	return ok
}

// cancel stops an in-flight request named by notifications/cancelled.
func (conn *stdioConn) cancel(params json.RawMessage) {
	var p struct {
		RequestID json.RawMessage `json:"requestId"`
	}
	_ = json.Unmarshal(params, &p)
	conn.mu.Lock()
	cancel := conn.inflight[string(p.RequestID)]
	conn.mu.Unlock()
	if cancel != nil {
		cancel()
	}
}
