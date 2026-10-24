// Command agentcc-mcpmock is a stand-in MCP upstream server for the gateway's
// tests and scenarios, using the standard library only:
//
//	agentcc-mcpmock --mode http --addr 127.0.0.1:18931 --era dual \
//	  --fixtures scripts/scenarios/fixtures/mcp/common --auth none
//	agentcc-mcpmock --mode stdio --era 2025-11-25 --fixtures <dir> [--addr <control port>]
//
// Eras (--era): 2026 (2026-07-28: per-request _meta, server/discover,
// resultType, subscriptions/listen, input_required), 2025-11-25, 2025-06-18
// and 2025-03-26 (initialize, Mcp-Session-Id, GET stream with event ids and
// Last-Event-ID replay, DELETE, 404 for an ended session), 2024-11-05 (GET
// /sse plus POST /messages), and dual (all of them on one endpoint).
//
// --auth: none, bearer:<token>, or oauth (the mock is its own RFC 8414
// authorization server and an RFC 9728 protected resource; see package
// mcpmock). /__received keeps credential headers as presence and SHA-256
// only: --auth-header (default Authorization) is reported as auth, and
// --credential-header adds names to the ones the mock already treats as
// credentials. Fixtures: tools.json, resources.json and prompts.json in
// --fixtures. The control API (/__control/*, /__received, /__charges,
// /__health) is on --addr in HTTP mode, and on --addr, when given, in stdio
// mode. Logs go to stderr.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
)

type options struct {
	mode, addr, addrFile, era, fixtures, auth, path, publicURL, name, marker string
	authHeader                                                               string
	credentialHeaders                                                        stringList
	ignoreSigterm                                                            bool
	linger                                                                   time.Duration
}

// stringList is a repeatable string flag.
type stringList []string

func (l *stringList) String() string     { return strings.Join(*l, ",") }
func (l *stringList) Set(v string) error { *l = append(*l, v); return nil }

func parseFlags(args []string) (options, error) {
	var o options
	fs := flag.NewFlagSet("agentcc-mcpmock", flag.ContinueOnError)
	fs.StringVar(&o.mode, "mode", "http", "http or stdio")
	fs.StringVar(&o.addr, "addr", "", "listen address (HTTP mode: required; stdio mode: the control API's side port)")
	fs.StringVar(&o.addrFile, "addr-file", "", "write the bound address here once listening (use with port 0)")
	fs.StringVar(&o.era, "era", "dual", "2026, 2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05 or dual")
	fs.StringVar(&o.fixtures, "fixtures", "", "directory with tools.json, resources.json and prompts.json")
	fs.StringVar(&o.auth, "auth", "none", "none, bearer:<token> or oauth")
	fs.StringVar(&o.path, "path", "/mcp", "the Streamable HTTP endpoint path")
	fs.StringVar(&o.publicURL, "public-url", "", "the base URL clients use (default http://<bound address>)")
	fs.StringVar(&o.name, "name", "agentcc-mcpmock", "serverInfo.name")
	fs.StringVar(&o.authHeader, "auth-header", "Authorization",
		"the header /__received reports as auth, by presence and SHA-256 (e.g. an api_key server's header)")
	fs.Var(&o.credentialHeaders, "credential-header",
		"repeatable: another header kept as presence and SHA-256 only (names with auth, key, token, secret, passw, cookie, credential or signature already are)")
	fs.StringVar(&o.marker, "marker", "", "stdio mode: file written when the mock exits after stdin closed")
	fs.BoolVar(&o.ignoreSigterm, "ignore-sigterm", false, "stdio mode: ignore SIGTERM (tests the SIGKILL step)")
	fs.DurationVar(&o.linger, "linger", 0, "stdio mode: keep running this long after stdin closes")
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	switch {
	case o.mode != "http" && o.mode != "stdio":
		return o, fmt.Errorf("--mode %q: want http or stdio", o.mode)
	case o.mode == "http" && o.addr == "":
		return o, errors.New("--addr is required in HTTP mode")
	case o.mode == "http" && (o.marker != "" || o.ignoreSigterm || o.linger != 0):
		return o, errors.New("--marker, --ignore-sigterm and --linger are stdio-mode flags")
	}
	return o, nil
}

