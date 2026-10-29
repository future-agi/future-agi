package common

import (
	"context"
	"errors"
	"net"
	"net/url"
	"os"
	"testing"
)

type timeoutErr struct{}

func (timeoutErr) Error() string   { return "i/o timeout" }
func (timeoutErr) Timeout() bool   { return true }
func (timeoutErr) Temporary() bool { return false }

func TestTimedOut(t *testing.T) {
	done, cancel := context.WithCancel(context.Background())
	cancel()

	var netErr net.Error = timeoutErr{}

	tests := []struct {
		name string
		ctx  context.Context
		err  error
		want bool
	}{
		{"the request deadline expired", done, errors.New("whatever"), true},
		{"a client timeout, ctx still live", context.Background(), &url.Error{Op: "Post", Err: netErr}, true},
		{"a bare deadline error", context.Background(), context.DeadlineExceeded, true},
		{"an i/o deadline error", context.Background(), os.ErrDeadlineExceeded, true},
		{"connection refused", context.Background(), errors.New("connect: connection refused"), false},
		{"no error at all", context.Background(), nil, false},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := TimedOut(tc.ctx, tc.err); got != tc.want {
				t.Errorf("TimedOut() = %v, want %v", got, tc.want)
			}
		})
	}
}
