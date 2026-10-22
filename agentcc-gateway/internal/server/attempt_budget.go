package server

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// Response header of a request whose deadline ended it.
const headerDeadlineExceeded = "x-agentcc-deadline-exceeded"

// errCodeDeadlineExceeded is the 504 of a request whose deadline passed
// before any attempt could start.
const errCodeDeadlineExceeded = "deadline_exceeded"

// The request deadline, request start + x-agentcc-deadline-ms, is enforced
// where an attempt begins (beginAttempt, startAttempt), which every primary,
// retry, failover and fallback attempt goes through. No attempt starts at or
// after it, and each attempt's context carries it, so every upstream call is
// cut at it and every upstream timeout is clamped to the time left. A run's
// waits between attempts (a retry's backoff, Failover's) end at it too when
// its caller bounds the run with withRequestDeadline, as the chat paths do.
//
// A refused attempt is recorded as skipped for its reason and never sent.

// requestDeadline is the request's deadline, request start + deadline-ms,
// and whether it has one. It is fixed for the request: rc.StartTime and
// rc.Agent.DeadlineMs do not change once the agent context is parsed.
func requestDeadline(rc *models.RequestContext) (time.Time, bool) {
	if rc.Agent.DeadlineMs <= 0 || rc.StartTime.IsZero() {
		return time.Time{}, false
	}
	return rc.StartTime.Add(time.Duration(rc.Agent.DeadlineMs) * time.Millisecond), true
}

// withRequestDeadline is ctx bounded by the request's deadline, when it has
// one, for a whole run of attempts: the waits between them too (a retry's
// backoff, Failover's), not only each attempt's own context. A wait the
// deadline cuts ends the run with the last attempt's error. release frees it.
func withRequestDeadline(ctx context.Context, rc *models.RequestContext) (context.Context, context.CancelFunc) {
	if deadline, ok := requestDeadline(rc); ok {
		return context.WithDeadline(ctx, deadline)
	}
	return ctx, func() {}
}

// streamAttemptContext is the context a stream attempt's upstream call runs
// on: ctx, the request's, bounded by the request's deadline when it has one,
// so a stream that started before the deadline is still cut at it. It is
// always cancellable: the stream path cancels it when the attempt fails or
// another attempt's stream replaces it. chat_stream.go's openStream makes
// its upstream call's context with it.
func streamAttemptContext(ctx context.Context, rc *models.RequestContext) (context.Context, context.CancelFunc) {
	if deadline, ok := requestDeadline(rc); ok {
		return context.WithDeadline(ctx, deadline)
	}
	return context.WithCancel(ctx)
}

// streamCutError is the error event a stream ends with when the context its
// upstream call ran on ended at a deadline: 504 gateway_timeout (with the
// request's deadline-ms when that was the deadline), never a [DONE] that
// passes the cut answer off as complete. Providers close a stream whose
// context ended without an error (the OpenAI adapter returns on ctx.Done),
// so the stream loop asks this when its chunks close. nil when the stream
// ended otherwise: it completed, or the client
// left.
func streamCutError(callCtx context.Context, rc *models.RequestContext) *models.APIError {
	if callCtx == nil || !errors.Is(callCtx.Err(), context.DeadlineExceeded) {
		return nil
	}
	if _, ok := requestDeadline(rc); ok {
		return errDeadlineCut(rc, "The request's deadline (x-agentcc-deadline-ms) passed before the stream ended.")
	}
	return models.ErrGatewayTimeout("the stream's upstream call timed out")
}

// errDeadlineCut is the 504 gateway_timeout of an upstream call the
// request's deadline cut, with msg and the deadline-ms it had.
func errDeadlineCut(rc *models.RequestContext, msg string) *models.APIError {
	return &models.APIError{Status: http.StatusGatewayTimeout, Type: models.ErrTypeGatewayTimeout,
		Code: models.ErrTypeGatewayTimeout, Message: msg, Metadata: map[string]any{"deadline_ms": rc.Agent.DeadlineMs}}
}

