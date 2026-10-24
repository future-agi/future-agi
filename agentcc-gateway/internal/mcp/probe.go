package mcp

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// Why a probe's ok is false (mcp-probe-v1's error).
const (
	ProbeErrInvalidURL         = "invalid_url"    // not an http(s) URL with a host, or one carrying credentials
	ProbeErrEgressRefused      = "egress_refused" // the SSRF guard refused an address or a redirect
	ProbeErrHTTPSRequired      = "https_required" // plaintext http to an address not in mcp.egress.private_allow
	ProbeErrUnreachable        = "unreachable"    // DNS, connect or TLS failure
	ProbeErrTimeout            = "timeout"
	ProbeErrAuthRequired       = "auth_required"  // the upstream answered 401 or 403
	ProbeErrNotMCP             = "not_mcp"        // no era's handshake succeeded
	ProbeErrUpstream           = "upstream_error" // an MCP server whose tool listing failed
	ProbeErrUnsupportedVersion = "unsupported_protocol_version"
)

const (
	probeTimeout   = 15 * time.Second
	probeMaxBody   = 64 << 10 // the request body
	probeMaxReply  = 8 << 20  // one upstream reply, and a listing's pages together
	probeMaxPages  = 100
	probeEra2026   = "2026-07-28"
	probeEraHTTPSE = "2024-11-05"
)

// probeClientInfo is the client the probe introduces itself as.
var probeClientInfo = map[string]string{"name": "agentcc-gateway-probe", "version": "1.0.0"}

// ProbeRequest is POST /-/mcp/probe's body (mcp-probe-v1). credential_id is
// refused: the caller sends the credential inline as auth.
type ProbeRequest struct {
	OrgID     string     `json:"org_id"`
	URL       string     `json:"url"`
	Transport string     `json:"transport,omitempty"` // "", http (Streamable HTTP) or sse (2024-11-05)
	Auth      *ProbeAuth `json:"auth,omitempty"`
}

// ProbeAuth is a credential used for one probe only, never stored or logged.
// It never prints or marshals its value.
type ProbeAuth struct {
	Type   string `json:"type"`             // bearer or api_key
	Header string `json:"header,omitempty"` // default Authorization for bearer; required for api_key
	Value  string `json:"value"`
}

func (a ProbeAuth) String() string   { return "ProbeAuth{" + a.Type + " " + a.headerName() + "}" }
func (a ProbeAuth) GoString() string { return a.String() }

// MarshalJSON leaves the value out.
func (a ProbeAuth) MarshalJSON() ([]byte, error) {
	return json.Marshal(map[string]string{"type": a.Type, "header": a.headerName()})
}

func (a ProbeAuth) headerName() string {
	if a.Header == "" {
		return "Authorization"
	}
	return a.Header
}

func (a ProbeAuth) header() http.Header {
	v := a.Value
	if a.Type == "bearer" {
		v = "Bearer " + v
	}
	h := http.Header{}
	h.Set(a.headerName(), v)
	return h
}

// ProbeServerInfo is the upstream's serverInfo.
type ProbeServerInfo struct {
	Name    string `json:"name"`
	Title   string `json:"title,omitempty"`
	Version string `json:"version,omitempty"`
}

// ProbeResult is mcp-probe-v1's result. Error is null when OK.
type ProbeResult struct {
	OK         bool             `json:"ok"`
	Era        string           `json:"era,omitempty"`
	ServerInfo *ProbeServerInfo `json:"server_info,omitempty"`
	ToolCount  *int             `json:"tool_count,omitempty"`
	ToolsHash  string           `json:"tools_hash,omitempty"`
	LatencyMS  int64            `json:"latency_ms"`
	Error      *string          `json:"error"`
}

func probeFailed(code string) ProbeResult { return ProbeResult{Error: &code} }

// headers no inline credential may set: the gateway's own on this hop.
var probeReservedHeaders = []string{"accept", "connection", "content-length", "content-type", "host",
	"last-event-id", "mcp-method", "mcp-name", "mcp-protocol-version", "mcp-session-id", "transfer-encoding"}

