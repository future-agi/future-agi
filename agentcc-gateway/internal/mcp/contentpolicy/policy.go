// Package contentpolicy binds the pure contentbounds scanner to MCP's byte
// envelopes. It contains product limits, not authentication or cache identity.
package contentpolicy

import (
	"bytes"
	"errors"
	"io"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentbounds"
)

const (
	DefaultBytes   int64 = 8 << 20
	MaxDepth       int64 = 128
	MaxNodes       int64 = 100000
	MaxItems       int64 = 5000
	MaxPages       int64 = 50
	MaxCursorBytes int64 = 4096
)

var ErrShape = errors.New("content_bounds_shape")
var ErrPages = errors.New("content_bounds_pages")

// ByteLimit never widens a positive inherited ceiling. A legacy zero/unset
// transport now takes the finite product default rather than being unlimited.
func ByteLimit(inherited ...int64) int64 {
	n := DefaultBytes
	for _, v := range inherited {
		if v > 0 {
			n = min(n, v)
		}
	}
	return n
}

func Limits(inherited int64) contentbounds.Limits {
	n := ByteLimit(inherited)
	return contentbounds.Limits{MaxReadBytes: n, MaxItemBytes: n, MaxTotalBytes: n, MaxItems: MaxItems, MaxDepth: MaxDepth, MaxNodes: MaxNodes}
}

// ReadObject returns only the exact bytes whose scan completed. On refusal
// neither the partial body nor a receipt escapes. TeeReader retains at most
// the finite read ceiling plus the scanner's one-byte overflow probe, and a
// structural refusal stops reads without first draining the upstream body.
// The caller still owns deadline, cancellation and Close.
func ReadObject(r io.Reader, inherited int64) ([]byte, error) {
	if r == nil {
		return nil, ErrShape
	}
	var body bytes.Buffer
	summary, err := contentbounds.Scan(io.TeeReader(r, &body), Limits(inherited))
	if err != nil {
		return nil, err
	}
	if summary.Kind() != contentbounds.KindObject {
		return nil, ErrShape
	}
	return body.Bytes(), nil
}

func Object(raw []byte, inherited int64) error {
	summary, err := contentbounds.Scan(bytes.NewReader(raw), Limits(inherited))
	if err != nil {
		return err
	}
	if summary.Kind() != contentbounds.KindObject {
		return ErrShape
	}
	return nil
}

func AddItem(a *contentbounds.Accumulator, raw []byte, limits contentbounds.Limits) error {
	limits.MaxReadBytes = min(limits.MaxReadBytes, limits.MaxItemBytes)
	summary, err := contentbounds.Scan(bytes.NewReader(raw), limits)
	if err != nil {
		return err
	}
	if summary.Kind() != contentbounds.KindObject {
		return ErrShape
	}
	return a.Add(summary)
}

func CursorLimits(inherited int64) contentbounds.Limits {
	l := Limits(inherited)
	l.MaxReadBytes = min(l.MaxReadBytes, MaxCursorBytes)
	l.MaxItemBytes = min(l.MaxItemBytes, MaxCursorBytes)
	l.MaxTotalBytes = min(l.MaxTotalBytes, 64<<10)
	l.MaxItems = MaxPages
	return l
}
