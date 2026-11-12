package credtemplate

import (
	"errors"
	"fmt"
)

// Error is a credential the gateway cannot place as it is stated, refused
// before anything is sent. Field names what is at fault, relative to the
// pushed MCPCredentialSecret ("in", "name", "value", "parts[1].name", ...),
// except "url" and "catalog_ref", which name the server's own fields. The
// reason never holds a value.
type Error struct {
	Field  string
	Reason string
}

func (e *Error) Error() string { return e.Field + ": " + e.Reason }

func refuse(field, format string, args ...any) *Error {
	return &Error{Field: field, Reason: fmt.Sprintf(format, args...)}
}

// scrubbedError is an error of the inner transport whose text carried a
// placed secret: Error is the scrubbed text, and the cause still unwraps,
// so errors.Is, errors.As and a timeout's classification keep working.
type scrubbedError struct {
	err error
	msg string
}

func (e *scrubbedError) Error() string { return e.msg }
func (e *scrubbedError) Unwrap() error { return e.err }

// Timeout is the cause's, so *url.Error and http.Client still see a
// timeout through the wrapper.
func (e *scrubbedError) Timeout() bool {
	var t interface{ Timeout() bool }
	return errors.As(e.err, &t) && t.Timeout()
}

// Temporary is the cause's, for callers that still ask (net.Error).
func (e *scrubbedError) Temporary() bool {
	var t interface{ Temporary() bool }
	return errors.As(e.err, &t) && t.Temporary()
}