// DecodeProbeRequest reads and checks one probe body. Unknown keys are
// refused, so no body can carry an egress exception.
func DecodeProbeRequest(r io.Reader) (ProbeRequest, error) {
	var body struct {
		ProbeRequest
		CredentialID json.RawMessage `json:"credential_id"` // refused when the key is present, null included
	}
	dec := json.NewDecoder(io.LimitReader(r, probeMaxBody+1))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		return ProbeRequest{}, fmt.Errorf("the body is not one probe object: %w", err)
	}
	if _, err := dec.Token(); err != io.EOF {
		return ProbeRequest{}, errors.New("the body must hold one JSON object only")
	}
	req := body.ProbeRequest
	switch {
	case len(body.CredentialID) > 0:
		return req, errors.New("credential_id is not accepted: send the credential inline as auth")
	case strings.TrimSpace(req.OrgID) == "" || strings.TrimSpace(req.URL) == "":
		return req, errors.New("org_id and url are required")
	case req.Transport != "" && req.Transport != "http" && req.Transport != "sse":
		return req, errors.New("transport must be http or sse")
	}
	if a := req.Auth; a != nil {
		name := strings.ToLower(a.headerName())
		switch {
		case a.Type != "bearer" && a.Type != "api_key":
			return req, errors.New("auth.type must be bearer or api_key")
		case a.Value == "":
			return req, errors.New("auth.value is required")
		case a.Type == "api_key" && a.Header == "":
			return req, errors.New("an api_key auth needs auth.header")
		case !validHeaderName(name) || slices.Contains(probeReservedHeaders, name):
			return req, errors.New("auth.header is not a header an upstream credential may use")
		}
	}
	return req, nil
}

func validHeaderName(name string) bool {
	for _, c := range []byte(name) {
		if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || strings.IndexByte("!#$%&'*+-.^_`|~", c) >= 0) {
			return false
		}
	}
	return name != ""
}

// HandleProbe serves POST /-/mcp/probe after the route's admin check: 400
// invalid_probe_request for a body it refuses, else 200 with the result. It
// logs the org, the host and the outcome, never the URL's path or query or
// the inline credential.
func (s *Server) HandleProbe(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	req, err := DecodeProbeRequest(http.MaxBytesReader(w, r.Body, probeMaxBody))
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid_probe_request", "message": err.Error()})
		return
	}
	res := s.Probe(r.Context(), req)
	host := ""
	if u, err := url.Parse(req.URL); err == nil {
		host = u.Host
	}
	errCode := ""
	if res.Error != nil {
		errCode = *res.Error
	}
	slog.Info("mcp probe", "org_id", req.OrgID, "host", host, "transport", req.Transport,
		"ok", res.OK, "era", res.Era, "error", errCode, "latency_ms", res.LatencyMS)
	_ = json.NewEncoder(w).Encode(res)
}

// Probe connects to req.URL through the egress guard, detects the era,
// lists the tools and reports them. It stores nothing.
func (s *Server) Probe(ctx context.Context, req ProbeRequest) ProbeResult {
	start := time.Now()
	res := s.probe(ctx, req)
	res.LatencyMS = time.Since(start).Milliseconds()
	return res
}

// guardedDialer is the egress guard's HTTP side.
type guardedDialer interface {
	HTTPClient(timeout time.Duration, sensitive ...string) *http.Client
}

