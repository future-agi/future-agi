package server

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/realtime"
)

// RealtimeHandler handles WebSocket-based realtime API sessions.
type RealtimeHandler struct {
	tracker            *realtime.SessionTracker
	registry           *providers.Registry
	keyStore           *auth.KeyStore
	maxSessionDuration time.Duration
	pingInterval       time.Duration
	pongTimeout        time.Duration
	readBufferSize     int
	writeBufferSize    int
	maxMessageSize     int64
	upgrader           websocket.Upgrader
	logger             *slog.Logger
	bindErrorRedactor  func(*models.RequestContext)
}

// NewRealtimeHandler creates a new handler for the realtime API.
func NewRealtimeHandler(tracker *realtime.SessionTracker, registry *providers.Registry, keyStore *auth.KeyStore, cfg realtimeHandlerConfig) *RealtimeHandler {
	return &RealtimeHandler{
		tracker:            tracker,
		registry:           registry,
		keyStore:           keyStore,
		maxSessionDuration: cfg.MaxSessionDuration,
		pingInterval:       cfg.PingInterval,
		pongTimeout:        cfg.PongTimeout,
		readBufferSize:     cfg.ReadBufferSize,
		writeBufferSize:    cfg.WriteBufferSize,
		maxMessageSize:     cfg.MaxMessageSize,
		upgrader: websocket.Upgrader{
			ReadBufferSize:  cfg.ReadBufferSize,
			WriteBufferSize: cfg.WriteBufferSize,
			CheckOrigin:     makeOriginChecker(cfg.AllowedOrigins),
		},
		logger: slog.Default(),
	}
}

// realtimeHandlerConfig holds realtime handler configuration.
type realtimeHandlerConfig struct {
	MaxSessionDuration time.Duration
	PingInterval       time.Duration
	PongTimeout        time.Duration
	ReadBufferSize     int
	WriteBufferSize    int
	MaxMessageSize     int64
	AllowedOrigins     []string
}

// makeOriginChecker returns a CheckOrigin function for WebSocket upgrades.
// If allowedOrigins contains "*", all origins are allowed. Otherwise, the
// origin must match one of the configured values. Non-browser clients
// (empty Origin header) are always allowed.
func makeOriginChecker(allowedOrigins []string) func(*http.Request) bool {
	// If no origins configured or wildcard, allow all.
	for _, o := range allowedOrigins {
		if o == "*" {
			return func(r *http.Request) bool { return true }
		}
	}
	allowed := make(map[string]bool, len(allowedOrigins))
	for _, o := range allowedOrigins {
		allowed[o] = true
	}
	return func(r *http.Request) bool {
		origin := r.Header.Get("Origin")
		if origin == "" {
			return true // non-browser client
		}
		return allowed[origin]
	}
}

