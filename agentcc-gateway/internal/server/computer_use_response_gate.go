package server

import (
	"bytes"
	"context"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
	"io"
	"sync"
)

func closeNativeStreamOnce(stream io.ReadCloser) func() {
	var once sync.Once
	return func() { once.Do(func() { stream.Close() }) }
}

func validateNativeResponse(ctx context.Context, view models.NativeBody, required models.ComputerUseRequirement, body []byte) *models.APIError {
	if !required.Required {
		return nil
	}
	v, ok := view.(*toolview.NativeRequest)
	if !ok || v.ValidateResponse(ctx, body, required) != nil {
		return computerUseUnavailable()
	}
	return nil
}

// prepareNativeStream must run before the dispatch claim or upstream call.
func prepareNativeStream(ctx context.Context, view models.NativeBody, required models.ComputerUseRequirement) (*computerUseReader, *models.APIError) {
	if !required.Required {
		return nil, nil
	}
	v, ok := view.(*toolview.NativeRequest)
	if !ok {
		return nil, computerUseUnavailable()
	}
	schema, err := v.NewComputerUseStream(ctx, required)
	if err != nil {
		return nil, computerUseUnavailable()
	}
	return &computerUseReader{ctx: ctx, schema: schema}, nil
}
func (r *computerUseReader) wrap(ctx context.Context, upstream io.ReadCloser) io.ReadCloser {
	if r == nil || upstream == nil {
		return upstream
	}
	r.ctx, r.upstream = ctx, upstream
	return r
}
func gateNativeStream(ctx context.Context, view models.NativeBody, required models.ComputerUseRequirement, upstream io.ReadCloser) (io.ReadCloser, *models.APIError) {
	gate, err := prepareNativeStream(ctx, view, required)
	if err != nil {
		if upstream != nil {
			upstream.Close()
		}
		return nil, err
	}
	return gate.wrap(ctx, upstream), nil
}

// The installed schema owns envelope framing and its accepted memory bound.
// This adapter checks original-byte fidelity and never forwards a partial or
// rejected envelope. No production schema/size/framing default is supplied.
type computerUseReader struct {
	ctx            context.Context
	upstream       io.ReadCloser
	schema         toolview.ComputerUseStream
	pending, ready []byte
	done           bool
	failure        error
	closed         bool
	closeError     error
}

func (r *computerUseReader) accept(out []byte) bool {
	if len(out) > len(r.pending) || !bytes.Equal(out, r.pending[:len(out)]) {
		return false
	}
	r.ready = append(r.ready, out...)
	r.pending = r.pending[len(out):]
	return true
}
func (r *computerUseReader) fail() (int, error) {
	r.pending = nil
	r.ready = nil
	r.done = true
	r.failure = computerUseUnavailable()
	r.Close()
	return 0, r.failure
}
func (r *computerUseReader) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	for len(r.ready) == 0 && !r.done {
		if r.ctx.Err() != nil {
			return r.fail()
		}
		var buf [4096]byte // transport read size, never a schema/envelope limit
		n, readErr := r.upstream.Read(buf[:])
		if n > 0 {
			r.pending = append(r.pending, buf[:n]...)
			out, err := r.schema.Feed(bytes.Clone(buf[:n]))
			if err != nil || !r.accept(out) {
				return r.fail()
			}
		}
		if readErr != nil {
			if readErr != io.EOF {
				return r.fail()
			}
			out, err := r.schema.Finish()
			if err != nil || !r.accept(out) || len(r.pending) != 0 {
				return r.fail()
			}
			r.done = true
		}
	}
	if len(r.ready) > 0 {
		n := copy(p, r.ready)
		r.ready = r.ready[n:]
		return n, nil
	}
	if r.failure != nil {
		return 0, r.failure
	}
	return 0, io.EOF
}
func (r *computerUseReader) Close() error {
	if r.closed {
		return r.closeError
	}
	r.closed = true
	r.done = true
	r.pending = nil
	r.ready = nil
	if r.upstream != nil {
		r.closeError = r.upstream.Close()
	}
	return r.closeError
}
