package server

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// freePort returns a loopback port nothing listens on right now.
func freePort(t *testing.T) int {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	return ln.Addr().(*net.TCPAddr).Port
}

type tlsGateway struct {
	srv       *Server
	httpsURL  string
	httpAddr  string // the configured HTTP address, listening or not
	pki       *testPKI
	certFile  string
	keyFile   string
	clientTLS *tls.Config
}

// startTLSGateway runs a real gateway with the HTTPS listener on, through
// Start, against an OpenAI-format upstream. mutate adjusts the config.
func startTLSGateway(t *testing.T, upstreamURL string, mutate func(*config.Config)) *tlsGateway {
	t.Helper()
	g := &tlsGateway{pki: newTestPKI(t)}
	cert, key := g.pki.issue(t, 1)
	g.certFile, g.keyFile = writePair(t, t.TempDir(), cert, key)

	cfg := config.DefaultConfig()
	cfg.Server.Host = "127.0.0.1"
	cfg.Server.Port = freePort(t)
	cfg.Server.TLS = config.ServerTLSConfig{Enabled: true, Port: freePort(t), CertFile: g.certFile, KeyFile: g.keyFile, MinVersion: "1.2", ReloadInterval: 20 * time.Millisecond}
	if upstreamURL != "" {
		cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstreamURL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-4o"}}
	}
	if mutate != nil {
		mutate(cfg)
	}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("config: %v", err)
	}
	g.httpAddr = cfg.Addr()

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	g.srv = New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	started := make(chan error, 1)
	go func() { started <- g.srv.Start() }()
	deadline := time.Now().Add(10 * time.Second)
	for !g.srv.ready.Load() {
		select {
		case err := <-started:
			t.Fatalf("Start returned before ready: %v", err)
		default:
		}
		if time.Now().After(deadline) {
			t.Fatal("gateway not ready")
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		g.srv.Shutdown(ctx)
		<-started
	})
	g.httpsURL = "https://" + g.srv.listenAddrs["https"]
	g.clientTLS = &tls.Config{RootCAs: g.pki.rootPool, ServerName: "localhost"}
	return g
}

func (g *tlsGateway) client(h2 bool) *http.Client {
	tr := &http.Transport{TLSClientConfig: g.clientTLS.Clone(), ForceAttemptHTTP2: h2}
	return &http.Client{Transport: tr, Timeout: 10 * time.Second}
}

func TestTLSListenerServesHealthWithTheFullChain(t *testing.T) {
	g := startTLSGateway(t, "", nil)
	for _, h2 := range []bool{false, true} {
		for _, path := range []string{"/healthz", "/livez", "/readyz"} {
			resp, err := g.client(h2).Get(g.httpsURL + path)
			if err != nil {
				t.Fatalf("GET %s over TLS (h2=%v): %v", path, h2, err)
			}
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				t.Errorf("GET %s over TLS = %d, want 200", path, resp.StatusCode)
			}
			if got := len(resp.TLS.PeerCertificates); got != 2 {
				t.Errorf("served chain length = %d, want leaf and intermediate", got)
			}
			if h2 && resp.ProtoMajor != 2 {
				t.Errorf("h2 client got %s", resp.Proto)
			}
		}
	}
	// HTTP stays on alongside it.
	resp, err := http.Get("http://" + g.httpAddr + "/healthz")
	if err != nil {
		t.Fatalf("HTTP listener: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("GET /healthz over HTTP = %d", resp.StatusCode)
	}
}