func (s *Server) probe(ctx context.Context, req ProbeRequest) ProbeResult {
	u, err := url.Parse(req.URL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.User != nil {
		return probeFailed(ProbeErrInvalidURL)
	}
	// The probe never dials unguarded: without the wiring's guard (a server
	// built without it) it uses one with no operator exceptions.
	guard, ok := s.Deps().Dialer.(guardedDialer)
	if !ok {
		guard, _ = egress.New(egress.Options{})
	}
	p := &prober{base: u}
	if req.Auth != nil {
		p.auth = req.Auth.header()
		p.client = guard.HTTPClient(0, req.Auth.headerName())
	} else {
		p.client = guard.HTTPClient(0)
	}
	defer p.client.CloseIdleConnections() // nothing outlives the probe
	ctx, cancel := context.WithTimeout(ctx, probeTimeout)
	defer cancel()
	if req.Transport != "sse" {
		if res, decided := p.streamable(ctx); decided || req.Transport == "http" {
			return res
		}
	}
	return p.httpSSE(ctx)
}

// probeErrorCode names a failed HTTP exchange.
func probeErrorCode(err error) string {
	var refused *egress.RefusedError
	var ne net.Error
	switch {
	case errors.As(err, &refused) && refused.Reason == egress.ReasonHTTPSRequired:
		return ProbeErrHTTPSRequired
	case errors.As(err, &refused), errors.Is(err, egress.ErrRedirectScheme):
		return ProbeErrEgressRefused
	case errors.Is(err, egress.ErrTooManyRedirects):
		return ProbeErrUpstream
	case errors.Is(err, context.DeadlineExceeded), errors.As(err, &ne) && ne.Timeout():
		return ProbeErrTimeout
	}
	return ProbeErrUnreachable
}

// prober runs one probe's requests.
type prober struct {
	client *http.Client
	base   *url.URL
	auth   http.Header // the inline credential, or nil
	nextID int
}

// rpcCall sends one request of an era's transport and returns its result,
// or the probe error code.
type rpcCall func(ctx context.Context, method string, params map[string]any) (json.RawMessage, string)

// reply is one HTTP answer to a request: its status and headers, and the
// JSON-RPC response it carried (nil when it carried none).
type reply struct {
	status int
	header http.Header
	msg    *Message
}

// send POSTs one JSON-RPC message to target; id 0 sends a notification.
// params is typed as a map so that a nil one is left out: JSON-RPC 2.0
// (section 4.2) allows an object or an array, or no member, never null, and
// the gateway's own client sends tools/list with no params.
func (p *prober) send(ctx context.Context, target, method string, id int, params map[string]any, extra http.Header) (*http.Response, error) {
	msg := map[string]any{"jsonrpc": "2.0", "method": method}
	if id != 0 {
		msg["id"] = id
	}
	if params != nil {
		msg["params"] = params
	}
	body, err := json.Marshal(msg)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, target, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	for _, h := range []http.Header{extra, p.auth} {
		for k, v := range h {
			req.Header[k] = v
		}
	}
	return p.client.Do(req)
}

// post sends a request and reads its answer, a JSON body or an SSE stream.
func (p *prober) post(ctx context.Context, method string, params map[string]any, extra http.Header) (reply, error) {
	p.nextID++
	id := p.nextID
	resp, err := p.send(ctx, p.base.String(), method, id, params, extra)
	if err != nil {
		return reply{}, err
	}
	defer resp.Body.Close()
	r := reply{status: resp.StatusCode, header: resp.Header}
	body := io.LimitReader(resp.Body, probeMaxReply)
	if strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
		events := newSSEReader(body)
		for {
			ev, err := events.next()
			if err != nil {
				return r, nil
			}
			if m := responseTo(ev.data, id); m != nil {
				r.msg = m
				return r, nil
			}
		}
	}
	data, _ := io.ReadAll(body)
	r.msg = responseTo(data, id)
	return r, nil
}

// notify sends a notification and ignores the answer.
func (p *prober) notify(ctx context.Context, target, method string, extra http.Header) {
	if resp, err := p.send(ctx, target, method, 0, nil, extra); err == nil {
		resp.Body.Close()
	}
}

// responseTo decodes data as the JSON-RPC response to request id.
func responseTo(data []byte, id int) *Message {
	var m Message
	if json.Unmarshal(data, &m) != nil || m.JSONRPC != "2.0" || string(bytes.TrimSpace(m.ID)) != strconv.Itoa(id) {
		return nil
	}
	if m.Result == nil && m.Error == nil {
		return nil
	}
	return &m
}

// resultOf is a reply's result, or why there is none.
func resultOf(r reply, err error) (json.RawMessage, string) {
	switch {
	case err != nil:
		return nil, probeErrorCode(err)
	case r.status == http.StatusUnauthorized || r.status == http.StatusForbidden:
		return nil, ProbeErrAuthRequired
	case r.status != http.StatusOK || r.msg == nil || r.msg.Error != nil:
		return nil, ProbeErrNotMCP
	}
	return r.msg.Result, ""
}

