package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"sync"
	"time"
)

type dialFunc func(ctx context.Context, network, addr string) (net.Conn, error)

// maxBody caps a captured response body. Contract fixtures are a few KiB.
const maxBody = 32 << 20

// exchange is one request the adapter sent and what upstream answered.
type exchange struct {
	Method      string
	Path        string
	Status      int
	ContentType string
	// Body is the complete upstream response body, byte for byte (after
	// transport-level gzip decoding), even if the adapter stopped reading.
	Body []byte
	// Err is set when the exchange did not complete: upstream unreachable,
	// body cut off, or the adapter called an unexpected host.
	Err error
}

// teeProxy stands between an adapter and its real upstream. The adapter's
// transport dials it in memory (net.Pipe; no port is opened) and speaks plain
// HTTP/1.1 to it; the proxy re-sends each request over https to the same host
// and path with the same headers, streams the answer back, and keeps a copy of
// the body. Request headers, credentials included, are forwarded and never
// stored.
type teeProxy struct {
	scheme string // upstream scheme, normally https
	host   string // the only Host the proxy forwards to
	client *http.Client
	// timeout bounds one upstream exchange, independent of the adapter: the
	// proxy reads the body to the end even after the adapter hangs up.
	timeout time.Duration

	ln  *pipeListener
	srv *http.Server

	mu        sync.Mutex
	idle      *sync.Cond // signalled when inflight drops
	inflight  int
	patch     []byte
	exchanges []exchange
}

func newTeeProxy(target *url.URL, client *http.Client, timeout time.Duration) *teeProxy {
	if client == nil {
		tr := http.DefaultTransport.(*http.Transport).Clone()
		client = &http.Client{Transport: tr}
	}
	// A redirect is answered to the adapter as-is (and fails the recording as
	// a non-2xx) rather than followed with the credential attached.
	c := *client
	c.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }

	p := &teeProxy{
		scheme:  target.Scheme,
		host:    target.Host,
		client:  &c,
		timeout: timeout,
		ln:      newPipeListener(),
	}
	p.idle = sync.NewCond(&p.mu)
	p.srv = &http.Server{
		Handler:           p,
		ReadHeaderTimeout: 30 * time.Second,
		ErrorLog:          log.New(io.Discard, "", 0),
	}
	go func() { _ = p.srv.Serve(p.ln) }()
	return p
}

// Dial is the adapter's DialContext: every address leads to the proxy.
func (p *teeProxy) Dial(ctx context.Context, _, _ string) (net.Conn, error) {
	return p.ln.dial(ctx)
}

// setPatch sets the JSON merge patch (RFC 7396) applied to request bodies
// from now on; nil sends bodies unchanged.
func (p *teeProxy) setPatch(patch []byte) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.patch = patch
}

// take waits until no exchange is in flight, then returns and forgets the
// exchanges seen so far.
func (p *teeProxy) take() []exchange {
	p.mu.Lock()
	defer p.mu.Unlock()
	for p.inflight > 0 {
		p.idle.Wait()
	}
	ex := p.exchanges
	p.exchanges = nil
	return ex
}

func (p *teeProxy) Close() error { return p.srv.Close() }

func (p *teeProxy) begin() []byte {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.inflight++
	return p.patch
}

func (p *teeProxy) end(ex exchange) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.exchanges = append(p.exchanges, ex)
	p.inflight--
	p.idle.Broadcast()
}

func (p *teeProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	patch := p.begin()
	ex := exchange{Method: r.Method, Path: r.URL.Path}
	defer func() { p.end(ex) }()

	fail := func(err error) {
		ex.Err = err
		http.Error(w, "contract-recorder: "+err.Error(), http.StatusBadGateway)
	}
	if r.Host != p.host {
		fail(fmt.Errorf("adapter called host %q, want %q", r.Host, p.host))
		return
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		fail(fmt.Errorf("reading the adapter's request: %v", err))
		return
	}
	if patch != nil {
		if body, err = applyMergePatch(body, patch); err != nil {
			fail(fmt.Errorf("applying upstream patch: %v", err))
			return
		}
	}

	// Detached from the adapter's connection: the body is read to the end
	// even if the adapter stops early, so the fixture is complete.
	ctx, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), p.timeout)
	defer cancel()
	out, err := http.NewRequestWithContext(ctx, r.Method, p.scheme+"://"+r.Host+r.RequestURI, bytes.NewReader(body))
	if err != nil {
		fail(err)
		return
	}
	copyHeaders(out.Header, r.Header)
	// Left to the client's transport, which then undoes any gzip itself, so
	// the fixture is the plain body the adapter would have read.
	out.Header.Del("Accept-Encoding")

	resp, err := p.client.Do(out)
	if err != nil {
		fail(fmt.Errorf("calling upstream: %v", err))
		return
	}
	defer func() { _ = resp.Body.Close() }()

	ex.Status = resp.StatusCode
	ex.ContentType = resp.Header.Get("Content-Type")
	copyHeaders(w.Header(), resp.Header)
	w.Header().Del("Content-Length")
	w.WriteHeader(resp.StatusCode)
	ex.Body, ex.Err = tee(w, resp.Body)
}

