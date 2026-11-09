// Package emit writes what each consumer of the connector catalog receives
// (CONNECTORS-PLAN §2.4, §2.6, §2.7), from a catalog in bundle form
// (bundle.Bundle.Catalog), so every output can be rebuilt from the
// published dist/catalog.json alone:
//
//   - gateway: gateway-servers.json, per http provider the org server entry
//     fields the manifest decides, for adding the server from the catalog,
//     with no credential value;
//   - ee-apps: ee-apps.json, Omega EE's IntegrationAppDefinition for every
//     app EE can use, the table EE's generated built-in apps are checked
//     against;
//   - ee-settings: ee-settings.json, an OMEGA_INTEGRATIONS_APPS_FILE that EE
//     loads today: at most 64 apps, each an OAuth app with a fixed vendor
//     MCP URL;
//   - oss-registry: oss-registry.json, OSS Omega's McpCatalogServer entries,
//     less the servers OSS cannot sign in to (OSSLeftOutServers);
//   - server-json: server-json/<app-id>.json, an official MCP Registry
//     server.json for each server FutureAGI hosts (rest providers);
//   - docs: docs/index.md and one card per app and platform.
//
// Each JSON output is checked against its consumer's rules before it is
// returned. Each but server.json, which follows the registry's own schema,
// names its schema and the bundle version and sha256 it was written from.
package emit

import (
	"errors"
	"fmt"
	"path"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// Targets, in the order agentcc-catalog writes them.
const (
	TargetGateway     = "gateway"
	TargetEEApps      = "ee-apps"
	TargetEESettings  = "ee-settings"
	TargetOSSRegistry = "oss-registry"
	TargetServerJSON  = "server-json"
	TargetDocs        = "docs"
)

// Targets lists every target.
var Targets = []string{TargetGateway, TargetEEApps, TargetEESettings, TargetOSSRegistry, TargetServerJSON, TargetDocs}

// Meta names the bundle the outputs are written from.
type Meta struct {
	Version string // the bundle's YYYY.MM.DD.N
	SHA256  string // the bundle's sha256
}

// Files maps a slash-separated path under dist/emit to its bytes.
type Files map[string][]byte

// Paths returns the files' paths in order.
func (f Files) Paths() []string {
	out := make([]string, 0, len(f))
	for p := range f {
		out = append(out, p)
	}
	slices.Sort(out)
	return out
}

// Target writes one target's files.
func Target(name string, cat *manifest.Catalog, meta Meta) (Files, error) {
	var files Files
	var err error
	switch name {
	case TargetGateway:
		files, err = gatewayServers(cat, meta)
	case TargetEEApps:
		files, err = eeApps(cat, meta)
	case TargetEESettings:
		files, err = eeSettings(cat, meta)
	case TargetOSSRegistry:
		files, err = ossRegistry(cat, meta)
	case TargetServerJSON:
		files, err = serverJSON(cat, meta)
	case TargetDocs:
		files, err = docs(cat, meta)
	default:
		return nil, fmt.Errorf("emit: no target %q; targets are %s", name, strings.Join(Targets, ", "))
	}
	if err != nil {
		return nil, fmt.Errorf("emit %s: %w", name, err)
	}
	for p := range files {
		if err := checkPath(p); err != nil {
			return nil, fmt.Errorf("emit %s: %w", name, err)
		}
	}
	return files, nil
}

// All writes every target's files.
func All(cat *manifest.Catalog, meta Meta) (Files, error) {
	all := Files{}
	var errs []error
	for _, name := range Targets {
		files, err := Target(name, cat, meta)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		for p, data := range files {
			all[p] = data
		}
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	return all, nil
}

// ignoredDirs are directory names the repository's .gitignore hides at any
// depth; nothing is written under one, so every output can be committed
// (AM-C20).
var ignoredDirs = []string{"build", "coverage", "logs", "media"}

// checkPath keeps an output path relative, clean and out of every ignored
// directory.
func checkPath(p string) error {
	if p == "" || path.IsAbs(p) || path.Clean(p) != p || strings.HasPrefix(p, "../") || p == ".." {
		return fmt.Errorf("output path %q is not a clean relative path", p)
	}
	dirs := strings.Split(path.Dir(p), "/")
	for _, d := range dirs {
		if slices.Contains(ignoredDirs, d) {
			return fmt.Errorf("output path %q is under a %s/ directory, which .gitignore hides", p, d)
		}
	}
	return nil
}

// jsonFile renders an output document, Prettier-stable.
func jsonFile(doc canonjson.Object) ([]byte, error) {
	return canonjson.Pretty(doc)
}

// envelope starts a JSON output: its schema and its bundle.
func envelope(schema string, meta Meta) canonjson.Object {
	return canonjson.Object{
		{Key: "schema", Value: schema},
		{Key: "bundle", Value: canonjson.Object{{Key: "version", Value: meta.Version}, {Key: "sha256", Value: meta.SHA256}}},
	}
}

// strings converts a string list to a JSON array.
func stringList(list []string) []any {
	out := make([]any, len(list))
	for i, s := range list {
		out[i] = s
	}
	return out
}

func catalogRef(p *manifest.Provider) string {
	return p.App + "/" + p.ID + "@" + p.Version
}