// streamable tries the Streamable HTTP eras: 2026-07-28's stateless
// server/discover, then a legacy initialize. decided is false when neither
// answered as MCP, so the caller may try 2024-11-05.
func (p *prober) streamable(ctx context.Context) (ProbeResult, bool) {
	meta := map[string]any{
		"io.modelcontextprotocol/protocolVersion":    probeEra2026,
		"io.modelcontextprotocol/clientCapabilities": map[string]any{},
		"io.modelcontextprotocol/clientInfo":         probeClientInfo,
	}
	call2026 := func(ctx context.Context, method string, params map[string]any) (json.RawMessage, string) {
		if params == nil {
			params = map[string]any{}
		}
		params["_meta"] = meta
		h := http.Header{"Mcp-Protocol-Version": {probeEra2026}, "Mcp-Method": {method}}
		return resultOf(p.post(ctx, method, params, h))
	}
	result, code := call2026(ctx, "server/discover", nil)
	var discover struct {
		SupportedVersions []string         `json:"supportedVersions"`
		ServerInfo        *ProbeServerInfo `json:"serverInfo"`
	}
	if code == "" && json.Unmarshal(result, &discover) == nil && slices.Contains(discover.SupportedVersions, probeEra2026) {
		return p.listResult(ctx, call2026, probeEra2026, discover.ServerInfo), true
	}
	if code != ProbeErrNotMCP && code != "" {
		return probeFailed(code), true
	}

	init := map[string]any{"protocolVersion": ProtocolVersion, "capabilities": map[string]any{}, "clientInfo": probeClientInfo}
	r, err := p.post(ctx, "initialize", init, nil)
	era, info, code := initialized(resultOf(r, err))
	if code == ProbeErrNotMCP {
		return probeFailed(code), false
	}
	if code != "" {
		return probeFailed(code), true
	}
	session := http.Header{"Mcp-Protocol-Version": {era}}
	if sid := r.header.Get("Mcp-Session-Id"); sid != "" {
		session.Set("Mcp-Session-Id", sid)
		defer p.endSession(ctx, session)
	}
	p.notify(ctx, p.base.String(), "notifications/initialized", session)
	callLegacy := func(ctx context.Context, method string, params map[string]any) (json.RawMessage, string) {
		return resultOf(p.post(ctx, method, params, session))
	}
	return p.listResult(ctx, callLegacy, era, info), true
}

// initialized reads an initialize result: the negotiated era and the server.
func initialized(result json.RawMessage, code string) (string, *ProbeServerInfo, string) {
	if code != "" {
		return "", nil, code
	}
	var init struct {
		ProtocolVersion string           `json:"protocolVersion"`
		ServerInfo      *ProbeServerInfo `json:"serverInfo"`
	}
	if json.Unmarshal(result, &init) != nil || init.ProtocolVersion == "" {
		return "", nil, ProbeErrNotMCP
	}
	if !slices.Contains(config.MCPProtocolVersions(), init.ProtocolVersion) {
		return "", nil, ProbeErrUnsupportedVersion
	}
	return init.ProtocolVersion, init.ServerInfo, ""
}

// endSession ends a legacy session the probe opened, past the probe's own
// deadline if need be.
func (p *prober) endSession(ctx context.Context, session http.Header) {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, p.base.String(), nil)
	if err != nil {
		return
	}
	for _, h := range []http.Header{session, p.auth} {
		for k, v := range h {
			req.Header[k] = v
		}
	}
	if resp, err := p.client.Do(req); err == nil {
		resp.Body.Close()
	}
}

