package models

import (
	"context"
	"net/http/httptrace"
	"sync/atomic"
)

// HeaderDispatched is the response header that says the gateway refused a
// request before it sent it to any model: "false" on such a refusal, absent
// otherwise. A client can tell the gateway's own refusal from a provider's
// answer passed through with it, whatever the error code says.
const HeaderDispatched = "X-Agentcc-Dispatched"

// DispatchRecord records whether a request was sent to a model. RequestID
// gives every request one (WithRequestScope), and the response writer reads
// it when it writes the status.
type DispatchRecord struct {
	sent atomic.Bool
}

// Dispatched reports whether the request was sent to a model.
func (r *DispatchRecord) Dispatched() bool {
	return r != nil && r.sent.Load()
}

type dispatchRecordKey struct{}

// requestScope is one context value that carries both the request id
// (ContextKeyRequestID, as WithRequestID stores it) and the request's
// DispatchRecord, so a request allocates no more than it did for the id
// alone: the scope replaces the id's context value, and the id is boxed once.
type requestScope struct {
	context.Context
	id       any
	dispatch DispatchRecord
}

func (s *requestScope) Value(key any) any {
	switch key {
	case ContextKeyRequestID:
		return s.id
	case dispatchRecordKey{}:
		return &s.dispatch
	}
	return s.Context.Value(key)
}

// WithRequestScope returns ctx carrying the request id, read as
// GetRequestID reads it, and a new DispatchRecord, which it also returns.
// The record is not pooled: a call still running after the response (a
// stream's late finish) may mark it, so no other request may share it.
func WithRequestScope(ctx context.Context, id string) (context.Context, *DispatchRecord) {
	s := &requestScope{Context: ctx, id: id}
	return s, &s.dispatch
}

// MarkDispatched records, on the record ctx carries, that the request is
// being sent to a model. Call it right before the call goes out, never for a
// call the gateway refuses or skips.
func MarkDispatched(ctx context.Context) {
	if r, ok := ctx.Value(dispatchRecordKey{}).(*DispatchRecord); ok && r != nil {
		r.sent.Store(true)
	}
}

// WithDispatchTrace returns ctx with a net/http client trace that marks the
// record ctx carries as soon as any HTTP request made under it starts: every
// provider sends through net/http with the context it is given, and net/http
// calls GetConn before each request it sends, on any client or transport. A
// provider call made under the returned context therefore marks the request
// whatever route made it. ctx is returned unchanged when it carries no record.
func WithDispatchTrace(ctx context.Context) context.Context {
	r, ok := ctx.Value(dispatchRecordKey{}).(*DispatchRecord)
	if !ok || r == nil {
		return ctx
	}
	return httptrace.WithClientTrace(ctx, &httptrace.ClientTrace{
		GetConn: func(string) { r.sent.Store(true) },
	})
}
