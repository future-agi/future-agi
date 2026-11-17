package auth

import (
	"context"
	"errors"
)

// KeySyncError is safe to include in operator logs. It stores only closed
// diagnostic enums: never a control-plane body, endpoint, or underlying error.
// Reason and StatusClass provide the same finite vocabulary as Error.
// errors.Is exposes only the trusted migration and context sentinels.
type KeySyncError struct {
	reason      keySyncReason
	statusClass keySyncStatusClass
}

type keySyncReason uint8

const (
	keySyncUnknown keySyncReason = iota
	keySyncRequestInvalid
	keySyncTransportFailed
	keySyncCanceled
	keySyncDeadlineExceeded
	keySyncHTTPStatus
	keySyncMigrationsPending
	keySyncResponseInvalid
	keySyncStatusFalse
	keySyncKeyInvalid
)

type keySyncStatusClass uint8

const (
	keySyncNoStatus keySyncStatusClass = iota
	keySync1xx
	keySync2xx
	keySync3xx
	keySync4xx
	keySync5xx
	keySyncOtherStatus
)

func newKeySyncError(reason keySyncReason, status int) *KeySyncError {
	class := keySyncOtherStatus
	switch {
	case status == 0:
		class = keySyncNoStatus
	case status >= 100 && status < 200:
		class = keySync1xx
	case status >= 200 && status < 300:
		class = keySync2xx
	case status >= 300 && status < 400:
		class = keySync3xx
	case status >= 400 && status < 500:
		class = keySync4xx
	case status >= 500 && status < 600:
		class = keySync5xx
	}
	return &KeySyncError{reason: reason, statusClass: class}
}

// Classify identity without retaining err or its possibly credential-bearing
// URL/message. Do not add arbitrary causes to KeySyncError's unwrap chain.
func keySyncTransportError(err error) *KeySyncError {
	reason := keySyncTransportFailed
	switch {
	case errors.Is(err, context.Canceled):
		reason = keySyncCanceled
	case errors.Is(err, context.DeadlineExceeded):
		reason = keySyncDeadlineExceeded
	}
	return newKeySyncError(reason, 0)
}

func (e *KeySyncError) Error() string {
	return "key sync failed: reason=" + e.Reason() + " status_class=" + e.StatusClass()
}

// Reason returns a finite code, never the source error text.
func (e *KeySyncError) Reason() string {
	switch e.reason {
	case keySyncRequestInvalid:
		return "request_invalid"
	case keySyncTransportFailed:
		return "transport_failed"
	case keySyncCanceled:
		return "canceled"
	case keySyncDeadlineExceeded:
		return "deadline_exceeded"
	case keySyncHTTPStatus:
		return "http_status"
	case keySyncMigrationsPending:
		return MigrationsPendingCode
	case keySyncResponseInvalid:
		return "response_invalid"
	case keySyncStatusFalse:
		return "status_false"
	case keySyncKeyInvalid:
		return "key_invalid"
	default:
		return "unknown"
	}
}

// StatusClass deliberately omits the endpoint and exact upstream status.
func (e *KeySyncError) StatusClass() string {
	switch e.statusClass {
	case keySyncNoStatus:
		return "none"
	case keySync1xx:
		return "1xx"
	case keySync2xx:
		return "2xx"
	case keySync3xx:
		return "3xx"
	case keySync4xx:
		return "4xx"
	case keySync5xx:
		return "5xx"
	default:
		return "other"
	}
}

// Unwrap preserves errors.Is behavior only for fixed trusted sentinels. The
// original transport, parsing and response errors must never be attached here.
func (e *KeySyncError) Unwrap() error {
	switch e.reason {
	case keySyncMigrationsPending:
		return ErrAppNotMigrated
	case keySyncCanceled:
		return context.Canceled
	case keySyncDeadlineExceeded:
		return context.DeadlineExceeded
	default:
		return nil
	}
}
