package server

import (
	"bytes"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// Tests for gzip inflation (otlp_gzip.go). A gzip body leases its compressed
// size plus room to decompress (gzipWireLease) before it is read, grows the
// lease if the count pass finds a larger body, then shrinks to exactly its
// decompressed size and inflates into a buffer of that size.

// acceptingServer enqueues accepted rows and never flushes them.
func acceptingServer() *Server {
	return New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour}, nil, nil, nil, nil)
}

// A gzip request with a Content-Length leases gzipWireLease before the count
// pass; a body that decompresses to more grows the lease to what the second
// pass holds (the compressed body, the inflater, the decompressed body). It
// then shrinks to exactly its decompressed size, which is also the buffer's
// size (capacity included). The lease stays until the handler returns.
func TestReadGzipBodyLeasesExactlyTheDecompressedSize(t *testing.T) {
	cases := map[string][]byte{
		"random body":         bytes.Repeat([]byte("0123456789abcdef"), 1<<14),
		"highly compressible": bytes.Repeat([]byte("otlp "), 3<<20/5),
		"empty body":          nil,
		"tiny body":           []byte("tiny"),
		"at the cap":          make([]byte, defaultMaxBodyBytes),
	}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) {
			s := acceptingServer()
			wire := gzipBytes(t, raw)
			req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(wire))
			out, l, err := s.readOTLPHTTPBody(httptest.NewRecorder(), req, true)
			if err != nil || !bytes.Equal(out, raw) {
				t.Fatalf("read: err=%v, %d bytes out, want %d", err, len(out), len(raw))
			}
			if c := cap(out); c != len(raw) {
				t.Errorf("buffer capacity %d for a %d byte body; want exactly the body size", c, len(raw))
			}
			cl := int64(len(wire))
			if p, want := s.wire.peak.Load(), max(gzipWireLease(cl, s.limits.maxBody), cl+readChunk+int64(len(raw))); p != want {
				t.Errorf("leased %d bytes at most, want %d", p, want)
			}
			if held := s.wire.inUse.Load(); held != int64(len(raw)) {
				t.Errorf("leased %d bytes after inflate, want exact decompressed size %d", held, len(raw))
			}
			l.release()
			l.release() // a second call is a no-op
			if u := s.wire.inUse.Load(); u != 0 {
				t.Errorf("%d bytes still leased after release", u)
			}
		})
	}
}

// A valid gzip body may contain multiple members. Inflate all members and hold
// the full decompressed size, regardless of the last member's trailer size.
func TestInflateGzipReadsAllMembers(t *testing.T) {
	raw := bytes.Repeat([]byte("span "), 1<<20/5)
	wire := append(gzipBytes(t, raw), gzipBytes(t, []byte("x"))...)
	var sized int64 = -1
	out, err := inflateGzip([][]byte{wire[:100], wire[100:]}, defaultMaxBodyBytes, func(n int64) error { sized = n; return nil })
	if err != nil || !bytes.Equal(out, append(raw, 'x')) {
		t.Fatalf("inflate: err=%v, %d bytes out, want %d", err, len(out), len(raw)+1)
	}
	if sized != int64(len(out)) {
		t.Errorf("sized %d for a %d byte body", sized, len(out))
	}
}

