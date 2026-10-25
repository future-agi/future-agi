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

// A speech session that names no model is served on a model the vendor picks (Deepgram's
// default for /v1/listen and /v1/speak). A key's model restrictions hold for it: a key with a
// model list is refused such a session before the vendor is dialed, since no list can name a
// model the vendor picks, and access groups admit it only when one of the key's groups admits
// every model of the vendor ("*" or "deepgram/*"). A key without restrictions keeps the
// vendor's default.

const (
	defaultModelKeyListed  = "sk-agentcc-speech-default-listed-0001"  // models: deepgram/nova-3, cartesia/sonic-2
	defaultModelKeyOpen    = "sk-agentcc-speech-default-open-0001"    // no model list, no access groups
	defaultModelKeyVendor  = "sk-agentcc-speech-default-vendor-0001"  // access group deepgram/*
	defaultModelKeyAll     = "sk-agentcc-speech-default-all-0001"     // access group *
	defaultModelKeyLiteral = "sk-agentcc-speech-default-literal-0001" // access group deepgram/nova-3, cartesia/sonic-2
	defaultModelKeyOrg     = "sk-agentcc-speech-default-orgkey-0001"  // an organization's key (not internal)
	defaultModelKeyCartVnd = "sk-agentcc-speech-default-cartvnd-0001" // access group cartesia/*
)

type speechDefaultModelGateway struct {
	base    string
	mu      sync.Mutex
	dialed  []string // upstream paths with their query, in dial order
	upModel []string // model_id of each Cartesia synthesis message the upstream received
}

func newSpeechDefaultModelGateway(t *testing.T) *speechDefaultModelGateway {
	t.Helper()
	return newSpeechRelayGateway(t, true)
}

// newSpeechRelayGateway builds the gateway as the binary does: the key store always exists, and
// the auth plugin runs only with authOn (auth.enabled).
func newSpeechRelayGateway(t *testing.T, authOn bool) *speechDefaultModelGateway {
	t.Helper()
	g := &speechDefaultModelGateway{}
	upgrader := websocket.Upgrader{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// A relay's dial is recorded before its handshake is answered, so a client that has its
		// 101 sees it. The gateway's start-up probes (GET /v1/models) are no dial.
		if !websocket.IsWebSocketUpgrade(r) {
			http.NotFound(w, r)
			return
		}
		g.mu.Lock()
		g.dialed = append(g.dialed, r.URL.RequestURI())
		g.mu.Unlock()
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
				ModelID *string `json:"model_id"`
			}
			if json.Unmarshal(data, &m) == nil && strings.HasPrefix(r.URL.Path, "/tts/") {
				model := "<none>"
				if m.ModelID != nil {
					model = *m.ModelID
				}
				g.mu.Lock()
				g.upModel = append(g.upModel, model)
				g.mu.Unlock()
			}
			conn.WriteMessage(websocket.TextMessage, []byte(`{"type":"done"}`))
		}
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = authOn
	key := func(name, raw string, models []string, groups string) config.AuthKeyConfig {
		k := config.AuthKeyConfig{Name: name, Key: raw, Owner: "platform", KeyType: "internal",
			Providers: []string{"deepgram", "cartesia"}, Models: models}
		if groups != "" {
			k.Metadata = map[string]string{"access_groups": groups}
		}
		return k
	}
	cfg.Auth.Keys = []config.AuthKeyConfig{
		key("listed", defaultModelKeyListed, []string{"deepgram/nova-3", "cartesia/sonic-2"}, ""),
		key("open", defaultModelKeyOpen, nil, ""),
		key("vendor", defaultModelKeyVendor, nil, "speech-deepgram"),
		key("all", defaultModelKeyAll, nil, "everything"),
		key("literal", defaultModelKeyLiteral, nil, "named-speech"),
		key("cartesia-vendor", defaultModelKeyCartVnd, nil, "speech-cartesia"),
		{Name: "org", Key: defaultModelKeyOrg, Owner: "org-a", Metadata: map[string]string{"org_id": "org-a"}},
	}
	cfg.Routing.AccessGroups = config.AccessGroupsConfig{
		"speech-deepgram": {Models: []string{"deepgram/*"}},
		"speech-cartesia": {Models: []string{"cartesia/*"}},
		"everything":      {Models: []string{"*"}},
		"named-speech":    {Models: []string{"deepgram/nova-3", "cartesia/sonic-2"}},
	}
	cfg.Providers["deepgram"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "platform-deepgram", APIFormat: "deepgram"}
	cfg.Providers["cartesia"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "platform-cartesia", APIFormat: "cartesia"}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	var plugins []pipeline.Plugin
	if authOn {
		plugins = append(plugins, authplugin.New(keyStore, true))
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	gw := httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(gw.Close)
	g.base = "ws" + strings.TrimPrefix(gw.URL, "http")
	return g
}

