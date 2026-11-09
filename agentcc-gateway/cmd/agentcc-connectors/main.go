// Command agentcc-connectors is the K2 connector runtime (CONNECTORS-PLAN
// 2.6, CN10): one MCP server, over Streamable HTTP at /<app-id>/mcp, for
// every kind: rest provider of a connector catalog. Standard library only:
//
//	agentcc-connectors [--addr 127.0.0.1:8790] [--addr-file <file>]
//	    [--catalog-dir <dir>] [--replay https://<vendor host>=http://127.0.0.1:<port>]...
//	    [--vendor-timeout 30s] [--max-vendor-bytes 8388608] [--page-size 50]
//
// The catalog is the embedded bundle (catalog/connectors/dist/catalog.json)
// unless --catalog-dir names an operator's catalog directory, checked as the
// gateway checks it (schema, L0, L1). A catalog with no rest provider the
// runtime can serve is refused: the process does not start rather than
// answer 404 for every app. So is a catalog with any provider or tool the
// runtime cannot serve as declared, such as a tool whose x-origin is not
// under its app's vendor.domains (the catalog check does not read
// x-origin): one log record names each refused app, provider and tool, and
// nothing is served.
//
// The bearer on each MCP request is the end user's vendor access token (an
// api_key provider's parts arrive where its manifest places them); the
// runtime keeps no credential and logs none. Every vendor call goes through
// M17's SSRF guard. In a hosted deployment it is reachable only from the
// gateway (a network policy, CN23); a self-hosted EE points
// OMEGA_INTEGRATION_<APP>_MCP_URL at it, and OSS adds it as a server URL.
//
// --replay (repeatable, operator only) answers a vendor origin from a
// loopback address, such as agentcc-cassette replay serving a recorded
// cassette, for conformance runs and scenarios; a target off loopback is
// refused. GET /healthz answers 200 with the apps served. Logs are JSON
// lines on stderr. SIGINT or SIGTERM drains in-flight requests and exits 0.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"runtime/debug"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/restmcp"
)

// Exit codes.
const (
	exitOK    = 0
	exitFail  = 1 // the catalog, the listener or the server failed
	exitUsage = 2 // bad flags
)

// DefaultAddr is where the runtime listens when --addr is not given:
// loopback, so a process started by hand is not reachable from the network.
const DefaultAddr = "127.0.0.1:8790"

// shutdownGrace bounds how long in-flight requests may take after a signal.
const shutdownGrace = 15 * time.Second

// version is set at build time (-ldflags "-X main.version=..."); otherwise
// it is the module's VCS revision, or "dev".
var version = ""

type options struct {
	addr, addrFile, catalogDir string
	replay                     map[string]string
	vendorTimeout              time.Duration
	maxVendorBytes             int64
	pageSize                   int
}

// replayFlag collects --replay origin=target pairs.
type replayFlag map[string]string