// Modern curl and openssl refuse TLS 1.0/1.1 themselves, so only a client
// that offers nothing newer proves the server refuses them.
func TestTLSListenerRefusesOldVersionsAndWeakSuites(t *testing.T) {
	g := startTLSGateway(t, "", nil)
	addr := strings.TrimPrefix(g.httpsURL, "https://")
	dial := func(cfg *tls.Config) (*tls.Conn, error) {
		return tls.DialWithDialer(&net.Dialer{Timeout: 5 * time.Second}, "tcp", addr, cfg)
	}

	for _, v := range []uint16{tls.VersionTLS10, tls.VersionTLS11} {
		cfg := g.clientTLS.Clone()
		cfg.MinVersion, cfg.MaxVersion = v, v
		if conn, err := dial(cfg); err == nil {
			conn.Close()
			t.Errorf("a %s-only client completed the handshake", tlsVersionName(v))
		}
	}

	cfg := g.clientTLS.Clone()
	cfg.MaxVersion = tls.VersionTLS12
	conn, err := dial(cfg)
	if err != nil {
		t.Fatalf("TLS 1.2 client: %v", err)
	}
	state := conn.ConnectionState()
	conn.Close()
	if state.Version != tls.VersionTLS12 {
		t.Errorf("version = %s, want TLS 1.2", tlsVersionName(state.Version))
	}
	if !isAllowedSuite(state.CipherSuite) {
		t.Errorf("negotiated %s, not an ECDHE AEAD suite", tls.CipherSuiteName(state.CipherSuite))
	}
	if len(state.PeerCertificates) != 2 {
		t.Errorf("chain length = %d, want 2", len(state.PeerCertificates))
	}

	// A TLS 1.2 client offering only CBC or non-ECDHE suites gets nothing.
	cfg = g.clientTLS.Clone()
	cfg.MaxVersion = tls.VersionTLS12
	cfg.CipherSuites = []uint16{tls.TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA, tls.TLS_RSA_WITH_AES_128_GCM_SHA256}
	if conn, err := dial(cfg); err == nil {
		conn.Close()
		t.Error("a client offering only CBC and RSA-key-exchange suites completed the handshake")
	}

	cfg = g.clientTLS.Clone()
	conn, err = dial(cfg)
	if err != nil {
		t.Fatalf("default client: %v", err)
	}
	if v := conn.ConnectionState().Version; v != tls.VersionTLS13 {
		t.Errorf("default client negotiated %s, want TLS 1.3", tlsVersionName(v))
	}
	conn.Close()
}

func isAllowedSuite(id uint16) bool {
	for _, s := range tls12CipherSuites {
		if s == id {
			return true
		}
	}
	return false
}

func TestTLSListenerMinVersion13(t *testing.T) {
	g := startTLSGateway(t, "", func(c *config.Config) { c.Server.TLS.MinVersion = "1.3" })
	cfg := g.clientTLS.Clone()
	cfg.MaxVersion = tls.VersionTLS12
	conn, err := tls.Dial("tcp", strings.TrimPrefix(g.httpsURL, "https://"), cfg)
	if err == nil {
		conn.Close()
		t.Fatal("a TLS 1.2 client completed the handshake with min_version 1.3")
	}
}

func TestTLSListenerWithHTTPOff(t *testing.T) {
	off := false
	g := startTLSGateway(t, "", func(c *config.Config) { c.Server.HTTPEnabled = &off })
	_, err := net.DialTimeout("tcp", g.httpAddr, time.Second)
	if err == nil || !errors.Is(err, syscall.ECONNREFUSED) {
		t.Errorf("dial HTTP port with HTTP off: %v, want connection refused", err)
	}
	if _, ok := g.srv.listenAddrs["http"]; ok {
		t.Error("an HTTP listener is open")
	}
	resp, err := g.client(false).Get(g.httpsURL + "/readyz")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("readyz over TLS = %d", resp.StatusCode)
	}
}

// A rotation is served on the next handshake, and a connection that was
// already open keeps working across it.
func TestTLSListenerServesARotatedCertificate(t *testing.T) {
	g := startTLSGateway(t, "", nil)
	held := g.client(false)
	if resp, err := held.Get(g.httpsURL + "/healthz"); err != nil {
		t.Fatal(err)
	} else {
		resp.Body.Close()
	}

	cert, key := g.pki.issue(t, 2)
	writePair(t, strings.TrimSuffix(g.certFile, "/tls.crt"), cert, key)
	deadline := time.Now().Add(5 * time.Second)
	for {
		c := g.client(false)
		resp, err := c.Get(g.httpsURL + "/healthz")
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		c.CloseIdleConnections()
		if resp.TLS.PeerCertificates[0].SerialNumber.Int64() == 2 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the rotated certificate was not served")
		}
		time.Sleep(10 * time.Millisecond)
	}
	resp, err := held.Get(g.httpsURL + "/healthz")
	if err != nil {
		t.Fatalf("the connection opened before the rotation: %v", err)
	}
	resp.Body.Close()
}

