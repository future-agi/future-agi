// H-MCP reuses the scenarios' MCP implementation and fixtures. The two local
// listeners share one mock: HTTP for custom servers, HTTPS for catalog adds
// (the product requires HTTPS there). There is no proxy or outbound client.
//
// /mcp-next is the same server's next release (GW-E2E-029): the fixtures in
// /fixtures-next, where echo's description changed and lookup_order is new,
// behind the same bearer. An org that points its server at it sees its
// upstream change what it serves.
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
)

func selectedMockAuth(value string) (string, error) {
	const bearer = "bearer:e2e-mcp-upstream-bearer-not-a-secret"
	switch value {
	case "", bearer:
		return bearer, nil
	case "oauth":
		return "oauth", nil
	default:
		return "", errors.New("E2E_MCP_MOCK_AUTH must select the reviewed bearer or OAuth fixture")
	}
}

func main() {
	auth, err := selectedMockAuth(os.Getenv("E2E_MCP_MOCK_AUTH"))
	if err != nil {
		log.Fatal(err)
	}

	fixtures, err := mcpmock.LoadFixtures("/fixtures")
	if err != nil {
		log.Fatal(err)
	}
	era, err := mcpmock.ParseEra("dual")
	if err != nil {
		log.Fatal(err)
	}
	mock, err := mcpmock.New(mcpmock.Config{
		Era: era, Auth: auth,
		Path: "/mcp", PublicURL: "https://mcp.e2e-public.test:8443",
		ServerName: "agentcc-mcpmock",
	}, fixtures)
	if err != nil {
		log.Fatal(err)
	}
	nextFixtures, err := mcpmock.LoadFixtures("/fixtures-next")
	if err != nil {
		log.Fatal(err)
	}
	next, err := mcpmock.New(mcpmock.Config{
		Era: era, Auth: auth,
		Path: "/mcp-next", PublicURL: "https://mcp.e2e-public.test:8443",
		ServerName: "agentcc-mcpmock",
	}, nextFixtures)
	if err != nil {
		log.Fatal(err)
	}
	mux := http.NewServeMux()
	mux.Handle("/mcp-next", next.Handler())
	// GW-E2E-036 reads the next release's own journal, not the original mock's.
	// Expose only its read-only receipt endpoint; no new product API or control.
	mux.Handle("GET /mcp-next/__received", http.StripPrefix("/mcp-next", next.Handler()))
	mux.Handle("/", mock.Handler())
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	httpServer := &http.Server{Addr: ":8080", Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	tlsServer := &http.Server{Addr: ":8443", Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	serve := func(err error) {
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatal(err)
		}
	}
	go func() { serve(httpServer.ListenAndServe()) }()
	go func() { serve(tlsServer.ListenAndServeTLS("/tls/ca.pem", "/tls/key.pem")) }()
	<-ctx.Done()
	shutdown, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = httpServer.Shutdown(shutdown)
	_ = tlsServer.Shutdown(shutdown)
}
