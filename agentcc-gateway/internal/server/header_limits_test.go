package server

import (
	"context"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// serveOnLoopback runs the real http.Server (so its header limits apply, which
// ServeHTTP alone does not exercise) on an ephemeral 127.0.0.1 port.
func serveOnLoopback(t *testing.T, s *Server) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go s.httpServer.Serve(ln)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		s.httpServer.Shutdown(ctx)
	})
	return "http://" + ln.Addr().String()
}

func healthWithHeader(t *testing.T, base string, size int) int {
	t.Helper()
	req, _ := http.NewRequest("GET", base+"/healthz", nil)
	req.Header.Set("X-Padding", strings.Repeat("a", size))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request with %d-byte header: %v", size, err)
	}
	resp.Body.Close()
	return resp.StatusCode
}

// TestServerMaxHeaderBytesHonoured: server.max_header_bytes must reach the
// listener. A configured cap refuses larger headers with 431, while the
// default (Go's 1MB) still accepts ordinary large headers.
func TestServerMaxHeaderBytesHonoured(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	base := serveOnLoopback(t, headerLimitServer(t, mock.URL, 4096))
	if got := healthWithHeader(t, base, 16*1024); got != http.StatusRequestHeaderFieldsTooLarge {
		t.Errorf("16KB header with max_header_bytes=4096: status %d, want 431", got)
	}
	if got := healthWithHeader(t, base, 512); got != http.StatusOK {
		t.Errorf("512B header with max_header_bytes=4096: status %d, want 200", got)
	}

	base = serveOnLoopback(t, headerLimitServer(t, mock.URL, 0))
	if got := healthWithHeader(t, base, 16*1024); got != http.StatusOK {
		t.Errorf("16KB header with default limit: status %d, want 200", got)
	}
	if got := healthWithHeader(t, base, 2<<20); got != http.StatusRequestHeaderFieldsTooLarge {
		t.Errorf("2MB header with default limit: status %d, want 431", got)
	}
}

// headerLimitServer is createTestServer with server.max_header_bytes set
// (0 keeps the default).
func headerLimitServer(t *testing.T, mockURL string, maxHeaderBytes int) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	if maxHeaderBytes > 0 {
		cfg.Server.MaxHeaderBytes = maxHeaderBytes
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: mockURL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}