// listResult lists every tool page through call and builds the result.
func (p *prober) listResult(ctx context.Context, call rpcCall, era string, info *ProbeServerInfo) ProbeResult {
	var tools []json.RawMessage
	seen := map[string]bool{}
	cursor, size := "", 0
	for page := 0; ; page++ {
		if page == probeMaxPages {
			return probeFailed(ProbeErrUpstream)
		}
		var params map[string]any
		if cursor != "" {
			params = map[string]any{"cursor": cursor}
		}
		result, code := call(ctx, "tools/list", params)
		if code == ProbeErrNotMCP {
			code = ProbeErrUpstream // the server spoke MCP; its listing failed
		}
		if size += len(result); code == "" && size > probeMaxReply {
			code = ProbeErrUpstream // a listing too large to hold
		}
		if code != "" {
			return probeFailed(code)
		}
		var list struct {
			Tools      []json.RawMessage `json:"tools"`
			NextCursor string            `json:"nextCursor"`
		}
		if json.Unmarshal(result, &list) != nil {
			return probeFailed(ProbeErrUpstream)
		}
		tools = append(tools, list.Tools...)
		if list.NextCursor == "" {
			break
		}
		if seen[list.NextCursor] {
			return probeFailed(ProbeErrUpstream)
		}
		seen[list.NextCursor], cursor = true, list.NextCursor
	}
	hash, err := ToolsHash(tools)
	if err != nil {
		return probeFailed(ProbeErrUpstream)
	}
	if info != nil && info.Name == "" {
		info = nil
	}
	n := len(tools)
	return ProbeResult{OK: true, Era: era, ServerInfo: info, ToolCount: &n, ToolsHash: hash}
}

// httpSSE tries 2024-11-05's HTTP+SSE transport: GET the URL for an event
// stream whose first event names the message endpoint (same origin), POST
// each request there, and read each answer from the stream.
func (p *prober) httpSSE(ctx context.Context) ProbeResult {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.base.String(), nil)
	if err != nil {
		return probeFailed(ProbeErrInvalidURL)
	}
	req.Header.Set("Accept", "text/event-stream")
	for k, v := range p.auth {
		req.Header[k] = v
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return probeFailed(probeErrorCode(err))
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return probeFailed(ProbeErrAuthRequired)
	}
	if resp.StatusCode != http.StatusOK || !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
		return probeFailed(ProbeErrNotMCP)
	}
	events := newSSEReader(resp.Body)
	first, err := events.next()
	if err != nil || first.event != "endpoint" {
		return probeFailed(ProbeErrNotMCP)
	}
	endpoint, err := p.base.Parse(strings.TrimSpace(string(first.data)))
	if err != nil || endpoint.Scheme != p.base.Scheme || endpoint.Host != p.base.Host {
		return probeFailed(ProbeErrNotMCP)
	}
	stream := make(chan []byte)
	go func() {
		defer close(stream)
		for {
			ev, err := events.next()
			if err != nil {
				return
			}
			if ev.event != "" && ev.event != "message" {
				continue
			}
			select {
			case stream <- ev.data:
			case <-ctx.Done():
				return
			}
		}
	}()
	call := func(ctx context.Context, method string, params map[string]any) (json.RawMessage, string) {
		p.nextID++
		id := p.nextID
		resp, err := p.send(ctx, endpoint.String(), method, id, params, nil)
		if err != nil {
			return nil, probeErrorCode(err)
		}
		resp.Body.Close()
		if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
			return nil, ProbeErrAuthRequired
		}
		if resp.StatusCode/100 != 2 {
			return nil, ProbeErrNotMCP
		}
		for {
			select {
			case data, open := <-stream:
				if !open {
					return nil, ProbeErrNotMCP
				}
				if m := responseTo(data, id); m != nil {
					return resultOf(reply{status: http.StatusOK, msg: m}, nil)
				}
			case <-ctx.Done():
				return nil, ProbeErrTimeout
			}
		}
	}
	init := map[string]any{"protocolVersion": probeEraHTTPSE, "capabilities": map[string]any{}, "clientInfo": probeClientInfo}
	era, info, code := initialized(call(ctx, "initialize", init))
	if code != "" {
		return probeFailed(code)
	}
	p.notify(ctx, endpoint.String(), "notifications/initialized", nil)
	return p.listResult(ctx, call, era, info)
}

// sseEvent is one server-sent event.
type sseEvent struct {
	event string
	data  []byte
}

// sseReader reads server-sent events (event and data fields; id, retry and
// comments are skipped).
type sseReader struct{ sc *bufio.Scanner }