// open dials the gateway with key in the header the vendor's SDK uses and returns the
// handshake status (101 when the session opened).
func (g *speechDefaultModelGateway) open(t *testing.T, path, key string) (int, *websocket.Conn) {
	t.Helper()
	return g.openWith(t, path, key, nil)
}

func (g *speechDefaultModelGateway) openWith(t *testing.T, path, key string, extra http.Header) (int, *websocket.Conn) {
	t.Helper()
	hdr := http.Header{"Authorization": {"Token " + key}}
	if strings.HasPrefix(path, "/cartesia/") {
		hdr = http.Header{"X-API-Key": {key}}
	}
	for k, v := range extra {
		hdr[k] = v
	}
	conn, resp, err := websocket.DefaultDialer.Dial(g.base+path, hdr)
	if resp == nil {
		t.Fatalf("%s: no handshake response: %v", path, err)
	}
	if conn != nil {
		t.Cleanup(func() { conn.Close() })
	}
	return resp.StatusCode, conn
}

const (
	deepgramSpeakNoModel  = "/deepgram/v1/speak"
	deepgramListenNoModel = "/deepgram/v1/listen?encoding=linear16&sample_rate=16000"
	cartesiaSTTNoModel    = "/cartesia/stt/websocket?encoding=pcm_s16le&sample_rate=16000"
)

func TestSpeechRelayKeyWithAModelListIsRefusedTheVendorsDefaultModel(t *testing.T) {
	g := newSpeechDefaultModelGateway(t)
	for _, path := range []string{deepgramSpeakNoModel, deepgramListenNoModel, cartesiaSTTNoModel} {
		if status, _ := g.open(t, path, defaultModelKeyListed); status != http.StatusForbidden {
			t.Errorf("%s with a key limited to deepgram/nova-3 and cartesia/sonic-2: status %d, want 403", path, status)
		}
	}
	// A model outside the list is refused; a listed one opens.
	if status, _ := g.open(t, "/deepgram/v1/speak?model=aura-2-thalia-en", defaultModelKeyListed); status != http.StatusForbidden {
		t.Errorf("speak with an unlisted model: status %d, want 403", status)
	}
	if status, _ := g.open(t, "/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000", defaultModelKeyListed); status != http.StatusSwitchingProtocols {
		t.Errorf("listen with the listed model: status %d, want 101", status)
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.dialed) != 1 || !strings.Contains(g.dialed[0], "model=nova-3") {
		t.Errorf("the vendor was dialed for %v, want only the listed model's session", g.dialed)
	}
}

func TestSpeechRelayAccessGroupsAdmitTheDefaultModelOnlyForEveryModelOfTheVendor(t *testing.T) {
	g := newSpeechDefaultModelGateway(t)
	cases := []struct {
		key, path string
		want      int
	}{
		{defaultModelKeyVendor, deepgramSpeakNoModel, http.StatusSwitchingProtocols},
		{defaultModelKeyVendor, deepgramListenNoModel, http.StatusSwitchingProtocols},
		{defaultModelKeyVendor, cartesiaSTTNoModel, http.StatusForbidden}, // deepgram/* admits no Cartesia model
		{defaultModelKeyAll, deepgramListenNoModel, http.StatusSwitchingProtocols},
		{defaultModelKeyAll, cartesiaSTTNoModel, http.StatusSwitchingProtocols},
		{defaultModelKeyLiteral, deepgramSpeakNoModel, http.StatusForbidden},
		{defaultModelKeyLiteral, deepgramListenNoModel, http.StatusForbidden},
		{defaultModelKeyLiteral, "/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000", http.StatusSwitchingProtocols},
	}
	for _, tc := range cases {
		if status, _ := g.open(t, tc.path, tc.key); status != tc.want {
			t.Errorf("%s with %s: status %d, want %d", tc.path, tc.key, status, tc.want)
		}
	}
}

