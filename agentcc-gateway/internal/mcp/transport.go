package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"slices"
	"strings"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentbounds"
	"github.com/futureagi/agentcc-gateway/internal/mcp/contentpolicy"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream/wire"
)

// Transport abstracts communication with an MCP server.
type Transport interface {
	// Send sends a JSON-RPC message and returns the response.
	// For notifications (no ID), the response may be nil.
	Send(ctx context.Context, msg *Message) (*Message, error)
	// Close shuts down the transport.
	Close() error
	// Healthy returns whether the transport is operational.
	Healthy() bool
}

// sendOptions are one request's additions on an HTTP transport.
type sendOptions struct {
	// auth replaces the server's configured credential for this request.
	auth *AuthOverride
	// header holds the forwarded and mirrored headers; the transport's own
	// (credential, session, protocol version) are set after it and win.
	header http.Header
	// maxBytes bounds the answer; 0 keeps the transport's bound.
	maxBytes int64
}

// optionSender is a transport that sends one request with sendOptions: the
// HTTP ones. stdio has no headers to carry them.
type optionSender interface {
	sendWith(ctx context.Context, msg *Message, opts sendOptions) (*Message, error)
}

var (
	// errSessionNotFound is HTTP 404 on a request that carried a legacy
	// session id: the upstream ended the session (it is checked before the
	// request is served).
	errSessionNotFound = errors.New("mcp upstream: session not found (HTTP 404)")
	// errStreamUnsupported is an upstream with no stream of that kind.
	errStreamUnsupported = errors.New("mcp upstream: no notification stream")
	// errUndecodable is an upstream answer that is not the JSON-RPC message
	// it should be (attributed to the upstream).
	errUndecodable = errors.New("mcp upstream: an answer that does not decode")
)

// HTTPStatusError is an upstream's non-2xx answer, with its JSON-RPC error
// when the body was one.
type HTTPStatusError struct {
	Status int
	RPC    *RPCError
}

func (e *HTTPStatusError) Error() string {
	if e.RPC != nil {
		return fmt.Sprintf("http %d: JSON-RPC error %d: %s", e.Status, e.RPC.Code, e.RPC.Message)
	}
	return fmt.Sprintf("http %d", e.Status)
}

// statusError builds the error of a non-2xx reply.
func statusError(rep wire.Reply) error {
	e := &HTTPStatusError{Status: rep.Status}
	var m Message
	if json.Unmarshal(rep.Message, &m) == nil && m.Error != nil {
		e.RPC = m.Error
	}
	return e
}

// --- HTTP Transport ---

// HTTPTransport communicates with an MCP server over Streamable HTTP, at the
// configured URL exactly. It accepts JSON and SSE answers, sends
// MCP-Protocol-Version once a version is negotiated, and bounds every answer.
type HTTPTransport struct {
	client    *http.Client
	url       string
	sessionID atomic.Pointer[string]
	version   atomic.Pointer[string] // sent as MCP-Protocol-Version, except on initialize
	auth      AuthConfig
	maxBytes  int64 // the largest answer; <= 0 = no bound
	healthy   atomic.Bool
	onMessage func(*Message) // the upstream's messages that answer no request
}

// AuthConfig holds authentication settings for an upstream MCP server.
type AuthConfig struct {
	// Governed clients carry only this opaque transport capability. Its payload
	// is private to request_credential.go; no header getter is exposed.
	requestCredential *requestCredential
	Type              string // "bearer", "api_key", "none"
	Token             string // for bearer auth
	Header            string // header name for api_key auth
	Key               string // header value for api_key auth
	// Template places a pushed credential (a header, a query parameter
	// or a secret URL variable, in one part or several) on every request
	// to the server's origin; Type is then empty, and the template wraps the
	// upstream HTTP client's transport. It prints without its values.
	Template *credtemplate.Template
	// Source gives the headers of every request to the server when it is
	// sent (the organization's signed-in account's token, fetched just
	// in time); Type is then empty. A request it cannot give headers for is
	// not sent.
	Source HeaderSource
}

// HeaderSource gives the credential headers of one upstream request. Its
// errors never carry a credential.
type HeaderSource interface {
	Header(ctx context.Context) (http.Header, error)
}

// sourceTransport sets each request's credential headers from a
// HeaderSource as it is sent, over next.
type sourceTransport struct {
	next http.RoundTripper
	src  HeaderSource
}