func main() {
	log.SetOutput(os.Stderr) // stdout is the protocol stream in stdio mode
	log.SetPrefix("agentcc-mcpmock: ")
	o, err := parseFlags(os.Args[1:])
	if err != nil {
		log.Fatal(err)
	}
	era, err := mcpmock.ParseEra(o.era)
	if err != nil {
		log.Fatal(err)
	}
	fix, err := mcpmock.LoadFixtures(o.fixtures)
	if err != nil {
		log.Fatalf("fixtures: %v", err)
	}
	m, err := mcpmock.New(mcpmock.Config{Era: era, Auth: o.auth, Path: o.path, PublicURL: o.publicURL,
		ServerName: o.name, Stdio: o.mode == "stdio", AuthHeader: o.authHeader, CredentialHeaders: o.credentialHeaders}, fix)
	if err != nil {
		log.Fatal(err)
	}
	if o.mode == "stdio" {
		runStdio(m, o)
		return
	}
	// The handler goes in before serve writes --addr-file, the readiness
	// signal: a SIGTERM sent the moment the file appears must still shut down
	// gracefully and exit 0, not take the default action (exit 143).
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
	srv, err := serve(m, o)
	if err != nil {
		log.Fatal(err)
	}
	<-stop
	shutdown(srv)
}

// serve listens on --addr and serves the mock's handler in the background.
func serve(m *mcpmock.Mock, o options) (*http.Server, error) {
	ln, err := net.Listen("tcp", o.addr)
	if err != nil {
		return nil, err
	}
	bound := ln.Addr().String()
	if o.publicURL == "" {
		m.SetPublicURL("http://" + bound)
	}
	if o.addrFile != "" {
		if err := writeAtomic(o.addrFile, bound+"\n"); err != nil {
			ln.Close()
			return nil, err
		}
	}
	srv := &http.Server{Handler: m.Handler(), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("serve: %v", err)
		}
	}()
	log.Printf("listening on %s (mode %s, era %s, auth %s)", bound, o.mode, o.era, authLabel(o.auth))
	return srv, nil
}

// writeAtomic writes path through a temporary file and a rename, so a reader
// polling for it never sees it empty or half-written.
func writeAtomic(path, content string) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(content), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func shutdown(srv *http.Server) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		srv.Close() // long-lived streams do not end on their own
	}
}

// runStdio serves the protocol on stdin and stdout. When stdin closes cleanly
// it lingers (--linger), writes the --marker file and exits 0; a read error
// exits 1 without the marker, and SIGTERM ends it at once, without the
// marker, unless --ignore-sigterm.
func runStdio(m *mcpmock.Mock, o options) {
	if o.ignoreSigterm {
		signal.Ignore(syscall.SIGTERM)
	}
	var srv *http.Server
	if o.addr != "" {
		var err error
		if srv, err = serve(m, o); err != nil {
			log.Fatal(err)
		}
	}
	if err := m.ServeStdio(os.Stdin, os.Stdout); err != nil {
		log.Fatalf("stdin: %v", err) // not a graceful close: no marker
	}
	if o.linger > 0 {
		time.Sleep(o.linger)
	}
	if srv != nil {
		shutdown(srv)
	}
	if o.marker != "" {
		msg := fmt.Sprintf("graceful stdin close pid=%d\n", os.Getpid())
		if err := os.WriteFile(o.marker, []byte(msg), 0o600); err != nil {
			log.Printf("shutdown marker: %v", err)
		}
	}
}

func authLabel(auth string) string {
	if strings.HasPrefix(auth, "bearer:") {
		return "bearer" // never log the token
	}
	return auth
}
