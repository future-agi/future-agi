package media

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

// Sink must consume through EOF and commit only on a successful read. In batch 3
// bind it to artifacts.Store.Put with the desired retention metadata.
type Sink func(context.Context, string, io.Reader) error
type Fetcher struct {
	client      *http.Client
	sink        Sink
	DeniedHosts []string
}
type Result struct {
	Data                              []byte
	Digest, ContentType, DecodedCheck string
	Bytes                             int64
	Width, Height                     int
	DurationSeconds                   float64
}

func NewFetcher(sink Sink) *Fetcher {
	return &Fetcher{client: NewHTTPClient(5*time.Second, 20*time.Second), sink: sink}
}

// NewHTTPClient never uses environment proxies: all actual connections are
// checked after DNS by netguard, including redirect hops and new connections.
func NewHTTPClient(connect, read time.Duration) *http.Client {
	if connect <= 0 {
		connect = 5 * time.Second
	}
	if read <= 0 {
		read = 20 * time.Second
	}
	return &http.Client{Transport: &http.Transport{DialContext: netguard.DialContext(net.Dialer{Timeout: connect}, false), TLSHandshakeTimeout: connect, ResponseHeaderTimeout: read, DisableCompression: true, IdleConnTimeout: 90 * time.Second}, CheckRedirect: func(r *http.Request, via []*http.Request) error {
		if len(via) > 3 {
			return failure(400, "media_url_refused")
		}
		r.Header.Del("Authorization")
		r.Header.Del("Cookie")
		return ValidateURL(r.URL.String())
	}}
}
func ValidateURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Fragment != "" {
		return failure(400, "media_url_refused")
	}
	for k := range u.Query() {
		switch strings.ToLower(k) {
		case "api_key", "apikey", "api-key", "authorization", "access_token", "password", "credential":
			return failure(400, "media_url_refused")
		}
	}
	return nil
}
func (f *Fetcher) Fetch(ctx context.Context, raw, declared, key string, l Limits) (Result, error) {
	if err := ValidateURL(raw); err != nil {
		return Result{}, err
	}
	u, _ := url.Parse(raw)
	for _, host := range f.DeniedHosts {
		if strings.EqualFold(host, u.Hostname()) {
			return Result{}, failure(400, "media_url_refused")
		}
	}
	if l.Deadline <= 0 {
		l.Deadline = 20 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, l.Deadline)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, raw, nil)
	client := *f.client
	redirect := client.CheckRedirect
	client.CheckRedirect = func(r *http.Request, via []*http.Request) error {
		for _, host := range f.DeniedHosts {
			if strings.EqualFold(host, r.URL.Hostname()) {
				return failure(400, "media_url_refused")
			}
		}
		return redirect(r, via)
	}
	// A missing cap must fail before the user URL is dialled. Verify also
	// rejects it, but only after the connection is open.
	if l.MaxBytes <= 0 {
		return Result{}, failure(400, "media_limit_required")
	}
	resp, err := client.Do(req)
	if err != nil {
		return Result{}, fetchError(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Result{}, failure(400, "media_fetch_failed")
	}
	if l.MaxBytes > 0 && resp.ContentLength > l.MaxBytes {
		return Result{}, failure(413, "media_too_large")
	}
	return f.Verify(ctx, resp.Body, declared, key, l)
}
func fetchError(err error) error {
	var e *Error
	if errors.As(err, &e) {
		return e
	}
	var blocked *netguard.BlockedError
	if errors.As(err, &blocked) {
		return &Error{Status: 400, Code: "media_url_refused", cause: blocked}
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return &Error{Status: 408, Code: "media_fetch_timeout", cause: context.DeadlineExceeded}
	}
	if errors.Is(err, context.Canceled) {
		return context.Canceled
	}
	return failure(400, "media_fetch_failed")
}

// Verify also handles inline media. The caller must close a source reader to
// interrupt a blocked non-HTTP read; HTTP reads use the request context.
func (f *Fetcher) Verify(ctx context.Context, r io.Reader, declared, key string, l Limits) (Result, error) {
	if l.MaxBytes <= 0 {
		return Result{}, failure(400, "media_limit_required")
	}
	if l.Deadline <= 0 {
		l.Deadline = 20 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, l.Deadline)
	defer cancel()
	h := sha256.New()
	var buf bytes.Buffer
	var dst io.Writer = &buf
	var pipe *io.PipeWriter
	var done chan error
	if f.sink != nil {
		if key == "" {
			return Result{}, failure(400, "media_key_required")
		}
		pr, pw := io.Pipe()
		pipe = pw
		dst = pw
		done = make(chan error, 1)
		go func() { err := f.sink(ctx, key, pr); pr.CloseWithError(err); done <- err }()
	}
	counted := &countWriter{w: io.MultiWriter(dst, h)}
	result, err := inspect(io.TeeReader(&cappedReader{ctx: ctx, r: r, left: l.MaxBytes}, counted), declared, l)
	if pipe != nil {
		pipe.CloseWithError(err)
		sinkErr := <-done
		if err == nil {
			err = sinkErr
		}
	}
	if err != nil {
		return Result{}, fetchError(err)
	}
	result.Bytes = counted.n
	result.Digest = hex.EncodeToString(h.Sum(nil))
	if f.sink == nil {
		result.Data = buf.Bytes()
	}
	return result, nil
}

type countWriter struct {
	w io.Writer
	n int64
}

func (w *countWriter) Write(p []byte) (int, error) {
	n, e := w.w.Write(p)
	w.n += int64(n)
	return n, e
}