func (t sourceTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	h, err := t.src.Header(req.Context())
	if err != nil {
		if req.Body != nil {
			_ = req.Body.Close()
		}
		return nil, err
	}
	req = req.Clone(req.Context())
	for name, values := range h {
		req.Header[http.CanonicalHeaderKey(name)] = slices.Clone(values)
	}
	return t.next.RoundTrip(req)
}

// NewHTTPTransport creates a transport for an HTTP-based MCP server at url,
// dialling with a plain net.Dialer and no answer bound.
func NewHTTPTransport(url string, auth AuthConfig) *HTTPTransport {
	return newHTTPTransport(url, auth, nil, 0)
}

func newHTTPTransport(url string, auth AuthConfig, dialer Dialer, maxBytes int64) *HTTPTransport {
	t := &HTTPTransport{
		client:   upstreamHTTPClient(dialer, auth),
		url:      url,
		auth:     auth,
		maxBytes: maxBytes,
	}
	t.healthy.Store(true)
	return t
}

// guardedClients are dialers with an HTTP client of their own: the probe step's
// egress.Dialer, whose client adds the HTTPS rule and checks redirects.
type guardedClients interface {
	HTTPClient(timeout time.Duration, sensitive ...string) *http.Client
}

// upstreamHTTPClient is the client every upstream request goes through,
// dialling with dialer (nil: net.Dialer). It has no overall timeout and no
// response-header timeout: each call is bounded by its context (the server's
// timeout_ms or tool_call_timeout), and a tool may take minutes before its
// headers come. It follows a redirect only within the URL's origin, so no
// per-call credential leaves it.
func upstreamHTTPClient(dialer Dialer, auth AuthConfig) *http.Client {
	var c *http.Client
	if g, ok := dialer.(guardedClients); ok {
		sensitive := []string{"Authorization"}
		if auth.Header != "" {
			sensitive = append(sensitive, auth.Header)
		}
		if auth.Template != nil {
			sensitive = append(sensitive, auth.Template.HeaderNames()...)
		}
		c = g.HTTPClient(0, sensitive...)
		if tr, ok := c.Transport.(*http.Transport); ok {
			tr.ResponseHeaderTimeout = 0
		}
	} else {
		if dialer == nil {
			dialer = &net.Dialer{Timeout: 30 * time.Second}
		}
		c = &http.Client{Transport: &http.Transport{
			Proxy:               nil,
			DialContext:         dialer.DialContext,
			MaxIdleConns:        10,
			MaxIdleConnsPerHost: 10,
			IdleConnTimeout:     90 * time.Second,
		}}
	}
	inner := c.CheckRedirect
	c.CheckRedirect = func(req *http.Request, via []*http.Request) error {
		if inner != nil {
			if err := inner(req, via); err != nil {
				return err
			}
		} else if len(via) >= 5 {
			return errors.New("mcp upstream: stopped after 5 redirects")
		}
		if req.URL.Scheme != via[0].URL.Scheme || !strings.EqualFold(req.URL.Host, via[0].URL.Host) {
			return http.ErrUseLastResponse
		}
		return nil
	}
	if auth.Template != nil {
		// Outermost, over the guard's transport (whose response-header
		// timeout is already cleared): the guard still checks every dial.
		c.Transport = auth.Template.RoundTripper(c.Transport)
	}
	if auth.Source != nil {
		// The same: the redirect rule above keeps the headers on the origin.
		c.Transport = sourceTransport{next: c.Transport, src: auth.Source}
	}
	installRequestCredential(c, auth)
	return c
}

func (t *HTTPTransport) Send(ctx context.Context, msg *Message) (*Message, error) {
	return t.sendWith(ctx, msg, sendOptions{})
}

