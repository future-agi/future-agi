// Package media verifies untrusted media with bounded memory and guarded egress.
package media

import (
	"context"
	"io"
	"time"
)

type Limits struct {
	MaxBytes                   int64
	Deadline                   time.Duration
	MaxPixels                  int64
	MinDimension, MaxDimension int
	MinRatio, MaxRatio         float64
}

// Error deliberately excludes source URLs, query strings and origin messages.
type Error struct {
	Status int
	Code   string
	cause  error
}

func (e *Error) Error() string              { return e.Code }
func (e *Error) Unwrap() error              { return e.cause }
func failure(status int, code string) error { return &Error{Status: status, Code: code} }

type cappedReader struct {
	ctx  context.Context
	r    io.Reader
	left int64
}

func (r *cappedReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	if len(p) == 0 {
		return 0, nil
	}
	if r.left == 0 {
		var b [1]byte
		n, e := r.r.Read(b[:])
		if n > 0 {
			return 0, failure(413, "media_too_large")
		}
		return 0, e
	}
	if int64(len(p)) > r.left {
		p = p[:r.left]
	}
	n, err := r.r.Read(p)
	r.left -= int64(n)
	return n, err
}
