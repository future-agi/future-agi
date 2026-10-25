package server

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/speech"
)

// The speech vendors' relayed WebSocket APIs (GET /deepgram/v1/listen,
// /cartesia/tts/websocket, ...) are an API surface outside /v1, so KeyAuth
// gates them like /v1beta: a caller without a valid gateway key gets 401 at
// the gate, before the relay handler says whether a vendor is configured or
// dials it, and the gate applies the key's and the org's IP rules there. The
// gate reads the key wherever the vendor's own SDK sends it, as the relay
// handler does (middleware.SpeechRequestAPIKey).

const (
	speechGateKey      = "sk-agentcc-speech-gate-0001"
	speechGateKeyIPs   = "sk-agentcc-speech-gate-ips-0001" // allowed_ips 203.0.113.0/24
	speechGateIPInList = "203.0.113.7"
	speechGateIPOut    = "192.0.2.99"
)

// newSpeechGateServer builds the gateway with request auth on and, when
// vendors is set, a Deepgram and a Cartesia provider whose upstream counts
// every request it gets.
func newSpeechGateServer(t *testing.T, vendors bool) (*Server, *atomic.Int32) {
	t.Helper()
	var dials atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Count the relayed paths only, not the registry's discovery calls.
		for _, ep := range speech.Endpoints() {
			if r.URL.Path == ep.Path {
				dials.Add(1)
			}
		}
		w.WriteHeader(http.StatusBadRequest)
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Admin.Token = canonAdminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "speech", Key: speechGateKey, Owner: "platform", KeyType: "internal"},
		{Name: "speech-ips", Key: speechGateKeyIPs, Owner: "platform", KeyType: "internal", AllowedIPs: []string{"203.0.113.0/24"}},
	}
	if vendors {
		cfg.Providers["deepgram"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "platform-deepgram", APIFormat: "deepgram"}
		cfg.Providers["cartesia"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "platform-cartesia", APIFormat: "cartesia"}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, metrics.NewRegistry(), testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv, &dials
}

func serveSpeech(t *testing.T, srv *Server, target, remoteIP string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, target, bytes.NewReader(nil))
	if remoteIP != "" {
		req.RemoteAddr = remoteIP + ":40000"
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	ctx, cancel := context.WithTimeout(req.Context(), 2*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	return w
}

func speechRoutes(t *testing.T) []string {
	t.Helper()
	var out []string
	for _, ep := range speech.Endpoints() {
		out = append(out, "/"+ep.Vendor+ep.Path)
	}
	if len(out) < 5 {
		t.Fatalf("speech.Endpoints() = %v, want the Deepgram and Cartesia relays", out)
	}
	return out
}

// No key, a wrong key in each place a speech SDK sends one, or the admin
// token: 401 with a Bearer challenge at the gate, whether or not any speech
// vendor is configured, and the vendor is never dialed.
func TestSpeechRelayRoutesRefuseACallerWithoutAKeyAtTheGate(t *testing.T) {
	for _, vendors := range []bool{false, true} {
		srv, dials := newSpeechGateServer(t, vendors)
		for _, route := range speechRoutes(t) {
			for label, tc := range map[string]struct {
				target  string
				headers map[string]string
			}{
				"no key":              {route, nil},
				"wrong token":         {route, map[string]string{"Authorization": "Token sk-not-a-key"}},
				"wrong bearer":        {route, map[string]string{"Authorization": "Bearer sk-not-a-key"}},
				"wrong x-api-key":     {route, map[string]string{"X-API-Key": "sk-not-a-key"}},
				"wrong api_key query": {route + "?api_key=sk-not-a-key", nil},
				"admin token":         {route, map[string]string{"Authorization": "Bearer " + canonAdminToken}},
				"non-key scheme":      {route, map[string]string{"Authorization": "Basic " + speechGateKey}},
				"subpath":             {route + "/x", nil},
				"double slash":        {"/" + route, nil},
			} {
				w := serveSpeech(t, srv, tc.target, "", tc.headers)
				requireKeyChallenge(t, w, "GET "+tc.target+" with "+label)
				if strings.Contains(w.Body.String(), "provider") {
					t.Fatalf("GET %s with %s: the refusal names a provider: %s", tc.target, label, w.Body.String())
				}
			}
		}
		if n := dials.Load(); n != 0 {
			t.Fatalf("vendors=%v: the speech vendor was contacted %d times by callers without a key", vendors, n)
		}
	}
}

// A key holder passes the gate with the key wherever the vendor's SDK puts
// it: a plain GET then reaches the relay handler, which asks for a WebSocket.
func TestSpeechRelayGateAdmitsAKeyWhereverTheVendorSDKSendsIt(t *testing.T) {
	srv, _ := newSpeechGateServer(t, true)
	for label, tc := range map[string]struct {
		route   string
		headers map[string]string
		query   string
	}{
		"deepgram token":     {"/deepgram/v1/listen", map[string]string{"Authorization": "Token " + speechGateKey}, ""},
		"bearer":             {"/deepgram/v1/speak", map[string]string{"Authorization": "Bearer " + speechGateKey}, ""},
		"cartesia x-api-key": {"/cartesia/tts/websocket", map[string]string{"X-API-Key": speechGateKey}, ""},
		"cartesia api_key":   {"/cartesia/stt/websocket", nil, "?api_key=" + speechGateKey},
	} {
		w := serveSpeech(t, srv, tc.route+tc.query, "", tc.headers)
		if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "websocket_required") {
			t.Fatalf("%s: GET %s: status %d, want 400 websocket_required from the relay handler. Body: %s",
				label, tc.route, w.Code, w.Body.String())
		}
	}
}

// The gate applies the key's allowed_ips on the speech routes, as on /v1.
func TestSpeechRelayGateAppliesTheKeysAllowedIPs(t *testing.T) {
	srv, dials := newSpeechGateServer(t, true)
	headers := map[string]string{"Authorization": "Token " + speechGateKeyIPs}

	w := serveSpeech(t, srv, "/deepgram/v1/listen", speechGateIPOut, headers)
	if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), "not authorized from this IP address") {
		t.Fatalf("from %s: status %d, want 403 from the key's allowed_ips. Body: %s", speechGateIPOut, w.Code, w.Body.String())
	}
	w = serveSpeech(t, srv, "/deepgram/v1/listen", speechGateIPInList, headers)
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "websocket_required") {
		t.Fatalf("from %s: status %d, want the relay handler's 400. Body: %s", speechGateIPInList, w.Code, w.Body.String())
	}
	if n := dials.Load(); n != 0 {
		t.Fatalf("the speech vendor was contacted %d times", n)
	}
}