func (t *HTTPTransport) sendWith(ctx context.Context, msg *Message, opts sendOptions) (*Message, error) {
	body, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}
	maxBytes := contentpolicy.ByteLimit(t.maxBytes, opts.maxBytes)
	sid := t.session()
	rep, err := wire.Post(ctx, wire.Exchange{
		Client:    t.client,
		URL:       t.url,
		Header:    t.headers(http.MethodPost, msg.Method, opts),
		Body:      body,
		ID:        msg.ID,
		MaxBytes:  maxBytes,
		OnMessage: func(raw []byte) { deliverRaw(raw, t.onMessage) },
	})
	if err != nil {
		if !errors.Is(err, wire.ErrTooLarge) && ctx.Err() == nil {
			t.healthy.Store(false)
		}
		return nil, fmt.Errorf("http request: %w", err)
	}

	// Store session ID from response.
	if s := rep.Header.Get("Mcp-Session-Id"); s != "" {
		t.sessionID.Store(&s)
	}
	switch {
	case rep.Status == http.StatusAccepted || rep.Status == http.StatusNoContent:
		if msg.IsNotification() || msg.IsResponse() {
			return nil, nil
		}
		return nil, fmt.Errorf("http %d: no answer to %s", rep.Status, msg.Method)
	case rep.Status == http.StatusNotFound && sid != "":
		return nil, errSessionNotFound
	case rep.Status/100 != 2:
		return nil, statusError(rep)
	}
	t.healthy.Store(true)
	if msg.IsNotification() || msg.IsResponse() {
		return nil, nil
	}
	var result Message
	if err := json.Unmarshal(rep.Message, &result); err != nil {
		return nil, fmt.Errorf("%w: %w", errUndecodable, err)
	}
	return &result, nil
}

// headers are one request's headers: the method's, then opts.header (the
// forwarded and mirrored ones), then the transport's own, which win.
func (t *HTTPTransport) headers(httpMethod, method string, opts sendOptions) http.Header {
	opts = credentialSendOptions(t.auth, opts)
	h := http.Header{}
	for k, vs := range opts.header {
		h[http.CanonicalHeaderKey(k)] = append([]string(nil), vs...)
	}
	if httpMethod == http.MethodPost {
		h.Set("Content-Type", "application/json")
		h.Set("Accept", "application/json, text/event-stream")
	} else {
		h.Set("Accept", "text/event-stream")
	}
	if opts.auth != nil {
		for k, vs := range opts.auth.Header {
			h[http.CanonicalHeaderKey(k)] = append([]string(nil), vs...)
		}
	} else {
		t.applyAuth(h)
	}
	if v := t.version.Load(); v != nil && method != MethodInitialize {
		h.Set("MCP-Protocol-Version", *v)
	}
	if sid := t.session(); sid != "" && method != MethodInitialize {
		h.Set("Mcp-Session-Id", sid) // an initialize starts a new session
	}
	return h
}

// session is the legacy session id, or "".
func (t *HTTPTransport) session() string {
	if sid := t.sessionID.Load(); sid != nil {
		return *sid
	}
	return ""
}

// setVersion makes v the MCP-Protocol-Version of every later request.
func (t *HTTPTransport) setVersion(v string) { t.version.Store(&v) }

// stream opens a long-lived notification stream (a legacy session's GET
// stream, or a 2026-07-28 subscriptions/listen POST when listen is set) and
// hands each message to onMessage until ctx ends or the stream does.
func (t *HTTPTransport) stream(ctx context.Context, listen *Message, header http.Header, onMessage func(*Message)) error {
	httpMethod, method := http.MethodGet, ""
	var body io.Reader
	if listen != nil {
		raw, err := json.Marshal(listen)
		if err != nil {
			return err
		}
		httpMethod, method, body = http.MethodPost, listen.Method, bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, httpMethod, t.url, body)
	if err != nil {
		return err
	}
	req.Header = t.headers(httpMethod, method, sendOptions{header: header})
	sid := t.session()
	resp, err := t.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusNotFound && sid != "" && listen == nil:
		return errSessionNotFound
	case resp.StatusCode/100 != 2 || !wire.IsEventStream(resp.Header):
		return fmt.Errorf("%w: http %d", errStreamUnsupported, resp.StatusCode)
	}
	events := wire.NewReader(resp.Body, contentpolicy.ByteLimit(t.maxBytes))
	for {
		ev, err := events.Next()
		if err != nil {
			if errors.Is(err, io.EOF) || ctx.Err() != nil {
				return nil
			}
			return err
		}
		if ev.Name == "" || ev.Name == "message" {
			if err := contentpolicy.Envelope(ev.Data, t.maxBytes); err != nil {
				return err
			}
			deliverRaw(ev.Data, onMessage)
		}
	}
}

func (t *HTTPTransport) Close() error {
	t.client.CloseIdleConnections()
	return nil
}

