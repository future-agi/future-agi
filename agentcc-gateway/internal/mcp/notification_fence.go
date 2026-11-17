package mcp

import "context"

// GuardUpstreamNotifications fences each forwarded progress/stream message,
// not merely the final result. The upstream may ignore cancellation or deliver
// an already buffered chunk after revocation; neither may escape this sink.
func GuardUpstreamNotifications(ctx context.Context, check func() error) context.Context {
	sink, _ := ctx.Value(notificationSinkKey{}).(func(*Message))
	if sink == nil {
		return ctx
	}
	return WithUpstreamNotifications(ctx, func(msg *Message) {
		if ctx.Err() == nil && check() == nil && ctx.Err() == nil {
			sink(msg)
		}
	})
}
