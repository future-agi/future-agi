package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// A chat request to "<provider>/<model>" on a native Anthropic provider is
// translated, and its adapter drops a first "/" segment on every call. The
// registry routes the match with the id the adapter sends
// (C26Q-COUNT-ROUTE-PREFIX), so eligibility judges the id the host gets, and
// that id must stay cut exactly once: an id with a slash of its own is left
// for the adapter to cut, never cut by both.
func TestChatPrefixMatchSendsTheHostTheModelCutOnce(t *testing.T) {
	for _, tc := range []struct {
		name, model, wantUpstream, wantJudged string
	}{
		{name: "bare id", model: "alpha/route-model", wantUpstream: "route-model", wantJudged: "alpha route-model"},
		{name: "id with a slash of its own", model: "alpha/vendor/route-model", wantUpstream: "vendor/route-model"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstreamModels := make(chan string, 4)
			up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body struct {
					Model string `json:"model"`
				}
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Errorf("decode synthetic request: %v", err)
				}
				upstreamModels <- body.Model
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprintf(w, `{"id":"msg_prefix","type":"message","role":"assistant","model":%q,"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":3,"output_tokens":1}}`, body.Model)
			}))
			t.Cleanup(up.Close)

			cfg := config.DefaultConfig()
			off := false
			cfg.Providers["alpha"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic-key", APIFormat: "anthropic", Models: []string{"listed-model"}, AutoDiscover: &off, Local: &off}
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "prefix-test", Key: "sk-synthetic-prefix", Owner: "synthetic-user", KeyType: "internal"}}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			keys := auth.NewKeyStore(cfg.Auth)
			srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)
			countRouteCleanup(t, srv)

			var judged []string
			srv.handlers.hookObserver = func(hook, detail string, _ *models.RequestContext) {
				if hook == hookCheckEligibility {
					judged = append(judged, detail)
				}
			}
			body := fmt.Sprintf(`{"model":%q,"max_tokens":16,"messages":[{"role":"user","content":"hello"}]}`, tc.model)
			r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
			r.Header.Set("Authorization", "Bearer sk-synthetic-prefix")
			r.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, r)
			if w.Code != http.StatusOK {
				t.Fatalf("chat %s = %d %s", tc.model, w.Code, w.Body)
			}
			select {
			case got := <-upstreamModels:
				if got != tc.wantUpstream {
					t.Errorf("host got model %q, want %q", got, tc.wantUpstream)
				}
			default:
				t.Fatal("successful response without the synthetic provider call")
			}
			if len(judged) == 0 {
				t.Fatal("chat bypassed route eligibility")
			}
			if tc.wantJudged != "" && judged[len(judged)-1] != tc.wantJudged {
				t.Errorf("eligibility judged %v, want last %q", judged, tc.wantJudged)
			}
		})
	}
}
