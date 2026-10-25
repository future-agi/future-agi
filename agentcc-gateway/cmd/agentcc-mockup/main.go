// Command agentcc-mockup is a stand-in upstream for the gateway's scenario
// tests, using the standard library only:
//
//	go run ./cmd/agentcc-mockup --addr 127.0.0.1:18911 --fixtures scripts/scenarios/fixtures/common
//
// Upstream routes, each under /v1 and /api/v1: POST chat/completions (stream
// and not), responses, messages (Anthropic), embeddings, images/generations
// and audio/speech; GET models, /api/v1/models/{author}/{slug}/endpoints,
// /api/v1/endpoints/zdr and /api/v1/credits.
//
// x-mock-behavior (comma-separated, applied left to right) shapes a call:
// fail:<status>[:n=<k>], latency:<ms>, chunk_delay:<ms>,
// stream_abort_after:<k>, no_usage, usage_cost:<usd>, cached_tokens:<n>,
// cache_write_tokens:<n>, reasoning_tokens:<n>, cache_read:<n> and
// cache_creation:<n> (aliases), finish:<reason>, empty, tools, served:<slug>.
// Model names stand for some of them: mock/fail-<code>, mock/slow-<ms>,
// mock/tools, mock/empty, mock/filter; mock/ok-* and any other model succeed.
// Directives the mock does not know go to package ext.
//
// Inspection: GET /__received?last=N&path=<substring>, GET /__charges,
// POST and GET /__logs (also the gateway's /agentcc/webhook/logs/),
// POST /__reset, and GET, POST and DELETE /__behavior, which arms a behaviour
// for inference calls without the header (the gateway does not forward it).
package main

import (
	"flag"
	"log"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/ext"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:18911", "listen address")
	fixtures := flag.String("fixtures", "", "fixtures directory (config.json, models.json, zdr.json, endpoints/)")
	flag.Parse()

	srv, err := newServer(*fixtures)
	if err != nil {
		log.Fatalf("agentcc-mockup: %v", err)
	}
	log.Printf("agentcc-mockup listening on %s (fixtures %q)", *addr, *fixtures)
	hs := &http.Server{Addr: *addr, Handler: srv, ReadHeaderTimeout: 10 * time.Second}
	log.Fatal(hs.ListenAndServe())
}

// newServer builds the mock over the fixtures in dir, with the extension
// routes and directives wired in.
func newServer(dir string) (*mock.Server, error) {
	fix, err := mock.LoadFixtures(dir)
	if err != nil {
		return nil, err
	}
	srv := mock.New(fix)
	srv.SetDirectiveHook(ext.Directive)
	ext.Register(srv.Mux(), srv.State())
	return srv, nil
}