func TestSpeechRelayKeyWithoutRestrictionsKeepsTheVendorsDefaultModel(t *testing.T) {
	g := newSpeechDefaultModelGateway(t)
	for _, path := range []string{deepgramSpeakNoModel, deepgramListenNoModel, cartesiaSTTNoModel} {
		if status, _ := g.open(t, path, defaultModelKeyOpen); status != http.StatusSwitchingProtocols {
			t.Errorf("%s with a key without model restrictions: status %d, want 101", path, status)
		}
	}
}

// Cartesia synthesis names its model in each request, so its connection opens with no model
// under any key, and a request that names no model is held to the same rule as a session.
func TestCartesiaSynthesisRequestWithoutAModelIsHeldToTheKeysModelList(t *testing.T) {
	g := newSpeechDefaultModelGateway(t)
	status, conn := g.open(t, "/cartesia/tts/websocket", defaultModelKeyListed)
	if status != http.StatusSwitchingProtocols {
		t.Fatalf("opening Cartesia synthesis with a listed-models key: status %d, want 101", status)
	}
	if err := conn.WriteMessage(websocket.TextMessage, []byte(`{"transcript":"hello","voice":{"mode":"id","id":"v"},"context_id":"c1"}`)); err != nil {
		t.Fatalf("writing: %v", err)
	}
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	_, _, err := conn.ReadMessage()
	var closeErr *websocket.CloseError
	if !errors.As(err, &closeErr) || closeErr.Code != websocket.ClosePolicyViolation {
		t.Fatalf("reply to a request naming no model: %v, want a policy-violation close", err)
	}
	g.mu.Lock()
	got := append([]string(nil), g.upModel...)
	g.mu.Unlock()
	if len(got) != 0 {
		t.Errorf("Cartesia received %v, want nothing: the request was refused", got)
	}

	// Without restrictions the same request is relayed unchanged.
	status, open := g.open(t, "/cartesia/tts/websocket", defaultModelKeyOpen)
	if status != http.StatusSwitchingProtocols {
		t.Fatalf("opening Cartesia synthesis with an unrestricted key: status %d, want 101", status)
	}
	if err := open.WriteMessage(websocket.TextMessage, []byte(`{"transcript":"hello","voice":{"mode":"id","id":"v"},"context_id":"c2"}`)); err != nil {
		t.Fatalf("writing: %v", err)
	}
	open.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, _, err := open.ReadMessage(); err != nil {
		t.Fatalf("reply to the unrestricted key's request: %v", err)
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if strings.Join(g.upModel, ",") != "<none>" {
		t.Errorf("Cartesia received models %v, want the one request with no model_id", g.upModel)
	}
}

// Every Cartesia synthesis request that names no model is held to the key's model rules, also
// after an earlier request of the session named one: a key with a model list, or whose access
// groups name single models, has such a request refused (policy-violation close, nothing
// relayed); an unrestricted key and the groups that admit every Cartesia model ("*",
// "cartesia/*") have it relayed.
func TestCartesiaSynthesisEveryRequestWithoutAModelIsHeldToTheKeysModelRules(t *testing.T) {
	const (
		named   = `{"model_id":"sonic-2","transcript":"hello","voice":{"mode":"id","id":"v"},"context_id":"a"}`
		unnamed = `{"transcript":"world","voice":{"mode":"id","id":"v"},"context_id":"b"}`
	)
	cases := []struct {
		name, key string
		relayed   bool
	}{
		{"model list", defaultModelKeyListed, false},
		{"access group naming single models", defaultModelKeyLiteral, false},
		{"no restrictions", defaultModelKeyOpen, true},
		{"access group *", defaultModelKeyAll, true},
		{"access group cartesia/*", defaultModelKeyCartVnd, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := newSpeechDefaultModelGateway(t)
			status, conn := g.open(t, "/cartesia/tts/websocket", tc.key)
			if status != http.StatusSwitchingProtocols {
				t.Fatalf("opening Cartesia synthesis: status %d, want 101", status)
			}
			if err := conn.WriteMessage(websocket.TextMessage, []byte(named)); err != nil {
				t.Fatalf("writing the request naming sonic-2: %v", err)
			}
			conn.SetReadDeadline(time.Now().Add(5 * time.Second))
			if _, _, err := conn.ReadMessage(); err != nil {
				t.Fatalf("reply to the request naming sonic-2: %v", err)
			}
			if err := conn.WriteMessage(websocket.TextMessage, []byte(unnamed)); err != nil {
				t.Fatalf("writing the request naming no model: %v", err)
			}
			conn.SetReadDeadline(time.Now().Add(5 * time.Second))
			_, _, err := conn.ReadMessage()
			want := "sonic-2,<none>"
			if tc.relayed {
				if err != nil {
					t.Fatalf("reply to the request naming no model: %v, want it relayed", err)
				}
			} else {
				var closeErr *websocket.CloseError
				if !errors.As(err, &closeErr) || closeErr.Code != websocket.ClosePolicyViolation {
					t.Fatalf("reply to the request naming no model: %v, want a policy-violation close", err)
				}
				want = "sonic-2"
			}
			g.mu.Lock()
			defer g.mu.Unlock()
			if got := strings.Join(g.upModel, ","); got != want {
				t.Errorf("Cartesia received model_id per request %q, want %q", got, want)
			}
		})
	}
}