func (r replayFlag) String() string {
	keys := make([]string, 0, len(r))
	for k := range r {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return strings.Join(keys, ",")
}

func (r replayFlag) Set(v string) error {
	origin, target, ok := strings.Cut(v, "=")
	if !ok || origin == "" || target == "" {
		return errors.New("want https://<vendor host>=http://127.0.0.1:<port>")
	}
	if _, dup := r[origin]; dup {
		return fmt.Errorf("%s is replayed twice", origin)
	}
	r[origin] = target
	return nil
}

func parseFlags(args []string, stderr io.Writer) (options, error) {
	o := options{replay: map[string]string{}}
	fs := flag.NewFlagSet("agentcc-connectors", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.StringVar(&o.addr, "addr", DefaultAddr, "listen address (host:port; port 0 picks one)")
	fs.StringVar(&o.addrFile, "addr-file", "", "write the bound address here once listening")
	fs.StringVar(&o.catalogDir, "catalog-dir", "", "an operator's catalog directory (default: the embedded bundle)")
	fs.Var(replayFlag(o.replay), "replay", "operator only: answer a vendor origin from a loopback address, origin=target (repeatable)")
	fs.DurationVar(&o.vendorTimeout, "vendor-timeout", restmcp.DefaultVendorTimeout, "bound on one vendor call")
	fs.Int64Var(&o.maxVendorBytes, "max-vendor-bytes", restmcp.DefaultMaxVendorBytes, "bound on one vendor answer, as read")
	fs.IntVar(&o.pageSize, "page-size", restmcp.DefaultPageSize, "tools per tools/list page")
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	switch {
	case fs.NArg() > 0:
		return o, fmt.Errorf("unexpected argument %q", fs.Arg(0))
	case o.vendorTimeout <= 0:
		return o, errors.New("--vendor-timeout must be positive")
	case o.maxVendorBytes <= 0:
		return o, errors.New("--max-vendor-bytes must be positive")
	case o.pageSize <= 0:
		return o, errors.New("--page-size must be positive")
	}
	return o, nil
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	code := run(ctx, os.Args[1:], os.Stderr)
	stop()
	os.Exit(code)
}

func run(ctx context.Context, args []string, stderr io.Writer) int {
	logger := slog.New(slog.NewJSONHandler(stderr, nil))
	// The egress guard and the MCP protocol package log through the default
	// logger: one handler, one stream, the same no-credential rule.
	slog.SetDefault(logger)
	o, err := parseFlags(args, stderr)
	if err != nil {
		if !errors.Is(err, flag.ErrHelp) {
			logger.Error("agentcc-connectors: bad flags", "error", err.Error())
		}
		return exitUsage
	}
	apps, source, err := loadApps(o.catalogDir)
	var refused *restmcp.CompileError
	switch {
	case errors.As(err, &refused):
		logRefusals(logger, refused)
		logger.Error("agentcc-connectors: the catalog cannot be served", "catalog", source, "refused", len(refused.Refusals),
			"error", "a catalog is served whole or not at all; each refusal is logged above")
		return exitFail
	case err != nil:
		logger.Error("agentcc-connectors: the catalog cannot be served", "error", err.Error())
		return exitFail
	}
	srv, err := restmcp.New(apps, restmcp.Options{Replay: o.replay, VendorTimeout: o.vendorTimeout,
		MaxVendorBytes: o.maxVendorBytes, PageSize: o.pageSize, Version: buildVersion(), Logger: logger})
	if err != nil {
		logger.Error("agentcc-connectors: bad options", "error", err.Error())
		return exitFail
	}
	ln, err := net.Listen("tcp", o.addr)
	if err != nil {
		logger.Error("agentcc-connectors: cannot listen", "addr", o.addr, "error", err.Error())
		return exitFail
	}
	hs := &http.Server{
		Handler:           handler(srv, apps),
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		// A call may retry (restmcp's retry bounds): three vendor timeouts
		// and the waits between them, plus a margin.
		WriteTimeout: 3*o.vendorTimeout + 30*time.Second,
		IdleTimeout:  90 * time.Second,
		ErrorLog:     slog.NewLogLogger(logger.Handler(), slog.LevelWarn),
	}
	served := make(chan error, 1)
	go func() { served <- hs.Serve(ln) }()
	bound := ln.Addr().String()
	if o.addrFile != "" {
		if err := writeAtomic(o.addrFile, bound+"\n"); err != nil {
			logger.Error("agentcc-connectors: --addr-file", "error", err.Error())
			_ = hs.Close()
			return exitFail
		}
	}
	logger.Info("agentcc-connectors serving", "addr", bound, "catalog", source, "apps", appIDs(apps),
		"replayed_origins", replayFlag(o.replay).String(), "version", buildVersion())
	select {
	case err := <-served:
		logger.Error("agentcc-connectors: the server stopped", "error", err.Error())
		return exitFail
	case <-ctx.Done():
	}
	sctx, cancel := context.WithTimeout(context.Background(), shutdownGrace)
	defer cancel()
	if err := hs.Shutdown(sctx); err != nil {
		logger.Warn("agentcc-connectors: shutdown cut in-flight requests", "error", err.Error())
	}
	logger.Info("agentcc-connectors stopped")
	return exitOK
}

// loadApps loads the catalog (the operator's directory, else the embedded
// bundle) and compiles its rest providers. A provider the runtime cannot
// serve as declared fails the load, and so does a catalog with none.
func loadApps(dir string) ([]*restmcp.App, string, error) {
	cat, err := connectors.Load(dir)
	if err != nil {
		return nil, "", err
	}
	apps, err := restmcp.Compile(cat.Records)
	if err != nil {
		return nil, cat.Source, fmt.Errorf("%s: %w", cat.Source, err)
	}
	if len(apps) == 0 {
		return nil, cat.Source, fmt.Errorf("%s has no kind: rest provider to serve; point --catalog-dir at a catalog that has one", cat.Source)
	}
	return apps, cat.Source, nil
}

// logRefusals writes one record per provider or tool the runtime refused
// to serve, naming its app, provider and tool, so an operator can find each
// one (a tool whose x-origin is outside its app's vendor.domains, say).
func logRefusals(logger *slog.Logger, e *restmcp.CompileError) {
	for _, r := range e.Refusals {
		if r.Tool == "" {
			logger.Error("agentcc-connectors: provider refused", "app", r.App, "provider", r.Provider, "reason", r.Reason)
			continue
		}
		logger.Error("agentcc-connectors: tool refused", "app", r.App, "provider", r.Provider, "tool", r.Tool, "reason", r.Reason)
	}
}

// handler serves /healthz and hands every other path to the runtime (an
// app's path is /<app-id>/mcp, so the two never meet).
func handler(srv *restmcp.Server, apps []*restmcp.App) http.Handler {
	k2 := strings.ToLower(strings.TrimSpace(os.Getenv("AGENTCC_MCP_K2_APPS_ENABLED")))
	k2On := k2 == "true" || k2 == "1"
	hidden := func(id string) bool {
		if k2On {
			return false
		}
		switch id {
		case "gmail", "google-calendar", "google-bigquery", "microsoft-outlook", "microsoft-teams", "arcade":
			return true
		}
		return false
	}
	visible := make([]*restmcp.App, 0, len(apps))
	for _, app := range apps {
		if !hidden(app.ID) {
			visible = append(visible, app)
		}
	}
	health, _ := json.Marshal(map[string]any{"status": "ok", "apps": appIDs(visible)})
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(health)
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		id := strings.Split(strings.TrimPrefix(r.URL.Path, "/"), "/")[0]
		if hidden(id) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"error":{"code":"not_found","message":"Not found."}}`))
			return
		}
		srv.ServeHTTP(w, r)
	})
	return mux
}

func appIDs(apps []*restmcp.App) []string {
	ids := make([]string, len(apps))
	for i, a := range apps {
		ids[i] = a.ID
	}
	return ids
}

func buildVersion() string {
	if version != "" {
		return version
	}
	if info, ok := debug.ReadBuildInfo(); ok {
		for _, s := range info.Settings {
			if s.Key == "vcs.revision" && len(s.Value) >= 12 {
				return s.Value[:12]
			}
		}
	}
	return "dev"
}

// writeAtomic writes data to path through a temporary file and a rename,
// so a reader never sees a partial address.
func writeAtomic(path, data string) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(data), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