func (t *HTTPTransport) Healthy() bool {
	return t.healthy.Load()
}

// applyAuth sets the server's configured credential.
func (t *HTTPTransport) applyAuth(h http.Header) {
	switch t.auth.Type {
	case "bearer":
		h.Set("Authorization", "Bearer "+t.auth.Token)
	case "api_key":
		if t.auth.Header != "" {
			h.Set(t.auth.Header, t.auth.Key)
		}
	}
}

// deliverRaw decodes one upstream message and hands it to sink; a message
// that does not decode is dropped.
func deliverRaw(raw []byte, sink func(*Message)) {
	if sink == nil {
		return
	}
	if contentpolicy.Envelope(raw, 0) != nil {
		return
	}
	var m Message
	if json.Unmarshal(raw, &m) == nil && m.JSONRPC == "2.0" {
		sink(&m)
	}
}

// --- HTTP+SSE Transport (2024-11-05) ---

// SSETransport speaks the 2024-11-05 HTTP+SSE transport (a server whose
// transport is "sse"): one GET stream per connection, requests POSTed to the
// endpoint it names, answers read from the stream.
type SSETransport struct {
	client    *http.Client
	url       string
	auth      AuthConfig
	maxBytes  int64
	onMessage func(*Message)
	onDead    func(error)
	session   atomic.Pointer[wire.Session]
}

func newSSETransport(url string, auth AuthConfig, dialer Dialer, maxBytes int64) *SSETransport {
	return &SSETransport{client: upstreamHTTPClient(dialer, auth), url: url, auth: auth, maxBytes: maxBytes}
}

// Start opens the stream; ctx bounds the wait for its endpoint event only.
func (t *SSETransport) Start(ctx context.Context) error {
	h := http.Header{}
	(&HTTPTransport{auth: t.auth}).applyAuth(h)
	s, err := wire.OpenSession(ctx, wire.SessionOptions{
		Client:    t.client,
		URL:       t.url,
		Header:    h,
		MaxBytes:  t.maxBytes,
		OnMessage: func(raw []byte) { deliverRaw(raw, t.onMessage) },
		OnDead:    t.onDead,
	})
	if err != nil {
		return err
	}
	t.session.Store(s)
	return nil
}

func (t *SSETransport) Send(ctx context.Context, msg *Message) (*Message, error) {
	return t.sendWith(ctx, msg, sendOptions{})
}

func (t *SSETransport) sendWith(ctx context.Context, msg *Message, opts sendOptions) (*Message, error) {
	opts = credentialSendOptions(t.auth, opts)
	s := t.session.Load()
	if s == nil {
		return nil, errors.New("mcp upstream: HTTP+SSE stream not open")
	}
	body, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}
	header := opts.header.Clone()
	if header == nil {
		header = http.Header{}
	}
	if opts.auth != nil {
		for k, vs := range opts.auth.Header {
			header[http.CanonicalHeaderKey(k)] = append([]string(nil), vs...)
		}
	}
	id := msg.ID
	if msg.IsResponse() {
		id = nil // an answer to the server's own request waits for nothing
	}
	raw, err := s.Send(ctx, id, body, header, opts.auth == nil)
	if err != nil || id == nil {
		return nil, err
	}
	if err := contentpolicy.Envelope(raw, contentpolicy.ByteLimit(t.maxBytes, opts.maxBytes)); err != nil {
		return nil, contentTransportError(err)
	}
	var result Message
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("%w: %w", errUndecodable, err)
	}
	return &result, nil
}

// Err is why the stream ended, or nil while it runs.
func (t *SSETransport) Err() error {
	if s := t.session.Load(); s != nil {
		return s.Err()
	}
	return nil
}

func (t *SSETransport) Close() error {
	if s := t.session.Load(); s != nil {
		s.Close()
	}
	t.client.CloseIdleConnections()
	return nil
}

func (t *SSETransport) Healthy() bool {
	s := t.session.Load()
	return s != nil && s.Err() == nil
}

// contentTransportError preserves the public byte-limit sentinel for transports
// that already supplied a decoded message; structural failures stay value-free.
func contentTransportError(err error) error {
	var refusal *contentbounds.Error
	if errors.As(err, &refusal) && refusal.Code() == contentbounds.CodeReadBytes {
		return ErrResultTooLarge
	}
	return err
}
