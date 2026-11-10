package server

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/speech"
	"github.com/futureagi/agentcc-gateway/internal/realtime"
)

const (
	speechDialTimeout = 10 * time.Second
	// A voice call can run past an hour; this only bounds a session nobody closes.
	speechMaxSession = 2 * time.Hour
	// SDKs keep idle synthesis connections pooled without reading them, so they answer no pings
	// meanwhile; LiveKit's Cartesia pool reuses one for up to five idle minutes.
	speechIdleTimeout = 10 * time.Minute
)

// speechOriginAllowed admits server-side SDKs only. A browser cannot set the headers these
// routes authenticate with, and refusing pages keeps a gateway key from being embedded in one.
func speechOriginAllowed(r *http.Request) bool { return r.Header.Get("Origin") == "" }

var speechUpgrader = websocket.Upgrader{
	ReadBufferSize:  32 << 10,
	WriteBufferSize: 32 << 10,
	CheckOrigin:     speechOriginAllowed,
}

var speechDialer = websocket.Dialer{
	Proxy:            http.ProxyFromEnvironment,
	HandshakeTimeout: speechDialTimeout,
	ReadBufferSize:   32 << 10,
	WriteBufferSize:  32 << 10,
}

// SpeechRelay serves a speech vendor's own streaming API at /{vendor}{path}, for example
// GET /deepgram/v1/listen. The client authenticates with its gateway key wherever the vendor's
// SDK puts its key; the gateway connects to the vendor with the platform's key, relays frames
// unchanged, and records the session's usage when it ends.
func (h *Handlers) SpeechRelay(vendor, path string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		h.relaySpeech(w, r, vendor, path)
	}
}

func (h *Handlers) relaySpeech(w http.ResponseWriter, r *http.Request, vendor, path string) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.RequestHeaders = cloneRequestHeaders(r)
	// Usage is only known when the session ends, so post-plugins run then, not after the dial.
	rc.IsStream = true
	rc.Metadata["client_ip"] = extractClientIP(r)
	if key := speechClientKey(r); key != "" {
		rc.Metadata["authorization"] = "Bearer " + key
	}

	provider, found := h.registry.GetProvider(vendor)
	relayer, isSpeech := provider.(providers.SpeechRelayProvider)
	if !found || !isSpeech || relayer.Vendor() != vendor {
		models.WriteError(w, models.ErrNotFound("provider_not_configured",
			fmt.Sprintf("no %s provider is configured", vendor)))
		return
	}
	if !speechOriginAllowed(r) {
		models.WriteError(w, models.ErrForbidden("speech relay is for server-side clients; browser origins are refused"))
		return
	}
	if !websocket.IsWebSocketUpgrade(r) {
		models.WriteError(w, models.ErrBadRequest("websocket_required",
			fmt.Sprintf("/%s%s is the vendor's WebSocket API; connect with a WebSocket client", vendor, path)))
		return
	}
	session, err := relayer.Open(path, r.URL.Query(), r.Header)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	rc.Provider = provider.ID()
	rc.Model = session.Model()
	rc.EndpointType = "speech_relay"
	if session.Kind == speech.Transcription {
		rc.EndpointType = "transcription_relay"
	}

	h.peekKeyOrgID(rc)
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgRateLimitOverrides(orgCfg, rc)
	h.applyOrgBudgetOverrides(orgCfg, rc)
	h.applyOrgCostTrackingOverrides(orgCfg, rc)
	h.applyOrgIPACLOverrides(orgCfg, rc)
	h.applyOrgAlertingOverrides(orgCfg, rc)
	h.applyOrgPrivacyOverrides(orgCfg, rc)
	h.applyOrgAuditOverrides(orgCfg, rc)
	h.applyOrgModelDatabaseOverrides(orgCfg, rc)

	var upstream *websocket.Conn
	dialCtx, cancel := context.WithTimeout(r.Context(), speechDialTimeout)
	defer cancel()
	err = h.engine.Process(dialCtx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		// The vendor is reached on the platform's credential, which customer keys must not use.
		if h.orgProvidersOnly(ctx, rc) {
			return models.ErrForbidden(fmt.Sprintf(
				"%s is relayed with the platform's credentials, which this API key cannot use", vendor))
		}
		if allowed, ok := rc.Metadata["auth_allowed_providers"]; ok && !slices.Contains(splitCSV(allowed), rc.Provider) {
			return models.ErrForbidden("API key does not have access to provider: " + rc.Provider)
		}
		if rc.Model != "" {
			if err := h.speechModelAllowed(rc, rc.Model); err != nil {
				return err
			}
		}
		conn, resp, err := speechDialer.DialContext(ctx, session.URL, session.Header)
		if err != nil {
			return speechDialError(resp, err)
		}
		upstream = conn
		return nil
	})
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	client, err := speechUpgrader.Upgrade(w, r, http.Header{"X-Agentcc-Request-Id": {rc.RequestID}})
	if err != nil {
		upstream.Close()
		rc.AddError(models.ErrBadRequest("websocket_upgrade_failed", err.Error()))
		h.engine.RunPostPlugins(context.WithoutCancel(r.Context()), rc)
		return
	}

	// Cartesia synthesis names its model in each request, so it is checked as it arrives.
	session.AdmitModel = func(model string) error {
		err := h.speechModelAllowed(rc, model)
		if err != nil {
			rc.AddError(err)
		}
		return err
	}
	relaySession := realtime.NewSession("speech-"+rc.RequestID, rc.RequestID, orgID, rc.Model, rc.Provider, client, upstream)
	expire := time.AfterFunc(speechMaxSession, func() { relaySession.Close("max_duration") })
	realtime.NewRelay(relaySession, realtime.RelayConfig{
		PingInterval:   30 * time.Second,
		PongTimeout:    10 * time.Second,
		IdleTimeout:    speechIdleTimeout,
		MaxMessageSize: 16 << 20,
		OnClientMessage: func(messageType int, data []byte) error {
			return session.ClientMessage(messageType == websocket.BinaryMessage, data)
		},
	}, slog.Default()).Start()
	expire.Stop()
	relaySession.Close("relay_done")

	if model := session.Model(); model != "" {
		rc.Model = model
	}
	rc.ResolvedModel = rc.Model
	if session.Kind == speech.Transcription {
		rc.SetMetadata("audio_seconds", formatAudioSeconds(session.AudioSeconds()))
	} else {
		rc.SetMetadata("input_characters", strconv.Itoa(session.Characters()))
	}
	if voice := session.Voice(); voice != "" {
		rc.SetMetadata("voice", voice)
	}
	rc.SetMetadata("close_reason", relaySession.CloseReason)
	h.engine.RunPostPlugins(context.WithoutCancel(r.Context()), rc)
}

