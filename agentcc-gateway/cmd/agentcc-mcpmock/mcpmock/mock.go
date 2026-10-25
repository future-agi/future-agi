package mcpmock

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

// Message is one JSON-RPC 2.0 message: a request, a notification or a
// response.
type Message struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *RPCError       `json:"error,omitempty"`
}

func (m *Message) isNotification() bool { return m.Method != "" && len(m.ID) == 0 }
func (m *Message) isResponse() bool     { return m.Method == "" && len(m.ID) > 0 }

func notification(method string, params any) *Message {
	raw, _ := json.Marshal(params)
	return &Message{JSONRPC: "2.0", Method: method, Params: raw}
}

func response(id json.RawMessage, result any, err *RPCError) *Message {
	msg := &Message{JSONRPC: "2.0", ID: id, Error: err}
	if err == nil {
		msg.Result, _ = json.Marshal(result)
	}
	if len(msg.ID) == 0 {
		msg.ID = json.RawMessage("null")
	}
	return msg
}

// Config is the mock's start-up configuration (the binary's flags).
type Config struct {
	Era        Era
	Auth       string // none, bearer:<token> or oauth
	Path       string // the Streamable HTTP endpoint (default /mcp)
	PublicURL  string // the base URL clients use (OAuth issuer and resource)
	ServerName string
	Stdio      bool // stdio mode: lists env_names too
	// AuthHeader is the header /__received reports as auth (default
	// Authorization); CredentialHeaders are more names kept as presence and
	// hash only, besides the ones credentialWords catch.
	AuthHeader        string
	CredentialHeaders []string
	// ElicitTimeout bounds how long a legacy elicitation waits for the
	// client's answer.
	ElicitTimeout time.Duration
}

// Mock is the MCP mock upstream: its state, sessions and listeners, behind
// the HTTP handler (Handler) or the stdio loop (ServeStdio).
type Mock struct {
	cfg      Config
	state    *State
	sessions *sessionStore
	oauth    *oauthServer
	stateKey []byte // HMAC key for the mock's own requestState
	headers  headerPolicy

	hubMu     sync.Mutex
	hubNext   int
	listeners map[int]func(*Message)
	elicitSeq int64
}

// New builds a mock over the fixtures.
func New(cfg Config, fix *Fixtures) (*Mock, error) {
	if cfg.Path == "" {
		cfg.Path = "/mcp"
	}
	if !strings.HasPrefix(cfg.Path, "/") || strings.HasPrefix(cfg.Path, "/__") {
		return nil, fmt.Errorf("--path %q must start with / and not with /__", cfg.Path)
	}
	if cfg.ServerName == "" {
		cfg.ServerName = "agentcc-mcpmock"
	}
	if cfg.ElicitTimeout <= 0 {
		cfg.ElicitTimeout = 60 * time.Second
	}
	if cfg.Auth == "" {
		cfg.Auth = "none"
	}
	if cfg.Auth != "none" && cfg.Auth != "oauth" &&
		(!strings.HasPrefix(cfg.Auth, "bearer:") || len(cfg.Auth) == len("bearer:")) {
		return nil, fmt.Errorf("--auth %q: want none, bearer:<token> or oauth", cfg.Auth)
	}
	if err := checkHeaderNames(cfg.AuthHeader, cfg.CredentialHeaders); err != nil {
		return nil, err
	}
	m := &Mock{
		cfg:       cfg,
		state:     NewState(fix),
		sessions:  newSessionStore(),
		stateKey:  randomBytes(32),
		listeners: map[int]func(*Message){},
		headers:   newHeaderPolicy(cfg.AuthHeader, cfg.CredentialHeaders),
	}
	if cfg.Auth == "oauth" {
		m.oauth = newOAuthServer()
	}
	return m, nil
}

// checkHeaderNames refuses an --auth-header or --credential-header value
// that is not a bare header name (an empty --auth-header means Authorization).
func checkHeaderNames(authHeader string, extra []string) error {
	for i, name := range append([]string{authHeader}, extra...) {
		if (i > 0 && name == "") || strings.ContainsAny(name, " \t:\r\n") {
			return fmt.Errorf("header name %q: want a bare HTTP header name", name)
		}
	}
	return nil
}

// State exposes the mock's state (tests and the control API).
func (m *Mock) State() *State { return m.state }

// SetPublicURL sets the base URL once the listener's address is known.
func (m *Mock) SetPublicURL(u string) { m.cfg.PublicURL = strings.TrimRight(u, "/") }

// resourceURI is the endpoint's canonical URI: the OAuth resource and the
// audience a token must carry.
func (m *Mock) resourceURI() string { return m.cfg.PublicURL + m.cfg.Path }

// Handler serves the MCP endpoint, the 2024-11-05 endpoints, OAuth and the
// control API. In stdio mode the binary serves only the control API.
func (m *Mock) Handler() http.Handler {
	mux := http.NewServeMux()
	m.registerControl(mux)
	if m.cfg.Stdio {
		return mux
	}
	if m.cfg.Era.streamable() {
		mux.HandleFunc(m.cfg.Path, m.serveStreamable)
	}
	if m.cfg.Era.HTTPSSE {
		mux.HandleFunc("GET /sse", m.serveSSEStream)
		mux.HandleFunc("POST /messages", m.serveSSEMessage)
	}
	if m.oauth != nil {
		m.registerOAuth(mux)
	}
	return mux
}

// listen registers a notification listener and returns its remover.
func (m *Mock) listen(fn func(*Message)) func() {
	m.hubMu.Lock()
	m.hubNext++
	id := m.hubNext
	m.listeners[id] = fn
	m.hubMu.Unlock()
	return func() {
		m.hubMu.Lock()
		delete(m.listeners, id)
		m.hubMu.Unlock()
	}
}

func (m *Mock) listenerCount() int {
	m.hubMu.Lock()
	defer m.hubMu.Unlock()
	return len(m.listeners)
}

// Broadcast sends a server notification to every legacy session's GET stream
// (kept for Last-Event-ID replay), every 2026 subscriptions/listen stream,
// every 2024-11-05 SSE stream and the stdio peer.
func (m *Mock) Broadcast(method string, params any) {
	if params == nil {
		params = map[string]any{}
	}
	msg := notification(method, params)
	data, _ := json.Marshal(msg)
	for _, s := range m.sessions.all() {
		s.push(data)
	}
	m.hubMu.Lock()
	fns := make([]func(*Message), 0, len(m.listeners))
	for _, fn := range m.listeners {
		fns = append(fns, fn)
	}
	m.hubMu.Unlock()
	for _, fn := range fns {
		fn(msg)
	}
}

func randomBytes(n int) []byte {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic("mcpmock: crypto/rand failed: " + err.Error())
	}
	return b
}

func randomID() string { return hex.EncodeToString(randomBytes(16)) }
