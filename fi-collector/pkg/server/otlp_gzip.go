package server

import (
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// otlpContentCoding reports whether the body is gzip-compressed, and false
// for ok when it carries a coding we do not decode. Tokens are
// case-insensitive (RFC 9110 section 8.4.1); `identity` is a no-op and
// `x-gzip` is gzip. Exactly one gzip layer is supported, which is all OTLP
// exporters send.
func otlpContentCoding(h http.Header) (gzipped, ok bool) {
	layers := 0
	for _, v := range h.Values("Content-Encoding") {
		for _, tok := range strings.Split(v, ",") {
			switch strings.ToLower(strings.TrimSpace(tok)) {
			case "", "identity":
			case "gzip", "x-gzip":
				layers++
			default:
				return false, false
			}
		}
	}
	if layers > 1 {
		return false, false
	}
	return layers == 1, true
}

// errInflatedBodyTooLarge reports a body whose decompressed size passes the
// OTLP/HTTP body cap.
var errInflatedBodyTooLarge = errors.New("decompressed body over size cap")

// readChunk is the unit a body of unknown length is read in, so that reading
// it never needs a buffer that grows by copying.
const readChunk = 64 << 10

// gzipLeaseRatio: before a gzip body with a Content-Length is read, its wire
// lease leaves room for it to decompress to this many times its size (up to
// the cap). OTLP bodies decompress to a few times their size; one that
// decompresses to more waits for the rest of its lease once the first inflate
// pass has measured it.
const gzipLeaseRatio = 16

// readOTLPHTTPBody reads the request body with the cap applied twice, as the
// upstream Collector's confighttp applies max_request_body_size: to the body
// as sent (http.MaxBytesReader, a 413), and for gzip to the decompressed body
// too (errInflatedBodyTooLarge, a 413).
//
// Before it reads a byte it leases from the wire pool what reading can hold:
//   - with a Content-Length: that length (read into a buffer of exactly that
//     size), plus, for gzip, room to decompress to gzipLeaseRatio times it;
//   - without one: the cap, read in chunks of readChunk.
//
// Once the body is in, the lease is fitted to what comes next: joining a
// plain body's chunks into one buffer, or for gzip the decompressed size,
// which the first inflate pass counts without allocating it; the second pass
// inflates into a buffer of exactly that size, and then only the body is
// leased. When what comes next needs more than the lease holds, it waits for
// the rest (lease.grow), holding what it has.
//
// The returned lease covers the returned body; the caller releases it when
// the handler returns. The body must arrive within otlpReadTimeout of the
// lease being granted.
func (s *Server) readOTLPHTTPBody(w http.ResponseWriter, r *http.Request, gzipped bool) (body []byte, wire *lease, err error) {
	limit := s.limits.maxBody
	cl := r.ContentLength
	if cl > limit {
		return nil, nil, &http.MaxBytesError{Limit: limit}
	}
	wireNeed := limit + readChunk
	switch {
	case cl >= 0 && gzipped:
		wireNeed = gzipWireLease(cl, limit)
	case cl >= 0:
		wireNeed = cl
	}
	wire, err = s.wire.acquire(r.Context(), wireNeed)
	if err != nil {
		return nil, nil, err
	}
	// Best effort: an in-process ResponseWriter (tests) has no deadline.
	_ = http.NewResponseController(w).SetReadDeadline(time.Now().Add(s.readTimeout))

	in := http.MaxBytesReader(w, r.Body, limit)
	var sent [][]byte       // the body as sent, in one or more pieces
	var sentLen, held int64 // its length, and the bytes allocated for it
	if cl >= 0 {
		buf := make([]byte, cl)
		if _, err = io.ReadFull(in, buf); err != nil {
			wire.release()
			return nil, nil, err
		}
		sent, sentLen, held = [][]byte{buf}, cl, cl
	} else {
		if sent, sentLen, err = readChunks(in); err != nil {
			wire.release()
			return nil, nil, err
		}
		held = int64(len(sent)) * readChunk
	}
	if !gzipped {
		if len(sent) == 1 {
			wire.shrink(held)
			return sent[0], wire, nil
		}
		// Joining the chunks needs room for the copy while they are held.
		if err = fitLease(r.Context(), wire, held+sentLen); err != nil {
			wire.release()
			return nil, nil, err
		}
		body = make([]byte, 0, sentLen)
		for _, c := range sent {
			body = append(body, c...)
		}
		wire.shrink(sentLen)
		return body, wire, nil
	}
	body, err = inflateGzip(sent, limit, func(n int64) error {
		return fitLease(r.Context(), wire, held+readChunk+n) // readChunk: the inflater's state
	})
	if err != nil {
		wire.release()
		return nil, nil, err
	}
	wire.shrink(int64(len(body))) // the compressed body is no longer referenced
	return body, wire, nil
}

// gzipWireLease is what a gzip body with a Content-Length of cl leases before
// it is read: cl, room to decompress to gzipLeaseRatio times cl (up to the
// cap), and the inflater's state.
func gzipWireLease(cl, limit int64) int64 {
	return cl + min(limit, gzipLeaseRatio*cl) + readChunk
}

// fitLease makes l hold n bytes: it shrinks, or waits for the rest.
func fitLease(ctx context.Context, l *lease, n int64) error {
	if held := l.size(); n > held {
		return l.grow(ctx, n-held)
	}
	l.shrink(n)
	return nil
}

// readChunks reads r to the end in readChunk-sized pieces.
func readChunks(r io.Reader) ([][]byte, int64, error) {
	var chunks [][]byte
	var total int64
	for {
		c := make([]byte, readChunk)
		n, err := io.ReadFull(r, c)
		if n > 0 {
			chunks = append(chunks, c[:n])
			total += int64(n)
		}
		switch {
		case err == io.EOF || err == io.ErrUnexpectedEOF:
			return chunks, total, nil
		case err != nil:
			return nil, 0, err
		}
	}
}

func multiReader(pieces [][]byte) io.Reader {
	rs := make([]io.Reader, len(pieces))
	for i, p := range pieces {
		rs[i] = bytes.NewReader(p)
	}
	return io.MultiReader(rs...)
}

// inflateGzip decompresses a gzip body that is already in memory into a
// buffer of exactly its decompressed size.
//
// The first pass only counts the output, stopping at limit+1 bytes; it
// allocates no decompressed buffer. A corrupt stream is a 400 and an over-cap
// one a 413. For a valid body, sized is told the decompressed size before the
// second pass inflates into a buffer of exactly that size; an error from it
// is returned as it is. The price is decompressing an accepted body twice.
func inflateGzip(wire [][]byte, limit int64, sized func(n int64) error) ([]byte, error) {
	zr, err := gzip.NewReader(multiReader(wire))
	if err != nil {
		return nil, gzipBodyError(err)
	}
	n, err := io.Copy(io.Discard, io.LimitReader(zr, limit+1))
	if err == nil {
		err = zr.Close()
	}
	switch {
	case err != nil:
		return nil, gzipBodyError(err)
	case n > limit:
		return nil, errInflatedBodyTooLarge
	}
	if err := sized(n); err != nil {
		return nil, err
	}
	if n == 0 {
		return []byte{}, nil
	}
	body := make([]byte, n)
	if err := gunzipExactly(zr, wire, body); err != nil {
		return nil, gzipBodyError(err)
	}
	return body, nil
}

// gunzipExactly inflates wire into body, which must be exactly its
// decompressed size. The first pass already checked that size and the
// checksums, so an error here means the two passes disagree.
func gunzipExactly(zr *gzip.Reader, wire [][]byte, body []byte) error {
	if err := zr.Reset(multiReader(wire)); err != nil {
		return err
	}
	if _, err := io.ReadFull(zr, body); err != nil {
		return err
	}
	var extra [1]byte
	if m, err := zr.Read(extra[:]); m != 0 || err != io.EOF {
		return errors.New("decompressed size changed between passes")
	}
	return zr.Close()
}

// gzipBodyError labels a corrupt or truncated gzip stream (a 400).
func gzipBodyError(err error) error {
	if errors.Is(err, io.EOF) {
		err = io.ErrUnexpectedEOF // an empty body is a truncated gzip stream
	}
	return fmt.Errorf("decode gzip: %w", err)
}