// attemptRefusal is why an attempt may not start now (a models.Reason*
// value), "" when it may: the request's deadline has passed. It never lifts
// within a request, so a run that meets it stops dispatching.
func attemptRefusal(rc *models.RequestContext) string {
	if deadline, ok := requestDeadline(rc); ok && !time.Now().Before(deadline) {
		return models.ReasonDeadline
	}
	return ""
}

// attemptContext is the context of an attempt that may start: ctx bounded
// by the request's deadline when it has one, with the release that frees it
// (nil without a deadline). Returning both from one call keeps beginAttempt's
// variables single-assignment, so its finish closure captures them by value
// and an attempt still costs one allocation there.
func attemptContext(ctx context.Context, rc *models.RequestContext) (context.Context, context.CancelFunc) {
	if deadline, ok := requestDeadline(rc); ok {
		return context.WithDeadline(ctx, deadline)
	}
	return ctx, nil
}

// recordRefusedAttempt records an attempt its limits refused (reason
// deadline) as skipped: never dispatched, no latency, no health sample.
func (h *Handlers) recordRefusedAttempt(rc *models.RequestContext, kind models.AttemptKind, reason, providerID, model string) {
	class := models.ErrClassBudget
	if reason == models.ReasonDeadline {
		class = models.ErrClassTimeout
	}
	rc.AddAttempt(models.Attempt{
		Kind:           kind,
		Reason:         reason,
		Provider:       providerID,
		Model:          model,
		CredentialType: h.attemptCredential(orgCredentials(rc), providerID, credentialType(rc)),
		Outcome:        models.OutcomeSkipped,
		ErrorClass:     class,
		ErrorOrigin:    models.OriginGateway,
		StartedAt:      time.Now(),
	})
}

// refusedContext is the context beginAttempt returns for an attempt it
// refused: already done, with the refusal as its cause, so a caller that
// dispatches anyway sends nothing (net/http checks the context before it
// dials). It is ended by a deadline in the past, not a cancellation, so a
// caller that keeps only its deadlines (context.WithoutCancel, as a stream's
// upstream call does) still gets a done context.
func refusedContext(ctx context.Context, reason string) context.Context {
	done, cancel := context.WithDeadlineCause(ctx, time.Time{}, routing.SkipHop(nil, reason))
	cancel()
	return done
}

// attemptLimitError is the error a request that failed is answered with,
// and the header that says its deadline ended it. A run whose last hop its
// limits refused carries the hop-skipped sentinel: the answer is the last
// dispatched attempt's own error, or, when no attempt could start before the
// deadline, 504 deadline_exceeded. The chat paths call it on their failure
// (chat_nonstream.go and chat_stream.go).
//
// A bare context error the request's deadline caused (a stream's wait for
// its first chunk returns the attempt context's own error, which no provider
// mapped) is answered 504 gateway_timeout, like a non-stream attempt the
// deadline cut.
func (h *Handlers) attemptLimitError(w http.ResponseWriter, rc *models.RequestContext, err error) error {
	deadlineRefused := false
	for _, a := range rc.AttemptsSnapshot() {
		if a.Outcome == models.OutcomeSkipped && a.Reason == models.ReasonDeadline {
			deadlineRefused = true
		}
	}
	deadline, hasDeadline := requestDeadline(rc)
	passed := hasDeadline && (deadlineRefused || !time.Now().Before(deadline))
	if passed {
		w.Header().Set(headerDeadlineExceeded, "true")
	}

	reason, prev, skipped := routing.SkippedHop(err)
	switch {
	case !skipped:
		var apiErr *models.APIError
		if passed && errors.Is(err, context.DeadlineExceeded) && !errors.As(err, &apiErr) {
			return errDeadlineCut(rc, "The request's deadline (x-agentcc-deadline-ms) passed before the upstream answered.")
		}
		return err
	case prev != nil:
		return prev
	case reason == models.ReasonDeadline:
		return &models.APIError{Status: http.StatusGatewayTimeout, Type: models.ErrTypeGatewayTimeout,
			Code: errCodeDeadlineExceeded, Message: "The request's deadline (x-agentcc-deadline-ms) passed before any attempt could start.",
			Metadata: map[string]any{"deadline_ms": rc.Agent.DeadlineMs}}
	default:
		return models.ErrServiceUnavailable("No upstream attempt could be made for this request (" + reason + ").")
	}
}