// A streamed chat completion goes through the TLS listener chunk by chunk,
// over HTTP/1.1 and HTTP/2.
func TestTLSListenerStreamsChatCompletions(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		flusher := w.(http.Flusher)
		for i := 0; i < 3; i++ {
			fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"part%d \"},\"finish_reason\":null}]}\n\n", i)
			flusher.Flush()
		}
		io.WriteString(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n")
		io.WriteString(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-4o\",\"choices\":[],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":3,\"total_tokens\":6}}\n\ndata: [DONE]\n\n")
	}))
	t.Cleanup(upstream.Close)
	g := startTLSGateway(t, upstream.URL, nil)

	for _, h2 := range []bool{false, true} {
		t.Run(fmt.Sprintf("h2=%v", h2), func(t *testing.T) {
			body := `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`
			resp, err := g.client(h2).Post(g.httpsURL+"/v1/chat/completions", "application/json", strings.NewReader(body))
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusOK || !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
				b, _ := io.ReadAll(resp.Body)
				t.Fatalf("status %d %s: %s", resp.StatusCode, resp.Header.Get("Content-Type"), b)
			}
			if h2 != (resp.ProtoMajor == 2) {
				t.Errorf("proto = %s, want h2=%v", resp.Proto, h2)
			}
			var parts []string
			var done bool
			sc := bufio.NewScanner(resp.Body)
			for sc.Scan() {
				line := sc.Text()
				if line == "data: [DONE]" {
					done = true
				}
				if strings.Contains(line, `"content":"part`) {
					parts = append(parts, line)
				}
			}
			if !done || len(parts) != 3 {
				t.Errorf("stream: %d content chunks, done=%v", len(parts), done)
			}
		})
	}
}

// The gateway's WebSocket routes hijack the connection, which needs
// HTTP/1.1. A WebSocket client negotiates HTTP/1.1 even though the listener
// also offers h2, and the upgrade works through the full middleware chain.
func TestTLSListenerUpgradesWebSockets(t *testing.T) {
	g := startTLSGateway(t, "", nil)
	upgrader := websocket.Upgrader{}
	g.srv.router.Handle("GET", "/-/test/echo-ws", func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		mt, msg, err := conn.ReadMessage()
		if err == nil {
			conn.WriteMessage(mt, append([]byte("echo: "), msg...))
		}
	})
	dialer := websocket.Dialer{TLSClientConfig: g.clientTLS.Clone(), HandshakeTimeout: 5 * time.Second}
	wsURL := "wss" + strings.TrimPrefix(g.httpsURL, "https")

	conn, resp, err := dialer.Dial(wsURL+"/-/test/echo-ws", nil)
	if err != nil {
		t.Fatalf("wss dial: %v (resp %v)", err, resp)
	}
	defer conn.Close()
	if err := conn.WriteMessage(websocket.TextMessage, []byte("hello")); err != nil {
		t.Fatal(err)
	}
	_, msg, err := conn.ReadMessage()
	if err != nil || string(msg) != "echo: hello" {
		t.Fatalf("echo = %q, %v", msg, err)
	}

	// /v1/realtime answers over wss as it does over ws: it reaches the handler.
	_, resp, err = dialer.Dial(wsURL+"/v1/realtime?model=gpt-4o", nil)
	if err == nil || resp == nil {
		t.Fatalf("wss /v1/realtime: err=%v resp=%v", err, resp)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound && resp.StatusCode != http.StatusNotImplemented {
		t.Errorf("wss /v1/realtime = %d %s, want the handler's own answer", resp.StatusCode, b)
	}
}

// A bad certificate fails startup with the file named, and no listener
// opens: not the HTTPS one, and not the plain HTTP one either.
func TestTLSStartupFailsWithoutOpeningAListener(t *testing.T) {
	pki := newTestPKI(t)
	_, key := pki.issue(t, 1)
	otherCert, _ := pki.issue(t, 2)
	for _, tt := range []struct {
		name      string
		cert, key []byte
		want      string
	}{
		{"missing cert", nil, key, "server.tls: reading "},
		{"unparsable cert", []byte("garbage"), key, "server.tls: loading "},
		{"key mismatch", otherCert, key, "private key does not match public key"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			certFile, keyFile := writePair(t, t.TempDir(), tt.cert, tt.key)
			cfg := config.DefaultConfig()
			cfg.Server.Host = "127.0.0.1"
			cfg.Server.Port = freePort(t)
			cfg.Server.TLS = config.ServerTLSConfig{Enabled: true, Port: freePort(t), CertFile: certFile, KeyFile: keyFile, MinVersion: "1.2", ReloadInterval: time.Second}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
			errc := make(chan error, 1)
			go func() { errc <- srv.Start() }()
			select {
			case err := <-errc:
				if err == nil || !strings.Contains(err.Error(), tt.want) || !strings.Contains(err.Error(), "tls.") {
					t.Fatalf("Start = %v, want an error containing %q", err, tt.want)
				}
				if tt.name == "missing cert" && !strings.Contains(err.Error(), certFile) {
					t.Errorf("error %q does not name %s", err, certFile)
				}
			case <-time.After(5 * time.Second):
				srv.Shutdown(t.Context())
				t.Fatal("Start did not fail")
			}
			if srv.ready.Load() {
				t.Error("the gateway reports ready")
			}
			for _, addr := range []string{cfg.Addr(), cfg.TLSAddr()} {
				if conn, err := net.DialTimeout("tcp", addr, time.Second); err == nil {
					conn.Close()
					t.Errorf("%s accepts connections after a failed start", addr)
				}
			}
		})
	}
}

