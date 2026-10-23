package guardrails

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"net/url"
	"syscall"
)

// CallFailure says why an outbound guardrail call failed, in words that name
// no URL, host, address or path.
//
// A guardrail's message reaches the caller: in a block response, in a
// warning, and in the request log an org reads. So a check reports the kind
// of failure from here and logs the error itself for the operator.
//
// An error that is not a transport error is the guardrail's own text (an HTTP
// status, a response that does not parse) and is returned as it is, so
// callers wrap transport errors with %w, never %v.
func CallFailure(err error) string {
	if err == nil {
		return ""
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "the request timed out"
	}
	if errors.Is(err, context.Canceled) {
		return "the request was cancelled"
	}

	var dnsErr *net.DNSError
	var certErr *tls.CertificateVerificationError
	var urlErr *url.Error
	var netErr net.Error
	switch {
	case errors.As(err, &dnsErr):
		return "the host name could not be resolved"
	case errors.Is(err, syscall.ECONNREFUSED):
		return "the connection was refused"
	case errors.As(err, &certErr):
		return "the TLS certificate was not accepted"
	case errors.As(err, &netErr) && netErr.Timeout():
		return "the request timed out"
	case errors.As(err, &urlErr) && urlErr.Op == "parse":
		return "the URL is not valid"
	case errors.As(err, &urlErr):
		return "the service could not be reached"
	case errors.As(err, &netErr):
		return "the connection failed"
	}
	return err.Error()
}