// A gzip bomb is stopped as soon as it expands past the cap. It holds only
// its lease from before the count pass (gzipWireLease), then releases it;
// this checks the decompressed stream limit, not the gzip trailer or wire
// size.
func TestOTLPHTTPGzipBombIs413WithinDecompressedCap(t *testing.T) {
	zeros := gzipBytes(t, make([]byte, 64<<20))
	cases := map[string][]byte{
		"64 MiB member":                         zeros,
		"64 MiB followed by 1-byte last member": append(append([]byte(nil), zeros...), gzipBytes(t, []byte{0})...),
		"256 members of 1 MiB of zeros":         zeroGzipMembers(t, 256),
		"16 MiB + 1 byte":                       gzipBytes(t, make([]byte, defaultMaxBodyBytes+1)),
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			s := directServer()
			rec := postBody(s, "application/x-protobuf", true, nil, body)
			if rec.Code != http.StatusRequestEntityTooLarge {
				t.Fatalf("status %d, want 413 (%s)", rec.Code, rec.Body.String())
			}
			if p, want := s.wire.peak.Load(), gzipWireLease(int64(len(body)), s.limits.maxBody); p != want {
				t.Errorf("over-cap body leased %d bytes during the count pass, want %d", p, want)
			}
			if u := s.wire.inUse.Load(); u != 0 {
				t.Errorf("%d wire bytes still leased after the request returned", u)
			}
		})
	}
}

// Floods of rejected requests do not write one log line each: the lines are
// sampled to at most about one per second per reason (over the cap, and
// pool busy).
func TestOTLPHTTPRejectionLogsAreSampled(t *testing.T) {
	var logs bytes.Buffer
	var mu sync.Mutex
	s := directServer()
	s.log = slog.New(slog.NewTextHandler(writerFunc(func(p []byte) (int, error) {
		mu.Lock()
		defer mu.Unlock()
		return logs.Write(p)
	}), nil))
	s.wire.wait = time.Millisecond
	release := holdPool(t, s.wire, s.wire.size) // in-flight requests holding the whole pool
	busy := gzipBytes(t, protoBody(t, makeTraces("busy", "31313131-3131-4131-8131-313131313131")))
	overCap := make([]byte, defaultMaxBodyBytes+1)

	start := time.Now()
	for i := 0; i < 100; i++ {
		if rec := postBody(s, "application/x-protobuf", true, nil, busy); rec.Code != http.StatusServiceUnavailable {
			t.Fatalf("status %d, want 503", rec.Code)
		}
	}
	release()
	for i := 0; i < 20; i++ {
		if rec := postBody(s, "application/x-protobuf", false, nil, overCap); rec.Code != http.StatusRequestEntityTooLarge {
			t.Fatalf("status %d, want 413", rec.Code)
		}
	}
	elapsed := time.Since(start)
	mu.Lock()
	defer mu.Unlock()
	perReason := 1 + int(elapsed/time.Second)
	for _, reason := range []string{"reason=wire_busy", "reason=body_size"} {
		if n := strings.Count(logs.String(), reason); n == 0 || n > perReason {
			t.Errorf("%d %q lines for a burst of rejections in %v, want about one per second:\n%s", n, reason, elapsed, logs.String())
		}
	}
	st := s.IngestStats()
	if st.Rejected["http/wire_busy/503"] != 100 || st.Rejected["http/body_size/413"] != 20 {
		t.Errorf("rejection counts %v, want 100 wire_busy and 20 body_size", st.Rejected)
	}
}

// A gzip body that decompresses to more than its lease allowed for waits for
// the rest while it holds its compressed body, and is served once the pool
// has room.
func TestGzipBodyWaitsToGrowItsLease(t *testing.T) {
	s := budgetServer(96)
	raw := tracesOfSize(t, "grow", 4<<20) // compresses far more than gzipLeaseRatio times
	body := gzipBytes(t, raw)
	before := gzipWireLease(int64(len(body)), s.limits.maxBody)
	release := holdPool(t, s.wire, s.wire.size-before) // room for the first lease only
	time.AfterFunc(200*time.Millisecond, release)
	start := time.Now()
	if rec := postGzipTo(s, body); rec.Code != http.StatusOK {
		t.Fatalf("status %d, want 200 once the pool has room (%s)", rec.Code, rec.Body.String())
	}
	if waited := time.Since(start); waited < 150*time.Millisecond {
		t.Errorf("served after %v, before the pool had room to grow the lease", waited)
	}
	if p, want := s.wire.peak.Load(), s.wire.size; p > want {
		t.Errorf("wire peak %d over the pool's %d", p, want)
	}
}