// Without TLS the gateway starts exactly as before: one HTTP listener.
func TestStartWithoutTLSOpensOnlyHTTP(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Server.Host = "127.0.0.1"
	cfg.Server.Port = freePort(t)
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	if srv.httpsServer != nil || srv.certs != nil {
		t.Fatal("a TLS server was built with TLS off")
	}
	errc := make(chan error, 1)
	go func() { errc <- srv.Start() }()
	deadline := time.Now().Add(5 * time.Second)
	for !srv.ready.Load() {
		if time.Now().After(deadline) {
			t.Fatal("not ready")
		}
		time.Sleep(5 * time.Millisecond)
	}
	if len(srv.listenAddrs) != 1 || srv.listenAddrs["http"] != cfg.Addr() {
		t.Errorf("listeners = %v, want only http on %s", srv.listenAddrs, cfg.Addr())
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}
	if err := <-errc; err != nil {
		t.Errorf("Start after shutdown = %v, want nil", err)
	}
}

// /-/config reports the listener that answers: the HTTPS one when HTTP is off.
func TestConfigReportsTheActiveListener(t *testing.T) {
	off := false
	for _, httpOn := range []bool{true, false} {
		g := startTLSGateway(t, "", func(c *config.Config) {
			c.Admin.Token = "synthetic-admin-token"
			if !httpOn {
				c.Server.HTTPEnabled = &off
			}
		})
		req, _ := http.NewRequest(http.MethodGet, g.httpsURL+"/-/config", nil)
		req.Header.Set("Authorization", "Bearer synthetic-admin-token")
		resp, err := g.client(false).Do(req)
		if err != nil {
			t.Fatal(err)
		}
		var body struct {
			Server map[string]any `json:"server"`
		}
		json.NewDecoder(resp.Body).Decode(&body)
		resp.Body.Close()
		cfg := g.srv.cfg.Server
		wantPort, wantScheme := cfg.Port, "http"
		if !httpOn {
			wantPort, wantScheme = cfg.TLS.Port, "https"
		}
		if body.Server["port"] != float64(wantPort) || body.Server["scheme"] != wantScheme ||
			body.Server["http_enabled"] != httpOn || body.Server["tls_port"] != float64(cfg.TLS.Port) {
			t.Errorf("http_enabled=%v: server = %v, want port %d scheme %s", httpOn, body.Server, wantPort, wantScheme)
		}
	}
}

// A shutdown that arrives while Start is still setting up (a signal during
// startup) stops it cleanly: Start returns, nothing keeps listening, and the
// certificate poller is not left running. Run with -race.
func TestShutdownDuringStartup(t *testing.T) {
	for i := 0; i < 20; i++ {
		pki := newTestPKI(t)
		cert, key := pki.issue(t, 1)
		certFile, keyFile := writePair(t, t.TempDir(), cert, key)
		cfg := config.DefaultConfig()
		cfg.Server.Host = "127.0.0.1"
		cfg.Server.Port = freePort(t)
		cfg.Server.TLS = config.ServerTLSConfig{Enabled: true, Port: freePort(t), CertFile: certFile, KeyFile: keyFile, MinVersion: "1.2", ReloadInterval: time.Second}
		registry, err := providers.NewRegistry(cfg)
		if err != nil {
			t.Fatal(err)
		}
		srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
		errc := make(chan error, 1)
		go func() { errc <- srv.Start() }()
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		srv.Shutdown(ctx)
		cancel()
		select {
		case err := <-errc:
			if err != nil {
				t.Fatalf("Start = %v after a shutdown during startup", err)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("Start still running after Shutdown")
		}
		for _, addr := range []string{cfg.Addr(), cfg.TLSAddr()} {
			if conn, err := net.DialTimeout("tcp", addr, time.Second); err == nil {
				conn.Close()
				t.Fatalf("%s still accepts connections after shutdown", addr)
			}
		}
	}
}
