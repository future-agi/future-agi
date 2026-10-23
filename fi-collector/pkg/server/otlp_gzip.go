package server

import (
	"bytes"
	"compress/gzip"
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

// readOTLPHTTPBody reads the request body with the cap applied twice, as the
// upstream Collector's confighttp applies max_request_body_size: to the body
// as sent (http.MaxBytesReader, a 413), and for gzip to the decompressed body
// too (errInflatedBodyTooLarge, a 413).
//
// Before it reads a byte it leases what reading can hold from the wire pool:
//   - a plain body with a Content-Length: that length (read into a buffer of
//     exactly that size);
//   - a plain body without one: twice the cap (read in chunks, then joined);
//   - gzip: the compressed body as above, plus the cap for the decompressed
//     body. The first inflate pass only counts the output; the lease then
//     shrinks to the compressed body plus the decompressed size, the second
//     pass inflates into a buffer of exactly that size, and the compressed
//     part is given back.
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
	wireNeed := 2 * limit
	if cl >= 0 {
		wireNeed = cl
	}
	if gzipped {
		wireNeed = limit + limit
		if cl >= 0 {
			wireNeed = cl + limit
		}
	}
	wire, err = s.wire.acquire(r.Context(), wireNeed)
	if err != nil {
		return nil, nil, err
	}
	// Best effort: an in-process ResponseWriter (tests) has no deadline.
	_ = http.NewResponseController(w).SetReadDeadline(time.Now().Add(s.readTimeout))

	in := http.MaxBytesReader(w, r.Body, limit)
	var sent [][]byte // the body as sent, in one or more pieces
	var sentLen int64
	if cl >= 0 {
		buf := make([]byte, cl)
		if _, err = io.ReadFull(in, buf); err != nil {
			wire.release()
			return nil, nil, err
		}
		sent, sentLen = [][]byte{buf}, cl
	} else {
		if sent, sentLen, err = readChunks(in); err != nil {
			wire.release()
			return nil, nil, err
		}
	}
	if !gzipped {
		if len(sent) == 1 {
			body = sent[0]
		} else {
			body = make([]byte, 0, sentLen)
			for _, c := range sent {
				body = append(body, c...)
			}
		}
		wire.shrink(sentLen)
		return body, wire, nil
	}
	body, err = inflateGzip(sent, limit, func(n int64) { wire.shrink(sentLen + readChunk + n) })
	if err != nil {
		wire.release()
		return nil, nil, err
	}
	wire.shrink(int64(len(body))) // the compressed body is no longer referenced
	return body, wire, nil
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
// second pass inflates into a buffer of exactly that size. The price is
// decompressing an accepted body twice.
func inflateGzip(wire [][]byte, limit int64, sized func(n int64)) ([]byte, error) {
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
	case n == 0:
		sized(0)
		return []byte{}, nil
	}

	sized(n)
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
