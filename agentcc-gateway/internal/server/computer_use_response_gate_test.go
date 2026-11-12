package server

import (
	"bytes"
	"context"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
	"io"
	"testing"
)

type syntheticCloseReader struct {
	io.Reader
	closes int
}

func (r *syntheticCloseReader) Close() error { r.closes++; return nil }
func TestNativeStreamCloseIsIdempotent(t *testing.T) {
	upstream := &syntheticCloseReader{Reader: bytes.NewBufferString("truncated")}
	gate := &computerUseReader{ctx: context.Background(), upstream: upstream, schema: &syntheticEnvelope{}}
	closeStream := closeNativeStreamOnce(gate)
	if _, err := io.ReadAll(gate); err == nil {
		t.Fatal("truncation accepted")
	}
	closeStream()
	closeStream()
	if upstream.closes != 1 {
		t.Fatal("upstream closed more than once")
	}
}

type syntheticEnvelope struct {
	pending []byte
	corrupt bool
}

func (s *syntheticEnvelope) Feed(b []byte) ([]byte, error) {
	s.pending = append(s.pending, b...)
	if i := bytes.LastIndexByte(s.pending, '\n'); i >= 0 {
		out := bytes.Clone(s.pending[:i+1])
		s.pending = s.pending[i+1:]
		if s.corrupt {
			out[0] = '!'
		}
		return out, nil
	}
	return nil, nil
}
func (s *syntheticEnvelope) Finish() ([]byte, error) {
	if len(s.pending) > 0 {
		return nil, io.ErrUnexpectedEOF
	}
	return nil, nil
}
func TestComputerUseStreamFidelityAndTruncation(t *testing.T) {
	for _, size := range []int{1, 2, 7, 32} {
		body := []byte("synthetic-one\nsynthetic-two\n")
		gate := &computerUseReader{ctx: context.Background(), upstream: io.NopCloser(bytes.NewReader(body)), schema: &syntheticEnvelope{}}
		var out bytes.Buffer
		buf := make([]byte, size)
		for {
			n, err := gate.Read(buf)
			out.Write(buf[:n])
			if err == io.EOF {
				break
			}
			if err != nil {
				t.Fatal(err)
			}
		}
		if !bytes.Equal(out.Bytes(), body) {
			t.Fatal("native bytes changed")
		}
	}
	for _, test := range []struct {
		body    string
		corrupt bool
	}{{"truncated", false}, {"complete\n", true}} {
		gate := &computerUseReader{ctx: context.Background(), upstream: io.NopCloser(bytes.NewBufferString(test.body)), schema: &syntheticEnvelope{corrupt: test.corrupt}}
		out, err := io.ReadAll(gate)
		if err == nil || len(out) != 0 {
			t.Fatal("invalid envelope escaped")
		}
	}
}
func TestComputerUseMissingSchemaRefuses(t *testing.T) {
	r := toolview.Responses([]byte(`{"input":"synthetic"}`))
	required := models.ComputerUseRequirement{Required: true, Protocol: models.NativeResponses, Stream: true}
	if _, err := gateNativeStream(context.Background(), r, required, io.NopCloser(bytes.NewReader(nil))); err == nil {
		t.Fatal("nil schema opened")
	}
	if validateNativeResponse(context.Background(), r, required, []byte(`{}`)) == nil {
		t.Fatal("nil response schema opened")
	}
}
