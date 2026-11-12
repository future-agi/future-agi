package video

import (
	"errors"
	"fmt"
	"time"
)

var (
	ErrCancelUnsupported    = errors.New("video cancel unsupported")
	ErrReconcileUnsupported = errors.New("video reconciliation unsupported")
)

type ProviderError struct {
	Code, Message, RequestID string
	Retryable                bool
}

// Body and provider messages are untrusted and must be redacted by the lifecycle.
// Error intentionally excludes Body, which may contain secrets or signed URLs.
type UpstreamError struct {
	Status          int
	Code, RequestID string
	Retryable       bool
	// RetryAfter preserves provider backoff on non-2xx responses. Zero is absent.
	RetryAfter time.Duration
	Body       []byte
}

func (e *UpstreamError) Error() string { return fmt.Sprintf("video upstream HTTP %d", e.Status) }

type SchemaError struct {
	Field string
	Cause error
}

func (e *SchemaError) Error() string { return "video adapter schema error: " + e.Field }
func (e *SchemaError) Unwrap() error { return e.Cause }
