package main

import (
	"net/http"
	"os"
	"strings"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/restmcp"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/runtimesupport"
)

// Set only at build time: -ldflags "-X main.runtimeSupportBuildID=sha256:<64 lowercase hex>".
// The digest must identify the composed source, embedded catalog, dependency
// locks, toolchain and build flags. Empty/dev/alias builds remain unavailable.
// Do not reuse main.version or read a mutable runtime setting as build identity.
var runtimeSupportBuildID = ""

func runtimeSupportHandler(cat *connectors.Catalog, visible []*restmcp.App) http.Handler {
	o := runtimesupport.Options{AdminToken: os.Getenv("AGENTCC_ADMIN_TOKEN"),
		RuntimeURL:    strings.TrimRight(os.Getenv("AGENTCC_CONNECTORS_RUNTIME_URL"), "/"),
		BuildRevision: runtimeSupportBuildID, Catalog: cat}
	for _, app := range visible {
		o.Routes = append(o.Routes, runtimesupport.Route{AppID: app.ID, ProviderID: app.Provider})
	}
	return runtimesupport.New(o)
}
