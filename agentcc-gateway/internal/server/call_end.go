package server

import (
	"context"
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// callEndMetadataKey is the gateway's request metadata key that says why a
// call it sent upstream ended before its answer finished. The request log
// carries it so Logs can name the cause. Nothing else reads it: routing,
// health, retries, the circuit breaker and billing read the attempts and
// usage_unreadable, never this key. It is set only on such a call, and a
// caller cannot set it (reservedMetadataKeys).
const callEndMetadataKey = "call_end"

// The causes call_end names.
const (
	// The client went away: its request's context was cancelled, or a write
	// to it failed while no time limit had passed.
	callEndClientCancelled = "client_cancelled"
	// The provider ended the call while the request was still live: the
	// connection dropped, or the stream failed, after the call was sent.
	callEndProviderStopped = "provider_stopped"
	// The request's own time limit passed: the client's timeout header or its
	// x-agentcc-deadline-ms (middleware.ErrRequestTimeLimit).
	callEndDeadline = "deadline"
	// The gateway's own time limit passed: the timeout it gives the model
	// (the org's model timeout, the node's, a failover attempt's, or the
	// server's ceiling).
	callEndGatewayTimeout = "gateway_timeout"
)

// callEndAfter is why a call that was sent ended early. The first of ctxs
// that ended says it: cancelled is the client; past its deadline is the
// request's own limit (middleware.ErrRequestTimeLimit as the cause) or else
// the gateway's. With none ended, failure says it: a timeout of the gateway's
// own (its client's time limit for the provider, by the error's class), or
// else the provider stopped.
func callEndAfter(failure error, ctxs ...context.Context) string {
	for _, ctx := range ctxs {
		if ctx == nil || ctx.Err() == nil {
			continue
		}
		if !errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return callEndClientCancelled
		}
		if errors.Is(context.Cause(ctx), middleware.ErrRequestTimeLimit) {
			return callEndDeadline
		}
		return callEndGatewayTimeout
	}
	if failure != nil {
		if class, origin := routing.ClassifyError(failure, 0); class == models.ErrClassTimeout && origin == models.OriginGateway {
			return callEndGatewayTimeout
		}
	}
	return callEndProviderStopped
}

// streamCallEnd is why a stream that did not end at its upstream's own close
// ended: its caller stopped it (callerStoppedStream), the request's context
// or the stream's upstream call ran past a time limit, or the stream failed
// with failure (nil when it got none) while the request was live.
func streamCallEnd(reqCtx, callCtx context.Context, clientWriteFailed bool, failure *models.APIError) string {
	if callerStoppedStream(reqCtx, clientWriteFailed) {
		return callEndClientCancelled
	}
	if callCtx != nil && !errors.Is(callCtx.Err(), context.DeadlineExceeded) {
		callCtx = nil // a call the gateway itself cancelled says nothing more
	}
	var err error
	if failure != nil {
		err = failure
	}
	return callEndAfter(err, reqCtx, callCtx)
}

// markCallEnd records why the request's latest attempt ended early.
func markCallEnd(rc *models.RequestContext, cause string) {
	rc.SetMetadata(callEndMetadataKey, cause)
}

// clearCallEnd drops an earlier attempt's call_end when another attempt is
// sent: the key speaks for the request's latest attempt, as usage_unreadable
// does (clearDispatchedWithoutUsage).
func clearCallEnd(rc *models.RequestContext) {
	rc.DeleteMetadata(callEndMetadataKey)
}

// markStreamCallEnd records why a stream ended, unless it ended at its
// upstream's own close or a guardrail stopped it (that refusal is the
// guardrail's, logged as such).
func markStreamCallEnd(rc *models.RequestContext, upstreamEnded bool, reqCtx, callCtx context.Context, clientWriteFailed bool, failure *models.APIError) {
	if upstreamEnded || rc.Flags.AnswerRefused {
		return
	}
	markCallEnd(rc, streamCallEnd(reqCtx, callCtx, clientWriteFailed, failure))
}