// HandleRealtime handles GET /v1/realtime — WebSocket upgrade and session management.
func (rh *RealtimeHandler) HandleRealtime(w http.ResponseWriter, r *http.Request) {
	requestID := models.GetRequestID(r.Context())
	if requestID == "" {
		requestID = middleware.NewID()
		r = r.WithContext(models.WithRequestID(r.Context(), requestID))
	}
	model := r.URL.Query().Get("model")
	if model == "" {
		http.Error(w, `{"error":{"message":"model query parameter is required","type":"invalid_request_error"}}`, http.StatusBadRequest)
		return
	}

	// Extract API key for auth.
	rawKey := ""
	if authHeader := r.Header.Get("Authorization"); authHeader != "" && strings.HasPrefix(authHeader, "Bearer ") {
		rawKey = authHeader[7:]
	}
	if rawKey == "" {
		rawKey = r.URL.Query().Get("api_key")
	}

	// Validate API key through the key store.
	orgID := "default"
	var apiKey *auth.APIKey
	if rh.keyStore != nil {
		if rawKey == "" {
			http.Error(w, `{"error":{"message":"API key is required","type":"authentication_error"}}`, http.StatusUnauthorized)
			return
		}
		if rh.keyStore.ConfigKeyPending(rawKey) {
			models.WriteError(w, &models.APIError{Status: http.StatusServiceUnavailable,
				Type: models.ErrTypeServer, Code: "key_list_not_loaded", Message: "Waiting for the first control-plane key list"})
			return
		}
		apiKey = rh.keyStore.Authenticate(rawKey)
		if apiKey == nil {
			http.Error(w, `{"error":{"message":"Invalid API key","type":"authentication_error"}}`, http.StatusUnauthorized)
			return
		}
		if id, ok := apiKey.Metadata["org_id"]; ok && id != "" {
			orgID = id
		}
		// The org-confinement guard chat applies (Handlers.orgProvidersOnly):
		// a key limited to its org's providers never gets the gateway's own
		// provider. A session is relayed with no budget, credits, guardrails
		// or request log, so it is refused before any provider is resolved
		// or a session slot taken; confinement through the org's own
		// provider build is for the release that ships a realtime provider.
		if apiKey.KeyType != "internal" && !middleware.IsLicenseAuthorized(r.Context()) {
			models.WriteError(w, models.ErrForbidden(fmt.Sprintf("model %q is not available for this API key", model)))
			return
		}
	}

	// Check concurrent session limit.
	if !rh.tracker.TryAcquire(orgID) {
		http.Error(w, `{"error":{"message":"Maximum concurrent realtime sessions exceeded","type":"rate_limit_error"}}`, http.StatusTooManyRequests)
		return
	}

	// Resolve provider.
	provider, err := rh.registry.Resolve(model)
	if err != nil {
		rh.tracker.Release(orgID)
		http.Error(w, fmt.Sprintf(`{"error":{"message":"No provider for model %q","type":"not_found_error"}}`, model), http.StatusNotFound)
		return
	}
	if apiKey != nil && apiKey.KeyType != "internal" && !apiKey.CanAccessProvider(provider.ID()) {
		rh.tracker.Release(orgID)
		models.WriteError(w, errKeyProviderDenied(provider.ID()))
		return
	}

	rp, ok := provider.(providers.RealtimeProvider)
	if !ok {
		rh.tracker.Release(orgID)
		http.Error(w, `{"error":{"message":"Provider does not support realtime API","type":"not_found_error"}}`, http.StatusNotImplemented)
		return
	}

	// Get upstream connection params.
	providerURL, err := rp.RealtimeURL(model)
	if err != nil {
		rh.tracker.Release(orgID)
		models.WriteError(w, rh.providerSetupFailure(requestID, orgID, provider.ID(), "url", err))
		return
	}
	providerHeaders, err := rp.RealtimeHeaders(model)
	if err != nil {
		rh.tracker.Release(orgID)
		models.WriteError(w, rh.providerSetupFailure(requestID, orgID, provider.ID(), "headers", err))
		return
	}

	// Upgrade client connection.
	clientConn, err := rh.upgrader.Upgrade(w, r, nil)
	if err != nil {
		rh.tracker.Release(orgID)
		rh.logger.Error("websocket upgrade failed", "error", err)
		return
	}

	// Connect to upstream provider.
	dialer := websocket.Dialer{
		HandshakeTimeout: 10 * time.Second,
		Subprotocols:     rp.RealtimeSubprotocols(),
	}
	errorCredentials := upstreamDialCredentials(providerURL, providerHeaders, dialer.Subprotocols...)
	providerConn, _, err := dialer.Dial(providerURL, providerHeaders)
	if err != nil {
		err = common.ExactError(err, errorCredentials...)
		rh.tracker.Release(orgID)
		failure := rh.providerSetupFailure(requestID, orgID, provider.ID(), "dial", err)
		// The gateway's request id is a 26-byte ULID. Keep this typed,
		// actionable reason below the WebSocket control-frame limit.
		closeMsg := websocket.FormatCloseMessage(4502, failure.Message)
		clientConn.WriteControl(websocket.CloseMessage, closeMsg, time.Now().Add(2*time.Second))
		clientConn.Close()
		return
	}

	// Create session.
	sessionID := fmt.Sprintf("rt-%s", requestID)
	session := realtime.NewSession(sessionID, requestID, orgID, model, provider.ID(), clientConn, providerConn)
	rh.tracker.Register(session)

	// Session timeout.
	go func() {
		select {
		case <-time.After(rh.maxSessionDuration):
			if !session.IsClosed() {
				session.Close("timeout")
			}
		case <-session.StopChan():
		}
	}()

	// Send gateway event.
	gatewayEvent, _ := json.Marshal(map[string]string{
		"type":       "gateway.session.created",
		"session_id": sessionID,
		"request_id": requestID,
	})
	clientConn.WriteMessage(websocket.TextMessage, gatewayEvent)

	// Start relay (blocks until session is done).
	relayConfig := realtime.RelayConfig{
		ChannelBufferSize: 64,
		PingInterval:      rh.pingInterval,
		PongTimeout:       rh.pongTimeout,
		MaxMessageSize:    rh.maxMessageSize,
	}
	relay := realtime.NewRelay(session, relayConfig, rh.logger)
	relay.Start()

	// Post-session cleanup.
	session.Close("relay_done")
	usage := session.Usage.Snapshot()

	rh.logger.Info("realtime session ended",
		"session_id", sessionID,
		"model", model,
		"provider", provider.ID(),
		"duration", session.Duration(),
		"input_tokens", usage.InputTokens,
		"output_tokens", usage.OutputTokens,
		"messages", usage.TotalMessages,
		"close_reason", session.CloseReason,
	)

	rh.tracker.Release(orgID)
	rh.tracker.Unregister(sessionID)
}