// tee copies body to w as it arrives, flushing each read so streams stay
// streams, and returns everything read. It keeps reading after w fails.
func tee(w http.ResponseWriter, body io.Reader) ([]byte, error) {
	var got bytes.Buffer
	flusher, _ := w.(http.Flusher)
	downstream := true
	buf := make([]byte, 32<<10)
	for {
		n, err := body.Read(buf)
		if n > 0 {
			if got.Len()+n > maxBody {
				return got.Bytes(), fmt.Errorf("upstream body exceeds %d bytes", maxBody)
			}
			got.Write(buf[:n])
			if downstream {
				if _, werr := w.Write(buf[:n]); werr != nil {
					downstream = false
				} else if flusher != nil {
					flusher.Flush()
				}
			}
		}
		if err == io.EOF {
			return got.Bytes(), nil
		}
		if err != nil {
			return got.Bytes(), fmt.Errorf("reading upstream body: %v", err)
		}
	}
}

// hopHeaders are connection-scoped and not forwarded (RFC 9110 7.6.1).
var hopHeaders = []string{
	"Connection", "Proxy-Connection", "Keep-Alive", "Proxy-Authenticate",
	"Proxy-Authorization", "Te", "Trailer", "Transfer-Encoding", "Upgrade",
}

func copyHeaders(dst, src http.Header) {
	for k, vv := range src {
		dst[k] = append([]string(nil), vv...)
	}
	for _, h := range hopHeaders {
		dst.Del(h)
	}
}

// applyMergePatch applies an RFC 7396 JSON merge patch to a JSON document.
func applyMergePatch(doc, patch []byte) ([]byte, error) {
	var d, pt any
	if err := decodeNumber(doc, &d); err != nil {
		return nil, fmt.Errorf("request body is not JSON: %v", err)
	}
	if err := decodeNumber(patch, &pt); err != nil {
		return nil, fmt.Errorf("patch is not JSON: %v", err)
	}
	var out bytes.Buffer
	enc := json.NewEncoder(&out)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(mergePatch(d, pt)); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(out.Bytes(), []byte("\n")), nil
}

func mergePatch(target, patch any) any {
	pm, ok := patch.(map[string]any)
	if !ok {
		return patch
	}
	tm, ok := target.(map[string]any)
	if !ok {
		tm = map[string]any{}
	}
	for k, v := range pm {
		if v == nil {
			delete(tm, k)
		} else {
			tm[k] = mergePatch(tm[k], v)
		}
	}
	return tm
}

func decodeNumber(raw []byte, v any) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(v); err != nil {
		return err
	}
	if dec.More() {
		return fmt.Errorf("trailing data after JSON value")
	}
	return nil
}

// pipeListener is a net.Listener whose connections are in-memory pipes made
// by dial, so the proxy needs no port another process could reach.
type pipeListener struct {
	conns chan net.Conn
	done  chan struct{}
	once  sync.Once
}

func newPipeListener() *pipeListener {
	return &pipeListener{conns: make(chan net.Conn), done: make(chan struct{})}
}

func (l *pipeListener) dial(ctx context.Context) (net.Conn, error) {
	client, server := net.Pipe()
	select {
	case l.conns <- server:
		return client, nil
	case <-l.done:
	case <-ctx.Done():
	}
	_ = client.Close()
	_ = server.Close()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return nil, net.ErrClosed
}

func (l *pipeListener) Accept() (net.Conn, error) {
	select {
	case c := <-l.conns:
		return c, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}

func (l *pipeListener) Close() error {
	l.once.Do(func() { close(l.done) })
	return nil
}

func (l *pipeListener) Addr() net.Addr { return pipeAddr{} }

type pipeAddr struct{}

func (pipeAddr) Network() string { return "pipe" }
func (pipeAddr) String() string  { return "contract-recorder" }
