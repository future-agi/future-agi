package mcp

import (
	"context"
	"net/http/httptrace"
)

// The dispatch boundary of an upstream request (M13's client; M15 IR-4,
// IC-R1-8): a tools/call is dispatched, and so "may have executed"
// (_meta["io.futureagi/tool_call"].executed), only once its request bytes
// were written to the upstream connection. A call that never left the
// gateway is reported and metered as not executed.

type dispatchNoticeKey struct{}

// WithDispatchNotice returns ctx carrying fn, which the client calls once a
// request it sends with ctx has its bytes written to the upstream: an HTTP
// request (Streamable HTTP, and the 2024-11-05 era's POST) once net/http
// reports it written without an error (httptrace's WroteRequest), a stdio
// request once its write to the child's stdin returned without one. A
// request that never left the gateway (a failed dial, a refused connection,
// an unhealthy client, a closed or stopped transport, a write cut short)
// never calls it.
//
// fn may run on the transport's goroutine, and more than once (net/http
// retries a request on a new connection when nothing was sent on a reused
// one), so the caller keeps what it records in an atomic and reads it after
// the call returns. WroteRequest fires once the request is in the
// connection's write buffer, before the flush: a reused connection whose
// flush then fails with nothing sent still calls fn, so such a call reports
// "may have executed", the safe direction.
func WithDispatchNotice(ctx context.Context, fn func()) context.Context {
	return context.WithValue(ctx, dispatchNoticeKey{}, fn)
}

// dispatchNotice is ctx's WithDispatchNotice function, or nil.
func dispatchNotice(ctx context.Context) func() {
	fn, _ := ctx.Value(dispatchNoticeKey{}).(func())
	return fn
}

// withWroteRequestNotice returns ctx tracing the HTTP requests made with it,
// so that ctx's dispatch notice runs once one is written without an error.
// ctx is returned as is when it carries no notice.
func withWroteRequestNotice(ctx context.Context) context.Context {
	fn := dispatchNotice(ctx)
	if fn == nil {
		return ctx
	}
	return httptrace.WithClientTrace(ctx, &httptrace.ClientTrace{
		WroteRequest: func(info httptrace.WroteRequestInfo) {
			if info.Err == nil {
				fn()
			}
		},
	})
}

// notifyDispatched runs ctx's dispatch notice, if it has one: a transport
// that writes requests itself (stdio) calls it once a write succeeded.
func notifyDispatched(ctx context.Context) {
	if fn := dispatchNotice(ctx); fn != nil {
		fn()
	}
}