func newSSEReader(r io.Reader) *sseReader {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64<<10), probeMaxReply)
	return &sseReader{sc: sc}
}

func (s *sseReader) next() (sseEvent, error) {
	var ev sseEvent
	var data [][]byte
	for s.sc.Scan() {
		line := bytes.TrimSuffix(s.sc.Bytes(), []byte("\r"))
		if len(line) == 0 {
			if data != nil || ev.event != "" {
				ev.data = bytes.Join(data, []byte("\n"))
				return ev, nil
			}
			continue
		}
		field, value, _ := bytes.Cut(line, []byte(":"))
		value = bytes.TrimPrefix(value, []byte(" "))
		switch string(field) {
		case "event":
			ev.event = string(value)
		case "data":
			data = append(data, append([]byte(nil), value...))
		}
	}
	if err := s.sc.Err(); err != nil {
		return ev, err
	}
	return ev, io.EOF
}

// probeToolFields are the definition fields ToolsHash covers.
var probeToolFields = []string{"name", "title", "description", "inputSchema", "outputSchema", "annotations"}

// ToolsHash is the hex sha256 of a tool list's canonical form. The form,
// exactly:
//   - each tool is projected onto those of name, title, description,
//     inputSchema, outputSchema and annotations it carries, values as the
//     upstream sent them;
//   - each projection is JSON with no insignificant whitespace and object
//     keys sorted bytewise at every depth (UTF-8 byte order, which is code
//     point order);
//   - number literals are kept byte for byte as written (1.50 stays 1.50,
//     1e3 stays 1e3), so they are compared as text, not as values;
//   - strings use encoding/json's escaping with HTML escaping off: \" \\ \b
//     \f \n \r \t, other C0 controls as \u00xx (lowercase), U+2028 and U+2029
//     as \u2028 and \u2029, and invalid UTF-8 (or an escaped lone surrogate)
//     as U+FFFD; every other character is written as itself;
//   - the projections are sorted by name (then by their bytes) and joined
//     with "," inside "[" and "]".
//
// This is not Python's json.dumps(sort_keys=True, separators=(",", ":"),
// ensure_ascii=False): that rewrites number literals as values (1.50 as 1.5,
// 1e3 as 1000.0) and writes U+2028 and U+2029 unescaped. Nor is it RFC 8785
// (JCS): that also normalizes numbers and sorts keys by UTF-16 code units.
// A recompute outside Go must follow the rules above.
func ToolsHash(tools []json.RawMessage) (string, error) {
	type entry struct {
		name  string
		canon []byte
	}
	entries := make([]entry, 0, len(tools))
	for i, raw := range tools {
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(raw, &fields); err != nil {
			return "", fmt.Errorf("tool %d: %w", i, err)
		}
		proj := make(map[string]any, len(probeToolFields))
		for _, k := range probeToolFields {
			if v, ok := fields[k]; ok {
				dec := json.NewDecoder(bytes.NewReader(v))
				dec.UseNumber()
				var val any
				if err := dec.Decode(&val); err != nil {
					return "", fmt.Errorf("tool %d %s: %w", i, k, err)
				}
				proj[k] = val
			}
		}
		var buf bytes.Buffer
		enc := json.NewEncoder(&buf)
		enc.SetEscapeHTML(false)
		if err := enc.Encode(proj); err != nil {
			return "", fmt.Errorf("tool %d: %w", i, err)
		}
		var name string
		_ = json.Unmarshal(fields["name"], &name)
		entries = append(entries, entry{name: name, canon: bytes.TrimSuffix(buf.Bytes(), []byte("\n"))})
	}
	sort.SliceStable(entries, func(i, j int) bool {
		if entries[i].name != entries[j].name {
			return entries[i].name < entries[j].name
		}
		return bytes.Compare(entries[i].canon, entries[j].canon) < 0
	})
	h := sha256.New()
	h.Write([]byte("["))
	for i, e := range entries {
		if i > 0 {
			h.Write([]byte(","))
		}
		h.Write(e.canon)
	}
	h.Write([]byte("]"))
	return hex.EncodeToString(h.Sum(nil)), nil
}