// upstreamDialCredentials snapshots the URL, headers and credential-bearing
// subprotocols of this dial, never a later provider/configuration lookup. In
// particular a url.Error may include a query credential in an encoded spelling.
func upstreamDialCredentials(rawURL string, headers http.Header, protocols ...string) []string {
	u, _ := url.Parse(rawURL)
	credentials := common.ErrorCredentials(&http.Request{URL: u, Header: headers})
	if _, query, ok := strings.Cut(rawURL, "?"); ok {
		query, _, _ = strings.Cut(query, "#")
		values, _ := url.ParseQuery(query)
		for name, values := range values {
			name = strings.ToLower(name)
			if strings.Contains(name, "key") || strings.Contains(name, "token") || strings.Contains(name, "secret") || strings.Contains(name, "signature") || strings.Contains(name, "credential") || name == "authorization" {
				credentials = append(credentials, values...)
			}
		}
	}
	allProtocols := append([]string(nil), protocols...)
	for _, value := range headers.Values("Sec-WebSocket-Protocol") {
		allProtocols = append(allProtocols, strings.Split(value, ",")...)
	}
	for _, protocol := range allProtocols {
		protocol = strings.TrimSpace(protocol)
		if token, ok := strings.CutPrefix(protocol, "openai-insecure-api-key."); ok {
			credentials = append(credentials, token, protocol)
		}
	}
	return credentials
}

// Realtime resolves only the global registry, never an org provider instance.
// The caller's key type is not ownership of that provider credential. Project
// its diagnostic exactly as a platform-owned provider failure before logging;
// neither the HTTP refusal nor the close frame contains any provider value.
func (rh *RealtimeHandler) providerSetupFailure(requestID, orgID, providerID, stage string, err error) *models.APIError {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID, rc.Provider, rc.CredentialSource = requestID, providerID, "platform"
	rc.Metadata["key_org_id"], rc.Metadata["org_id"] = orgID, orgID
	if rh.bindErrorRedactor != nil {
		rh.bindErrorRedactor(rc)
	}
	code := "realtime_provider_" + stage + "_failed"
	logCopy := providerFailure(rc, models.ErrUpstreamProvider(0, err.Error()))
	rh.logger.Error("realtime provider setup failed", "request_id", requestID, "code", code, "error", logCopy)
	return &models.APIError{
		Status: http.StatusBadGateway, Type: models.ErrTypeUpstream, Code: code,
		Message: code + ": retry; contact support (request_id: " + requestID + ")",
	}
}
