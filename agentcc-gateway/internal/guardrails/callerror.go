package guardrails

import (
	"context"
	"errors"
	"log/slog"
	"net"
	"net/url"
)

// The sentences a check reports when its call to a remote service fails
// before an answer. None of them names the URL or an address.
var (
	errCallInvalidURL = errors.New("the configured URL is not valid")
	errCallTimedOut   = errors.New("the service did not answer in time")
	errCallFailed     = errors.New("the connection to the service failed")
)

// CallError is the error a check reports for a failed call to its remote
// service (an evaluation API, an external provider, a webhook).
//
// A check's message reaches the organization whose request it judged: the
// 403 a block answers, a warning, the request log. So it must not say where
// the gateway connected. net/http's *url.Error repeats the whole URL (for a
// futureagi check that names no base_url, the operator's FI_BASE_URL), and
// the dial, DNS or proxy error inside it names the address the gateway's own
// network used (an in-cluster backend, an egress proxy). An error that
// carries neither (a status the service answered, a body it could not parse)
// is returned unchanged, so the org can still tell a refused key from an
// outage. The full error goes to the gateway's log, for the operator.
func CallError(guardrail string, err error) error {
	if err == nil {
		return nil
	}
	// *url.Error is a net.Error too, so hasNet covers both.
	var urlErr *url.Error
	var netErr net.Error
	hasURL := errors.As(err, &urlErr)
	hasNet := errors.As(err, &netErr)
	if !hasURL && !hasNet {
		return err
	}
	slog.Warn("guardrail call failed", "guardrail", guardrail, "error", err)
	switch {
	case hasURL && urlErr.Op == "parse":
		return errCallInvalidURL
	case errors.Is(err, context.DeadlineExceeded) || netErr.Timeout():
		return errCallTimedOut
	default:
		return errCallFailed
	}
}
