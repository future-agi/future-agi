//go:build cataloguepreflighttest

package main

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sync/atomic"
	"time"

	fixture "github.com/futureagi/agentcc-gateway/internal/cataloguepreflight"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// CatalogueNativeBoundary is supplied by the independently accepted OS1-OS6
// supervisor, NEVER by an env flag or self-authored acceptance JSON. There is
// intentionally no implementation or runnable Test entrypoint in this patch.
// This prevents go test ./... from launching any stack/catalogue action. The
// future accepted adapter must invoke this function in its contained child.
// BeforeSetup must already cover this process, startup and every descendant.
// SealLive must add PID+birth/socket ownership and seal the static input hashes.
// BeforeAction must verify the same attachment and live immutable registration.
type CatalogueNativeBoundary interface {
	BeforeSetup(context.Context, string) error
	SealLive(context.Context, map[string]any) (string, error)
	BeforeAction(context.Context, string) error
}

type CatalogueRouteRuntime struct {
	URL, Bundle, Seal string
	Fixture           *fixture.Fixture
	HTTP              *http.Server
	Gateway           *server.Server
	ProbeCalls        atomic.Int64
	Done              chan error
}

// LaunchCatalogueRoute uses the real gateway router and full MCP wiring. Only
// the probe step's injected Dialer differs; the original step is also called.
// Its authenticated /-/mcp/probe route and real HTTPClient/DialTLSContext are
// unchanged. The Django launcher calls the real authenticated catalogue add,
// never Server.Probe, HandleProbe, a fake listing, or a patched service method.
func LaunchCatalogueRoute(ctx context.Context, boundary CatalogueNativeBoundary, caseName, scenarioRoot, output, head, adminToken string) (*CatalogueRouteRuntime, error) {
	if boundary == nil {
		return nil, errors.New("accepted OS1-OS6 supervisor adapter is required; execution remains blocked")
	}
	if err := boundary.BeforeSetup(ctx, head); err != nil {
		return nil, err
	}
	if len(adminToken) < 32 {
		return nil, errors.New("fresh synthetic admin token required")
	}
	f, err := fixture.Start(caseName, scenarioRoot)
	if err != nil {
		return nil, err
	}
	ok := false
	defer func() {
		if !ok {
			f.Close()
		}
	}()
	catalogDir := filepath.Join(output, "catalog")
	bundlePath, err := fixture.BuildCatalog(scenarioRoot, catalogDir, f.Port, head)
	if err != nil {
		return nil, err
	}
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{}
	cfg.MCP.Enabled = true
	cfg.Admin.Token = adminToken
	cfg.Connectors.CatalogDir = catalogDir
	var state [32]byte
	if _, err := rand.Read(state[:]); err != nil {
		return nil, err
	}
	cfg.MCP.StateKeys = []string{base64.StdEncoding.EncodeToString(state[:])}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		return nil, err
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	srv := server.New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, &mdb, tenant.NewStore(), nil)
	defer func() {
		if !ok {
			srv.MCP().Close()
		}
	}()
	d := &wiringDeps{cfg: cfg, server: srv, ctx: ctx}
	if err := initConnectors(d); err != nil {
		return nil, err
	}
	steps := append([]mcpWiringStep{}, mcpWiringOrder...)
	found := 0
	for i := range steps {
		if steps[i].name == "probe" {
			found++
			steps[i].init = func(d *wiringDeps, deps *mcp.Deps) error {
				if err := initMCPProbe(d, deps); err != nil {
					return err
				}
				deps.Dialer = f.Dialer
				return nil
			}
		}
	}
	if found != 1 {
		return nil, errors.New("real probe wiring step identity changed")
	}
	if err := runMCPWiring(d, steps); err != nil {
		return nil, err
	}
	if err := d.startServerWiring(); err != nil {
		return nil, err
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	defer func() {
		if !ok {
			listener.Close()
		}
	}()
	r := &CatalogueRouteRuntime{URL: "http://" + listener.Addr().String(), Bundle: bundlePath, Fixture: f, Gateway: srv, Done: make(chan error, 1)}
	bundleBytes, err := os.ReadFile(bundlePath)
	if err != nil {
		return nil, err
	}
	executable, err := os.Executable()
	if err != nil {
		return nil, err
	}
	binary, err := os.ReadFile(executable)
	if err != nil {
		return nil, err
	}
	registration := map[string]any{"head": head, "case": caseName, "pid": os.Getpid(),
		"binary_sha256": fixture.Digest(binary), "gateway": r.URL, "dns": f.DNS.Address(),
		"tls": f.TLS.Address(), "name": f.Name, "port": f.Port, "bundle": bundlePath,
		"bundle_sha256": fixture.Digest(bundleBytes), "ca_sha256": fixture.Digest(f.Certificates.CA.Raw),
		"leaf_sha256": fixture.Digest(f.Certificates.Leaf.Raw), "san": f.Certificates.Leaf.DNSNames,
		"proof": "synthetic integration only; no OS containment certified by this launcher"}
	r.Seal, err = boundary.SealLive(ctx, registration)
	if err != nil || r.Seal == "" {
		return nil, errors.New("live registration not sealed by accepted supervisor")
	}
	real := srv.CataloguePreflightHandler()
	r.HTTP = &http.Server{ReadHeaderTimeout: 5 * time.Second, Handler: http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if err := boundary.BeforeAction(req.Context(), r.Seal); err != nil {
			http.Error(w, "containment attachment lost", 503)
			return
		}
		if req.URL.Path == "/__catalogue-observe" {
			if req.Method != "GET" || req.Header.Get("Authorization") != "Bearer "+adminToken {
				http.Error(w, "refused", 403)
				return
			}
			obs := f.Observation()
			obs["probe_calls"], obs["seal"] = r.ProbeCalls.Load(), r.Seal
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(obs)
			return
		}
		if req.Method == "POST" && req.URL.Path == "/-/mcp/probe" {
			r.ProbeCalls.Add(1)
		}
		real.ServeHTTP(w, req)
	})}
	go func() { r.Done <- r.HTTP.Serve(listener) }()
	ok = true
	return r, nil
}

func (r *CatalogueRouteRuntime) Close() {
	r.HTTP.Close()
	<-r.Done
	r.Gateway.MCP().Close()
	r.Fixture.Close()
}