// The relays reach the vendor on the platform's credential, so only an internal key may open
// one: an organization's key is refused before the vendor is dialed. A browser page (any
// request with an Origin) is refused too, so a gateway key is never embedded in one.
func TestSpeechRelayOpensOnlyForAnInternalKeyAndNeverForABrowserPage(t *testing.T) {
	g := newSpeechDefaultModelGateway(t)
	listen := "/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000"
	if status, _ := g.open(t, listen, defaultModelKeyOrg); status != http.StatusForbidden {
		t.Errorf("an organization's key: status %d, want 403", status)
	}
	if status, _ := g.openWith(t, listen, defaultModelKeyOpen, http.Header{"Origin": {"https://app.example"}}); status != http.StatusForbidden {
		t.Errorf("an internal key from a browser page: status %d, want 403", status)
	}
	if status, _ := g.open(t, listen, defaultModelKeyOpen); status != http.StatusSwitchingProtocols {
		t.Errorf("an internal key from a server: status %d, want 101", status)
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.dialed) != 1 {
		t.Errorf("the vendor was dialed for %v, want only the internal key's server-side session", g.dialed)
	}
}

// With auth off (auth.enabled false) the gateway checks no key on any route, so the relays
// refuse no caller for its key: a request with no key, or with an organization's key, opens a
// session on the platform's credential, as a chat request is served on the platform's providers.
// A browser page is still refused. The release note's key rules (N12) hold with auth on.
func TestSpeechRelayWithAuthOffChecksNoKey(t *testing.T) {
	g := newSpeechRelayGateway(t, false)
	listen := "/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000"
	conn, resp, err := websocket.DefaultDialer.Dial(g.base+listen, nil)
	if conn != nil {
		t.Cleanup(func() { conn.Close() })
	}
	if resp == nil || resp.StatusCode != http.StatusSwitchingProtocols {
		t.Errorf("no key, auth off: %v %v, want 101", resp, err)
	}
	if status, _ := g.open(t, listen, defaultModelKeyOrg); status != http.StatusSwitchingProtocols {
		t.Errorf("an organization's key, auth off: status %d, want 101", status)
	}
	if status, _ := g.openWith(t, listen, defaultModelKeyOpen, http.Header{"Origin": {"https://app.example"}}); status != http.StatusForbidden {
		t.Errorf("a browser page, auth off: status %d, want 403", status)
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.dialed) != 2 {
		t.Errorf("the vendor was dialed for %v, want the two server-side sessions", g.dialed)
	}
}
