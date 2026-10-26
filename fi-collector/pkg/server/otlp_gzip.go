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
// 16 MiB OTLP/HTTP cap.
var errInflatedBodyTooLarge = errors.New("decompressed body over size cap")

// readOTLPHTTPBody reads the request body with limit applied twice, as the
// upstream Collector's confighttp applies max_request_body_size: to the body
// as sent (http.MaxBytesReader, a 413), and for gzip to the decompressed body
// too (errInflatedBodyTooLarge, a 413).
//
// A gzip body is read off the wire in full before it touches the inflate
// budget, so a slow or stalled upload holds a connection and its compressed
// bytes, as a plain body does, but no lease. The lease is held from inflation
// until the handler returns; the caller calls release.
func readOTLPHTTPBody(w http.ResponseWriter, r *http.Request, gzipped bool, limit int64, budget *inflateBudget) (body []byte, release func(), err error) {
	wire, err := io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
	if err != nil || !gzipped {
		return wire, noRelease, err
	}
	return inflateGzip(r.Context(), wire, limit, budget)
}

func noRelease() {}

// inflateGzip decompresses a gzip body that is already in memory into a
// buffer of exactly its decompressed size, leased from budget.
//
// It reserves limit bytes before either inflate pass, but only after the
// complete wire body has arrived. The first pass only counts the output,
// stopping at limit+1 bytes; it allocates no decompressed buffer. A corrupt
// stream is a 400 and an over-cap one a 413, and either error releases the
// reservation. For a valid body, the lease shrinks to the exact decompressed
// size, and the second pass inflates into a buffer of exactly that size. Thus
// all decompression is under a lease, while the wire upload is not. The price
// is decompressing an accepted body twice.
//
// The lease counts the decompressed body only. Decoding it into a request and
// rows allocates more, for gzip and plain bodies alike.
func inflateGzip(ctx context.Context, wire []byte, limit int64, budget *inflateBudget) ([]byte, func(), error) {
	lease, err := budget.reserve(ctx, limit)
	if err != nil {
		return nil, noRelease, errInflateBudgetBusy
	}
	zr, err := gzip.NewReader(bytes.NewReader(wire))
	if err != nil {
		lease.release()
		return nil, noRelease, gzipBodyError(err)
	}
	n, err := io.Copy(io.Discard, io.LimitReader(zr, limit+1))
	if err == nil {
		err = zr.Close()
	}
	switch {
	case err != nil:
		lease.release()
		return nil, noRelease, gzipBodyError(err)
	case n > limit:
		lease.release()
		return nil, noRelease, errInflatedBodyTooLarge
	case n == 0:
		lease.release()
		return []byte{}, noRelease, nil
	}

	lease.shrink(n)
	body := make([]byte, n)
	if err := gunzipExactly(zr, wire, body); err != nil {
		lease.release()
		return nil, noRelease, gzipBodyError(err)
	}
	return body, lease.release, nil
}

// gunzipExactly inflates wire into body, which must be exactly its
// decompressed size. The first pass already checked that size and the
// checksums, so an error here means the two passes disagree.
func gunzipExactly(zr *gzip.Reader, wire, body []byte) error {
	if err := zr.Reset(bytes.NewReader(wire)); err != nil {
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
