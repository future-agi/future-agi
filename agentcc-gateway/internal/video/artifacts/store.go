// Package artifacts stores verified video blobs. Keys are immutable identities
// owned by the lifecycle; authorization remains the gateway handler's job.
package artifacts

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"hash"
	"io"
	"strconv"
	"strings"
	"time"
)

var (
	ErrKey          = errors.New("artifact key outside owned namespace")
	ErrTooLarge     = errors.New("artifact too large")
	ErrNotFound     = errors.New("artifact not found")
	ErrRange        = errors.New("artifact range not satisfiable")
	ErrSizeMismatch = errors.New("artifact size mismatch")
)

type Meta struct {
	ContentType string
	Bytes       int64
	Digest      string
	ExpiresAt   time.Time
}
type Object struct {
	Body         io.ReadCloser
	Meta         Meta
	Length       int64
	ContentRange string
}
type Store interface {
	Put(context.Context, string, io.Reader, Meta) (Meta, error)
	Open(context.Context, string, string) (Object, error)
	Delete(context.Context, string) error
	Stat(context.Context, string) (Meta, error)
}

func validKey(key string) bool {
	if key == "" || len(key) > 1024 {
		return false
	}
	for _, p := range strings.Split(key, "/") {
		if p == "" || p == "." || p == ".." {
			return false
		}
		for _, c := range p {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-' || c == '.') {
				return false
			}
		}
	}
	return true
}

type bounded struct {
	ctx     context.Context
	r       io.Reader
	left, n int64
	hash    hash.Hash
}

func newBounded(ctx context.Context, r io.Reader, max int64) *bounded {
	return &bounded{ctx: ctx, r: r, left: max, hash: sha256.New()}
}
func (r *bounded) Read(p []byte) (int, error) {
	if e := r.ctx.Err(); e != nil {
		return 0, e
	}
	if len(p) == 0 {
		return 0, nil
	}
	if r.left == 0 {
		var b [1]byte
		n, e := r.r.Read(b[:])
		if n > 0 {
			return 0, ErrTooLarge
		}
		return 0, e
	}
	if int64(len(p)) > r.left {
		p = p[:r.left]
	}
	n, e := r.r.Read(p)
	r.left -= int64(n)
	r.n += int64(n)
	r.hash.Write(p[:n])
	return n, e
}
func (r *bounded) metadata(m Meta) (Meta, error) {
	if m.Bytes > 0 && m.Bytes != r.n {
		return Meta{}, ErrSizeMismatch
	}
	m.Bytes = r.n
	m.Digest = hex.EncodeToString(r.hash.Sum(nil))
	return m, nil
}
func parseRange(h string, size int64) (start, length int64, cr string, err error) {
	if h == "" {
		return 0, size, "", nil
	}
	if size <= 0 || !strings.HasPrefix(h, "bytes=") || strings.Contains(h, ",") {
		return 0, 0, "", ErrRange
	}
	a, b, ok := strings.Cut(h[6:], "-")
	if !ok {
		return 0, 0, "", ErrRange
	}
	number := func(s string) (int64, error) {
		if s == "" {
			return 0, ErrRange
		}
		for _, c := range s {
			if c < '0' || c > '9' {
				return 0, ErrRange
			}
		}
		return strconv.ParseInt(s, 10, 64)
	}
	end := size - 1
	if a == "" {
		n, e := number(b)
		if e != nil || n <= 0 {
			return 0, 0, "", ErrRange
		}
		if n > size {
			n = size
		}
		start = size - n
	} else {
		start, err = number(a)
		if err != nil || start >= size {
			return 0, 0, "", ErrRange
		}
		if b != "" {
			end, err = number(b)
			if err != nil || end < start {
				return 0, 0, "", ErrRange
			}
			if end >= size {
				end = size - 1
			}
		}
	}
	return start, end - start + 1, fmt.Sprintf("bytes %d-%d/%d", start, end, size), nil
}

type sectionCloser struct {
	io.Reader
	io.Closer
}
