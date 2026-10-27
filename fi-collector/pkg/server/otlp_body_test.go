package server

import (
	"bytes"
	"compress/gzip"
	"io"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"testing"
)

// The 16 MiB cap applies to the body as sent as well as after decompression,
// as upstream confighttp applies max_request_body_size to both. Without the
// wire cap, a gzip stream of empty deflate blocks (5 bytes each, no output)
// could be read for as long as the sender kept sending.

// emptyBlockGzip is a valid gzip stream of n bytes or more that inflates to
// nothing: a header, empty non-final stored blocks, an empty final block and a
// trailer. It is produced as it is read and counts the bytes read.
type emptyBlockGzip struct {
	n, read int64
	pending []byte
	done    bool
}

func (g *emptyBlockGzip) Read(p []byte) (int, error) {
	if len(g.pending) == 0 {
		switch {
		case g.done:
			return 0, io.EOF
		case g.read == 0:
			g.pending = []byte{0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 0xff} // gzip header, deflate
		case g.read < g.n:
			g.pending = bytes.Repeat([]byte{0, 0, 0, 0xff, 0xff}, 4096) // empty stored blocks
		default:
			// The final empty block, then CRC32 and ISIZE of an empty body.
			g.pending = []byte{1, 0, 0, 0xff, 0xff, 0, 0, 0, 0, 0, 0, 0, 0}
			g.done = true
		}
	}
	m := copy(p, g.pending)
	g.pending = g.pending[m:]
	g.read += int64(m)
	return m, nil
}

func TestOTLPHTTPGzipOverCapAsSentIs413(t *testing.T) {
	const limit = maxOTLPHTTPBodyBytes

	t.Run("endless empty deflate blocks", func(t *testing.T) {
		wire := &emptyBlockGzip{n: 64 << 20}
		req := httptest.NewRequest(http.MethodPost, "/v1/traces", wire)
		req.Header.Set("Content-Type", "application/x-protobuf")
		req.Header.Set("Content-Encoding", "gzip")
		rec := httptest.NewRecorder()
		directServer().handleHTTPTraces(rec, req)
		if rec.Code != http.StatusRequestEntityTooLarge {
			t.Fatalf("status %d, want 413 (%s)", rec.Code, rec.Body.String())
		}
		if wire.read > limit+1 {
			t.Errorf("read %d bytes off the wire, want the handler to stop at the %d byte cap", wire.read, limit)
		}
	})

	t.Run("64 MiB of empty gzip members", func(t *testing.T) {
		body := bytes.Repeat(gzipBytes(t, nil), (64<<20)/len(gzipBytes(t, nil)))
		if rec := postBody(directServer(), "application/x-protobuf", true, nil, body); rec.Code != http.StatusRequestEntityTooLarge {
			t.Fatalf("status %d, want 413 (%s)", rec.Code, rec.Body.String())
		}
	})

	// Incompressible data grows a little when gzipped, so a body just under
	// the cap is just over it as sent: refused, as upstream would.
	t.Run("over the cap as sent, under it inflated", func(t *testing.T) {
		raw := make([]byte, limit-1024)
		if _, err := rand.New(rand.NewSource(42)).Read(raw); err != nil {
			t.Fatal(err)
		}
		var wire bytes.Buffer
		zw := gzip.NewWriter(&wire)
		if _, err := zw.Write(raw); err != nil {
			t.Fatal(err)
		}
		if err := zw.Close(); err != nil {
			t.Fatal(err)
		}
		if wire.Len() <= limit {
			t.Fatalf("test setup: compressed body is %d bytes, want more than %d", wire.Len(), limit)
		}
		s := directServer()
		if rec := postBody(s, "application/x-protobuf", true, nil, wire.Bytes()); rec.Code != http.StatusRequestEntityTooLarge {
			t.Fatalf("status %d, want 413 (%s)", rec.Code, rec.Body.String())
		}
		if p := s.inflate.peak.Load(); p != 0 {
			t.Errorf("leased %d budget bytes for a body refused as sent, want none", p)
		}
	})
}
