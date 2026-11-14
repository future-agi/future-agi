package server

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Cartesia names the model in each synthesis request rather than in the URL, so the gateway
// learns it only from the messages. A key's model restrictions must hold for those requests: a
// model the key may not use never reaches Cartesia, whether it is the connection's first
// request or a later one on a connection that started with an allowed model.
func TestCartesiaSynthesisEnforcesTheKeysModelRestrictions(t *testing.T) {
	cases := []struct {
		name      string
		requests  []string
		forwarded []string
	}{
		{"first request names a disallowed model", []string{"sonic-3"}, nil},
		{"connection switches to a disallowed model", []string{"sonic-2", "sonic-3", "sonic-2"}, []string{"sonic-2"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var mu sync.Mutex
			var upstreamModels []string
			upgrader := websocket.Upgrader{}
			cartesia := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, err := upgrader.Upgrade(w, r, nil)
				if err != nil {
					return
				}
				defer conn.Close()
				for {
					_, data, err := conn.ReadMessage()
					if err != nil {
						return
					}
					var m struct {
						ModelID string `json:"model_id"`
					}
					json.Unmarshal(data, &m)
					mu.Lock()
					upstreamModels = append(upstreamModels, m.ModelID)
					mu.Unlock()
					conn.WriteMessage(websocket.TextMessage, []byte(`{"type":"done"}`))
				}
			}))
			defer cartesia.Close()

			cfg := config.DefaultConfig()
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{
				Name:      "internal-service",
				Key:       "sk-internal",
				Owner:     "platform",
				KeyType:   "internal",
				Models:    []string{"cartesia/sonic-2"},
				Providers: []string{"cartesia"},
			}}
			cfg.Providers["cartesia"] = config.ProviderConfig{
				BaseURL:   cartesia.URL,
				APIKey:    "platform-cartesia-key",
				APIFormat: "cartesia",
			}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatalf("creating registry: %v", err)
			}
			keyStore := auth.NewKeyStore(cfg.Auth)
			engine := pipeline.NewEngine(authplugin.New(keyStore, true))
			srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()

			client, _, err := websocket.DefaultDialer.Dial(
				"ws"+strings.TrimPrefix(gateway.URL, "http")+"/cartesia/tts/websocket",
				http.Header{"X-API-Key": {"sk-internal"}})
			if err != nil {
				t.Fatalf("dialing gateway: %v", err)
			}
			defer client.Close()

			var closeErr *websocket.CloseError
			for _, model := range tc.requests {
				request := `{"model_id":"` + model + `","transcript":"hello","voice":{"mode":"id","id":"v"}}`
				if err := client.WriteMessage(websocket.TextMessage, []byte(request)); err != nil {
					break
				}
				client.SetReadDeadline(time.Now().Add(5 * time.Second))
				if _, _, err := client.ReadMessage(); err != nil {
					if !errors.As(err, &closeErr) {
						t.Fatalf("reading the reply to %s: %v", model, err)
					}
					break
				}
			}

			if closeErr == nil || closeErr.Code != websocket.ClosePolicyViolation {
				t.Errorf("client close = %v, want a policy-violation close for sonic-3", closeErr)
			} else if !strings.Contains(closeErr.Text, "cartesia/sonic-3") {
				t.Errorf("close reason %q does not name the refused model", closeErr.Text)
			}
			mu.Lock()
			defer mu.Unlock()
			if strings.Join(upstreamModels, ",") != strings.Join(tc.forwarded, ",") {
				t.Errorf("Cartesia received models %v, want %v", upstreamModels, tc.forwarded)
			}
		})
	}
}
