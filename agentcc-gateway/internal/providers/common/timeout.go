package common

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TimedOut reports whether a failed provider call ran out of time, rather than
// failing for a reason the upstream chose.
//
// Two clocks can end a call and only one of them shows up on ctx. The
// request's own deadline does. The provider's http.Client.Timeout does not: it
// aborts the call and leaves ctx untouched, so err is the only evidence the
// second clock expired. A caller that asks ctx alone therefore reads a
// provider ceiling as an upstream failure and answers 502.
//
// This classifies; it does not stop a retry. Both SDKs retry 504 as readily as
// 502, this gateway's own retry and failover lists hold both, and the
// Anthropic stream-start loop retries either. What a correct status buys is the
// ability to tell a timeout from an upstream failure — in a log, in a metric,
// and in a client that chooses to treat them differently. Suppressing the
// duplicate work needs a retry policy that excludes timeouts, which is a
// separate change.
func TimedOut(ctx context.Context, err error) bool {
	return ctx.Err() != nil || timedOut(err)
}

// timedOut asks the error alone, which is all there is to ask once a response
// has started: a body or a stream that stalls fails on a deadline the error
// carries, and the request context may still be live.
func timedOut(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, os.ErrDeadlineExceeded) {
		return true
	}
	var netErr net.Error
	return errors.As(err, &netErr) && netErr.Timeout()
}

// ReadError classifies a failure to read a provider's response.
//
// The call that produced it already returned headers, so the earlier
// classification at the Do boundary never sees this one. A body or stream that
// stalls until a deadline is a timeout, and reporting it as an upstream
// failure makes it a 502 that an SDK retries — on a request that has already
// spent its budget. The message is unchanged for every other cause.
func ReadError(err error, status int, label string) *models.APIError {
	if timedOut(err) {
		return models.ErrGatewayTimeout(label + " timed out")
	}
	return models.ErrUpstreamProvider(status, fmt.Sprintf("%s: %v", label, err))
}
