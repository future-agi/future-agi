package restmcp

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/protocol"
)

// ServerName is the runtime's serverInfo name.
const ServerName = "agentcc-connectors"

// Defaults for Options.
const (
	DefaultPageSize = 50
	maxRequestBytes = 4 << 20
	listTTL         = 5 * time.Minute
)

// ServedVersions are the MCP revisions the runtime serves, newest first:
// the stateless 2026-07-28, then the legacy revisions an initialize may
// negotiate (2025-11-25 first; 2025-06-18 and 2025-03-26 for older
// clients, with the same tools and results).
var ServedVersions = []string{protocol.Version2026, protocol.Version20251125, protocol.Version20250618, protocol.Version20250326}

// legacyVersions are ServedVersions without the stateless one.
var legacyVersions = ServedVersions[1:]

// Options configure a Server. They come from the operator only.
type Options struct {
	// Replay maps a vendor origin (https://host) to a loopback base URL that
	// answers in its place (agentcc-cassette replay, for conformance runs and
	// scenarios). Any other target is refused.
	Replay map[string]string
	// TLSConfig is the vendor client's TLS configuration; nil means the
	// system roots.
	TLSConfig *tls.Config
	// VendorTimeout bounds one vendor call; 0 means DefaultVendorTimeout.
	VendorTimeout time.Duration
	// MaxVendorBytes bounds one vendor answer as read; 0 means
	// DefaultMaxVendorBytes.
	MaxVendorBytes int64
	// PageSize is how many tools one tools/list page holds; 0 means
	// DefaultPageSize.
	PageSize int
	// Version is serverInfo.version.
	Version string
	// Logger receives one line per request; nil means slog.Default().
	Logger *slog.Logger
}

// Server serves every App at its Path.
type Server struct {
	apps     map[string]*App
	vendor   *vendorClient
	log      *slog.Logger
	info     json.RawMessage
	pageSize int
}

// New builds a server for apps.
func New(apps []*App, o Options) (*Server, error) {
	vc, err := newVendorClient(o)
	if err != nil {
		return nil, err
	}
	s := &Server{apps: map[string]*App{}, vendor: vc, log: o.Logger, pageSize: o.PageSize}
	if s.log == nil {
		s.log = slog.Default()
	}
	if s.pageSize <= 0 {
		s.pageSize = DefaultPageSize
	}
	version := o.Version
	if version == "" {
		version = "dev"
	}
	s.info, _ = json.Marshal(map[string]string{"name": ServerName, "version": version})
	for _, a := range apps {
		if s.apps[a.Path] != nil {
			return nil, fmt.Errorf("%s is served twice", a.Path)
		}
		s.apps[a.Path] = a
	}
	return s, nil
}

// ServeHTTP answers MCP at each app's path, POST only. It authenticates
// before it reads the body: every request, initialize and server/discover
// included, must carry the app's credential. Every request gets one log
// line, the ones refused before dispatch included.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	app := s.apps[r.URL.Path]
	refuse := func(status int, id json.RawMessage, code int, message, outcome string, data json.RawMessage) {
		writeError(w, status, id, code, message, data)
		s.log.Info("connectors request", "app", appID(app), "status", status, "outcome", outcome,
			"duration_ms", time.Since(start).Milliseconds())
	}
	defer func() {
		if p := recover(); p != nil {
			s.log.Error("connectors request panicked", "app", appID(app), "panic_type", fmt.Sprintf("%T", p))
			writeError(w, http.StatusInternalServerError, nil, codeInternal, "internal error", nil)
		}
	}()
	if app == nil {
		refuse(http.StatusNotFound, nil, codeInvalidRequest, "no connector is served at this path", "no_app", nil)
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		refuse(http.StatusMethodNotAllowed, nil, codeInvalidRequest,
			"POST only: this server keeps no session and opens no stream", "method_not_allowed", nil)
		return
	}
	if !protocol.OriginOK(r.Header.Get("Origin"), r.Host, nil, nil) {
		refuse(http.StatusForbidden, nil, codeInvalidRequest, "the Origin header is not allowed", "origin_refused", nil)
		return
	}
	cred, ch := app.cred.extract(r)
	if ch != nil {
		w.Header().Set("WWW-Authenticate", ch.header)
		refuse(http.StatusUnauthorized, nil, codeInvalidRequest, ch.message, "unauthenticated", nil)
		return
	}
	msg, bad := readMessage(r)
	if bad != nil {
		refuse(bad.status, nil, bad.code, bad.message, "bad_request", nil)
		return
	}
	era := protocol.Classify(msg.Method, msg.Params, r.Header)
	if perr := s.checkEra(app, msg, era, r); perr != nil {
		refuse(perr.HTTPStatus, msg.ID, perr.Code, perr.Message, "era_refused", perr.Data)
		return
	}
	switch {
	case msg.isResponse():
		if era.Stateless {
			refuse(http.StatusBadRequest, msg.ID, codeInvalidRequest, "this server sends no requests, so it takes no responses",
				"bad_request", nil)
			return
		}
		w.WriteHeader(http.StatusAccepted)
		return
	case msg.isNotification():
		w.WriteHeader(http.StatusAccepted)
		return
	}
	s.dispatch(w, r, app, msg, era, cred, start)
}

// badMessage is a body readMessage refuses.
type badMessage struct {
	status, code int
	message      string
}

// readMessage reads one JSON-RPC message; a batch, a body over the limit or
// a non-JSON body is refused.
func readMessage(r *http.Request) (*message, *badMessage) {
	if !protocol.ContentTypeOK(r.Header) {
		return nil, &badMessage{http.StatusUnsupportedMediaType, codeInvalidRequest, "Content-Type must be application/json"}
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, maxRequestBytes+1))
	if err != nil || len(body) > maxRequestBytes {
		return nil, &badMessage{http.StatusRequestEntityTooLarge, codeInvalidRequest, "the body is unreadable or over 4 MiB"}
	}
	if t := bytes.TrimSpace(body); len(t) > 0 && t[0] == '[' {
		return nil, &badMessage{http.StatusBadRequest, codeInvalidRequest, "JSON-RPC batches are not served"}
	}
	var msg message
	if err := json.Unmarshal(body, &msg); err != nil || msg.JSONRPC != "2.0" || (msg.Method == "" && len(msg.ID) == 0) {
		return nil, &badMessage{http.StatusBadRequest, codeParse, "want one JSON-RPC 2.0 message"}
	}
	return &msg, nil
}

// checkEra applies the era's rules: the served versions, Accept, and on the
// stateless era the per-request _meta and the mirrored headers.
func (s *Server) checkEra(app *App, msg *message, era protocol.Era, r *http.Request) *protocol.Error {
	if perr := era.Validate(ServedVersions); perr != nil {
		return perr
	}
	if msg.Method != "" && !msg.isNotification() && !protocol.AcceptOK(r.Header, era.Stateless) {
		return &protocol.Error{HTTPStatus: http.StatusNotAcceptable, Code: codeInvalidRequest,
			Message: "Accept must list application/json and text/event-stream"}
	}
	if !era.Stateless || msg.isResponse() {
		return nil
	}
	return protocol.CheckHeaders(r.Header, msg.Method, msg.Params, era.Meta, func(name string) (json.RawMessage, bool) {
		if t := app.Tool(name); t != nil {
			return t.schema.listed, true
		}
		return nil, false
	})
}

func appID(a *App) string {
	if a == nil {
		return ""
	}
	return a.ID
}