// speechModelAllowed applies the key's model restrictions (its own model list and its access
// groups) to a speech model. The auth plugin checks only a model known when the request
// arrives, which a Cartesia synthesis connection never has.
func (h *Handlers) speechModelAllowed(rc *models.RequestContext, model string) error {
	if key := h.lookupRequestKey(rc); key != nil && !key.CanAccessModel(model) {
		return models.ErrForbidden("API key does not have access to model: " + model)
	}
	groups := splitCSV(rc.Metadata["key_access_groups"])
	if h.accessGroupChecker.IsEnabled() && len(groups) > 0 {
		if _, allowed := h.accessGroupChecker.Check(model, groups); !allowed {
			return models.ErrForbidden(fmt.Sprintf("Model %q is not available for this API key. %s",
				model, h.accessGroupChecker.DescribeAllowed(groups)))
		}
	}
	return nil
}

// speechClientKey reads the gateway key from wherever the vendor's own SDK puts its key:
// Deepgram's "Authorization: Token", Cartesia's X-API-Key header or api_key query parameter,
// or a Bearer token.
func speechClientKey(r *http.Request) string {
	if auth := r.Header.Get("Authorization"); auth != "" {
		for _, scheme := range []string{"Token ", "Bearer "} {
			if key, ok := strings.CutPrefix(auth, scheme); ok {
				return strings.TrimSpace(key)
			}
		}
	}
	if key := r.Header.Get("X-API-Key"); key != "" {
		return key
	}
	return r.URL.Query().Get("api_key")
}

// speechDialError reports a refused upstream handshake with the vendor's own message. A request
// the vendor rejects keeps its status; a rejected platform credential or an unreachable vendor
// is the gateway's failure.
func speechDialError(resp *http.Response, err error) error {
	if resp == nil {
		return models.ErrUpstreamProvider(http.StatusBadGateway, "connecting to the speech provider: "+err.Error())
	}
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	message := strings.TrimSpace(string(body))
	if message == "" {
		message = resp.Status
	}
	status := http.StatusBadGateway
	if resp.StatusCode >= 400 && resp.StatusCode < 500 &&
		resp.StatusCode != http.StatusUnauthorized && resp.StatusCode != http.StatusForbidden {
		status = resp.StatusCode
	}
	return &models.APIError{
		Status:  status,
		Type:    models.ErrTypeUpstream,
		Code:    fmt.Sprintf("provider_%d", resp.StatusCode),
		Message: message,
	}
}
