package server

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
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
			CheckOrigin: makeOriginChecker(cfg.AllowedOrigins),
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
	if rh.keyStore != nil {
		if rawKey == "" {
			http.Error(w, `{"error":{"message":"API key is required","type":"authentication_error"}}`, http.StatusUnauthorized)
			return
		}
		apiKey := rh.keyStore.Authenticate(rawKey)
		if apiKey == nil {
			http.Error(w, `{"error":{"message":"Invalid API key","type":"authentication_error"}}`, http.StatusUnauthorized)
			return
		}
		if id, ok := apiKey.Metadata["org_id"]; ok && id != "" {
			orgID = id
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
		http.Error(w, fmt.Sprintf(`{"error":{"message":"Failed to get provider URL: %s","type":"server_error"}}`, err), http.StatusBadGateway)
		return
	}
	providerHeaders, err := rp.RealtimeHeaders(model)
	if err != nil {
		rh.tracker.Release(orgID)
		http.Error(w, fmt.Sprintf(`{"error":{"message":"Failed to get provider headers: %s","type":"server_error"}}`, err), http.StatusBadGateway)
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
	providerConn, _, err := dialer.Dial(providerURL, providerHeaders)
	if err != nil {
		rh.tracker.Release(orgID)
		closeMsg := websocket.FormatCloseMessage(4502, "Unable to connect to upstream provider: "+err.Error())
		clientConn.WriteControl(websocket.CloseMessage, closeMsg, time.Now().Add(2*time.Second))
		clientConn.Close()
		rh.logger.Error("upstream connection failed", "model", model, "error", err)
		return
	}

	// Create session.
	requestID := models.GetRequestID(r.Context())
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
