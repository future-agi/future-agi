package wire

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"strconv"
	"sync"
	"sync/atomic"
)

// Tokens maps the progress tokens of the calls on one shared upstream client
// to gateway tokens unique to that client, and back: callers from different
// principals may send the same token, and none may see another's progress.
type Tokens struct {
	prefix string
	next   atomic.Int64
	mu     sync.Mutex
	calls  map[string]tokenCall // gateway token -> the call
}

type tokenCall struct {
	downstream json.RawMessage
	sink       func(params json.RawMessage)
}

// NewTokens returns an empty map with a random prefix of its own.
func NewTokens() *Tokens {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return &Tokens{prefix: "agentcc-" + hex.EncodeToString(b) + "-", calls: map[string]tokenCall{}}
}

// Issue registers one call's downstream token and the sink its progress goes
// to, and returns the gateway token to send upstream; release forgets it.
func (t *Tokens) Issue(downstream json.RawMessage, sink func(params json.RawMessage)) (json.RawMessage, func()) {
	gw := t.prefix + strconv.FormatInt(t.next.Add(1), 10)
	t.mu.Lock()
	t.calls[gw] = tokenCall{downstream: append(json.RawMessage(nil), downstream...), sink: sink}
	t.mu.Unlock()
	raw, _ := json.Marshal(gw)
	return raw, func() {
		t.mu.Lock()
		delete(t.calls, gw)
		t.mu.Unlock()
	}
}

// Route takes the params of an upstream notifications/progress: for a token
// this map issued it returns the call's sink and the params with the call's
// own token restored; ok is false for any other token, which is dropped.
func (t *Tokens) Route(params json.RawMessage) (sink func(json.RawMessage), rewritten json.RawMessage, ok bool) {
	var fields map[string]json.RawMessage
	if json.Unmarshal(params, &fields) != nil {
		return nil, nil, false
	}
	var gw string
	if json.Unmarshal(fields["progressToken"], &gw) != nil {
		return nil, nil, false
	}
	t.mu.Lock()
	call, found := t.calls[gw]
	t.mu.Unlock()
	if !found || call.sink == nil {
		return nil, nil, false
	}
	fields["progressToken"] = call.downstream
	out, err := json.Marshal(fields)
	if err != nil {
		return nil, nil, false
	}
	return call.sink, out, true
}
