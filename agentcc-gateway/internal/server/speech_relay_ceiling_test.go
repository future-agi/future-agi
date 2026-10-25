package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// server.max_request_timeout bounds a request, and a relayed speech session is
// one request that becomes a WebSocket. The session is not held at the
// ceiling: once upgraded it runs until either side closes it, an idle
// timeout, or the relay's own maximum session length. This pins that a
// session keeps relaying past the ceiling, through the real server chain.
func TestSpeechRelaySessionOutlivesTheRequestCeiling(t *testing.T) {
	upgrader := websocket.Upgrader{}
	cartesia := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
			conn.WriteMessage(websocket.TextMessage, []byte(`{"type":"done"}`))
		}
	}))
	defer cartesia.Close()

	const ceiling = 300 * time.Millisecond
	cfg := config.DefaultConfig()
	cfg.Server.MaxRequestTimeout = ceiling
	cfg.Server.DefaultRequestTimeout = ceiling
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "internal-service", Key: "sk-internal-ceiling", Owner: "platform", KeyType: "internal",
		Providers: []string{"cartesia"},
	}}
	cfg.Providers["cartesia"] = config.ProviderConfig{BaseURL: cartesia.URL, APIKey: "platform-cartesia-key", APIFormat: "cartesia"}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	gateway := httptest.NewServer(srv.httpServer.Handler)
	defer gateway.Close()

	client, _, err := websocket.DefaultDialer.Dial(
		"ws"+strings.TrimPrefix(gateway.URL, "http")+"/cartesia/tts/websocket",
		http.Header{"X-API-Key": {"sk-internal-ceiling"}})
	if err != nil {
		t.Fatalf("dialing gateway: %v", err)
	}
	defer client.Close()

	start := time.Now()
	for i := 0; time.Since(start) < 3*ceiling; i++ {
		request := `{"model_id":"sonic-2","transcript":"hello","voice":{"mode":"id","id":"v"}}`
		if err := client.WriteMessage(websocket.TextMessage, []byte(request)); err != nil {
			t.Fatalf("write %d after %v: %v", i, time.Since(start), err)
		}
		client.SetReadDeadline(time.Now().Add(5 * time.Second))
		if _, _, err := client.ReadMessage(); err != nil {
			t.Fatalf("read %d after %v (ceiling %v): %v", i, time.Since(start), ceiling, err)
		}
		time.Sleep(ceiling / 4)
	}
}

// The relay's own limits, as release note N12 names them: a session ends when a side sends
// neither a message nor a ping answer for 10 minutes, and after 2 hours in all. IF THIS FAILS
// because a limit changed, update N12 in docs/gateway/release-notes.md with it.
func TestSpeechRelayLimitsAreTheOnesTheReleaseNoteNames(t *testing.T) {
	if speechIdleTimeout != 10*time.Minute || speechMaxSession != 2*time.Hour {
		t.Errorf("speech relay idle timeout %v, maximum session %v; N12 says 10m0s and 2h0m0s", speechIdleTimeout, speechMaxSession)
	}
}
