package record

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"sort"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
)

// maxBody bounds one request or response body the proxy records.
const maxBody = 32 << 20

// hopByHop headers are not forwarded or recorded (Proxy-Authorization is
// for this proxy, never the upstream). Accept-Encoding is
// dropped on the way out so Go's transport negotiates gzip itself and hands
// back the decoded body, which is what the cassette must hold;
// Content-Length and Content-Encoding no longer describe that body.
var hopByHop = map[string]bool{"Connection": true, "Keep-Alive": true, "Proxy-Connection": true,
	"Proxy-Authenticate": true, "Proxy-Authorization": true, "Te": true, "Trailer": true, "Transfer-Encoding": true, "Upgrade": true,
	"Host": true, "Accept-Encoding": true, "Content-Length": true, "Content-Encoding": true}

// ProxyOptions configure an HTTP recording.
type ProxyOptions struct {
	Upstream    string // scheme://host[:port], no path
	TestAccount string
	Timeout     time.Duration // per request; 0 means 60 s
	HTTPClient  *http.Client  // nil: a client that follows no redirect
}

// Proxy forwards every request to the upstream and records the pair in
// memory, in arrival order. It writes nothing; Cassette hands the recording
// to cassette.Save.
type Proxy struct {
	upstream *url.URL
	hc       *http.Client

	mu        sync.Mutex
	seq       int
	exchanges map[int]cassette.HTTPExchange
	account   string
}

// NewProxy refuses to start without a test-account label or with an
// upstream a credential must not travel to.
func NewProxy(o ProxyOptions) (*Proxy, error) {
	if err := cassette.ValidateTestAccount(o.TestAccount); err != nil {
		return nil, err
	}
	if err := CheckUpstream(o.Upstream, true); err != nil {
		return nil, err
	}
	u, _ := url.Parse(o.Upstream)
	hc := noRedirectClient()
	if o.HTTPClient != nil {
		copied := *o.HTTPClient
		hc = &copied
	}
	hc.Timeout = o.Timeout
	if hc.Timeout <= 0 {
		hc.Timeout = 60 * time.Second
	}
	return &Proxy{upstream: u, hc: hc, exchanges: map[int]cassette.HTTPExchange{}, account: o.TestAccount}, nil
}

func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBody+1))
	if err != nil || len(body) > maxBody {
		http.Error(w, "agentcc-cassette: request body unreadable or over 32 MiB", http.StatusRequestEntityTooLarge)
		return
	}
	p.mu.Lock()
	p.seq++
	seq := p.seq
	p.mu.Unlock()
	target := *p.upstream
	target.Path, target.RawPath, target.RawQuery = r.URL.Path, r.URL.RawPath, r.URL.RawQuery
	req, err := http.NewRequestWithContext(r.Context(), r.Method, target.String(), bytes.NewReader(body))
	if err != nil {
		http.Error(w, "agentcc-cassette: bad request", http.StatusBadRequest)
		return
	}
	req.Header = forwardable(r.Header)
	resp, err := p.hc.Do(req)
	if err != nil {
		http.Error(w, "agentcc-cassette: "+plainError(err).Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(io.LimitReader(resp.Body, maxBody+1))
	if err != nil || len(out) > maxBody {
		http.Error(w, "agentcc-cassette: upstream body unreadable or over 32 MiB; not recorded", http.StatusBadGateway)
		return
	}
	respHeaders := forwardable(resp.Header)
	p.mu.Lock()
	p.exchanges[seq] = cassette.HTTPExchange{
		Request: cassette.HTTPRequest{Method: r.Method, Path: r.URL.EscapedPath(), Query: r.URL.RawQuery,
			Headers: forwardable(r.Header), Body: cassette.NewBody(r.Header.Get("Content-Type"), body)},
		Response: cassette.HTTPResponse{Status: resp.StatusCode, Headers: respHeaders,
			Body: cassette.NewBody(resp.Header.Get("Content-Type"), out)},
	}
	p.mu.Unlock()
	for k, vs := range respHeaders {
		w.Header()[k] = vs
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = w.Write(out)
}

func forwardable(h http.Header) http.Header {
	out := http.Header{}
	for k, vs := range h {
		if !hopByHop[http.CanonicalHeaderKey(k)] {
			out[k] = append([]string(nil), vs...)
		}
	}
	return out
}

// Cassette is everything recorded so far, in arrival order, not scrubbed.
func (p *Proxy) Cassette() *cassette.Cassette {
	p.mu.Lock()
	defer p.mu.Unlock()
	seqs := make([]int, 0, len(p.exchanges))
	for s := range p.exchanges {
		seqs = append(seqs, s)
	}
	sort.Ints(seqs)
	rec := &cassette.HTTPRecording{}
	for _, s := range seqs {
		rec.Exchanges = append(rec.Exchanges, p.exchanges[s])
	}
	return &cassette.Cassette{Format: cassette.Format, Kind: cassette.KindHTTP, TestAccount: p.account,
		RecordedAt: time.Now().UTC().Format(time.RFC3339), HTTP: rec}
}

// CheckUpstream refuses an upstream a credential must not travel to: not
// HTTPS (plain HTTP only to a loopback host, for local test servers), or
// with user info in it. A proxy upstream is a bare origin.
func CheckUpstream(raw string, origin bool) error {
	u, err := url.Parse(raw)
	switch {
	case err != nil || u.Host == "":
		return errors.New("the upstream must be an absolute http(s) URL")
	case u.User != nil:
		return errors.New("the upstream URL must not carry credentials")
	case u.Scheme == "http" && !isLoopback(u.Hostname()):
		return fmt.Errorf("plain http is allowed to a loopback host only, not %q", u.Hostname())
	case u.Scheme != "http" && u.Scheme != "https":
		return fmt.Errorf("upstream scheme %q: want https", u.Scheme)
	case origin && (u.Path != "" && u.Path != "/" || u.RawQuery != "" || u.Fragment != ""):
		return errors.New("the proxy upstream is an origin (scheme://host[:port]) with no path or query")
	}
	return nil
}

// CheckLoopback refuses a listen address that is not a loopback one: the
// recording proxy relays the operator's credential and must not be an open
// relay on the network.
func CheckLoopback(addr string) error {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return fmt.Errorf("listen address %q: %w", addr, err)
	}
	if !isLoopback(host) {
		return fmt.Errorf("the recording proxy listens on a loopback address only, not %q", host)
	}
	return nil
}

func isLoopback(host string) bool {
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}
