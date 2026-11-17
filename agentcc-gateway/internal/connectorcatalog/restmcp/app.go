package restmcp

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"regexp"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// DefaultMaxBytes caps a tool's result when its response block names no
// maxBytes.
const DefaultMaxBytes = 100_000

// restURLPrefix starts every rest provider's transport.url (the L0 rule:
// exactly https://{connectorsHost}/<app-id>/mcp).
const restURLPrefix = "https://{connectorsHost}"

// bodyInput is the input that carries a request body (the generator's
// request contract).
const bodyInput = "body"

var placeholderRE = regexp.MustCompile(`\{([^{}/]+)\}`)

// hostNameRE is the manifest's hostname pattern (manifest/defs.go "hostname")
// with one label allowed: an x-origin's host must be an ASCII DNS name. The
// vendor.domains it must sit under are manifest hostnames (two labels or more),
// so the catalog bounds the rest; one label keeps the egress tests' localhost.
var hostNameRE = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?([.][a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$`)

// App is one rest provider, compiled for serving at Path.
type App struct {
	ID, Provider string
	Name         string // the app's display name
	VendorName   string // vendor.name, for attribution
	Path         string // /<app-id>/mcp

	cred   *credentialSpec
	tools  []*Tool
	byName map[string]*Tool
	listed []json.RawMessage // tools/list entries, in tool order
	hash   string            // binds list cursors to this tool list
}

// Tool is one tool and the REST call it makes.
type Tool struct {
	Name        string
	annotations manifest.Annotations
	method      string
	origin      string // scheme://host[:port], under vendor.domains
	host        string // origin's host, for attribution
	path        string // operation.path, {name} placeholders included
	pathParams  []string
	reserved    map[string]*regexp.Regexp
	schema      *inputSchema
	unwrap      string
	fields      []string
	maxBytes    int
	structured  bool // the tool declares an outputSchema: results carry structuredContent
	retry       retryPolicy
}

// Refusal is a provider, or one of its tools, that Compile refuses, and why.
type Refusal struct {
	App, Provider string
	Tool          string // empty when the provider as a whole is refused
	Reason        string
}

func (r Refusal) String() string {
	if r.Tool == "" {
		return r.App + "/" + r.Provider + ": " + r.Reason
	}
	return r.App + "/" + r.Provider + ": tool " + r.Tool + ": " + r.Reason
}

// CompileError is everything Compile refused, one line per refusal, each
// naming its app, provider and tool.
type CompileError struct{ Refusals []Refusal }

func (e *CompileError) Error() string {
	lines := make([]string, len(e.Refusals))
	for i, r := range e.Refusals {
		lines[i] = r.String()
	}
	return strings.Join(lines, "\n")
}

// Compile turns every rest provider of cat into an App. Sunset providers
// are left out. Anything the runtime cannot serve as declared is refused,
// naming the app, the provider and the tool: a tool whose request would go
// anywhere but an https vendor host under its app's vendor.domains above
// all (the catalog check does not read x-origin, so this is where it is
// enforced). A catalog with any refusal is served not at all: Compile then
// returns no App and a *CompileError listing every refusal.
func Compile(cat *manifest.Catalog) ([]*App, error) {
	var apps []*App
	var refused []Refusal
	paths := map[string]string{}
	for _, a := range cat.Apps {
		for _, p := range a.Providers {
			if p.Kind != manifest.KindREST || p.Lifecycle.Status == "sunset" {
				continue
			}
			app, rs := compileApp(a, p)
			if len(rs) > 0 {
				refused = append(refused, rs...)
				continue
			}
			if other, dup := paths[app.Path]; dup {
				refused = append(refused, Refusal{App: a.ID, Provider: p.ID,
					Reason: fmt.Sprintf("%s is already served by %s", app.Path, other)})
				continue
			}
			paths[app.Path] = a.ID + "/" + p.ID
			apps = append(apps, app)
		}
	}
	if len(refused) > 0 {
		return nil, &CompileError{Refusals: refused}
	}
	return apps, nil
}

func compileApp(a *manifest.App, p *manifest.Provider) (*App, []Refusal) {
	refuse := func(reason string) []Refusal { return []Refusal{{App: a.ID, Provider: p.ID, Reason: reason}} }
	rest, ok := strings.CutPrefix(p.Transport.URL, restURLPrefix)
	if !ok || rest != "/"+a.ID+"/mcp" {
		return nil, refuse(fmt.Sprintf("transport.url %s is not %s/%s/mcp", p.Transport.URL, restURLPrefix, a.ID))
	}
	cred, err := compileCredential(a.ID, p.Auth)
	if err != nil {
		return nil, refuse(err.Error())
	}
	app := &App{ID: a.ID, Provider: p.ID, Name: a.Name, VendorName: a.Vendor.Name, Path: rest,
		cred: cred, byName: map[string]*Tool{}}
	var refused []Refusal
	toolRefused := func(name, reason string) {
		refused = append(refused, Refusal{App: a.ID, Provider: p.ID, Tool: name, Reason: reason})
	}
	sum := sha256.New()
	retry := compileRetry(p.Retry)
	for _, mt := range p.AllTools() {
		t, err := compileTool(a, mt, retry)
		if err != nil {
			toolRefused(mt.Name, err.Error())
			continue
		}
		if app.byName[t.Name] != nil {
			toolRefused(t.Name, "is declared twice")
			continue
		}
		if clash := cred.queryClash(t); clash != "" {
			toolRefused(t.Name, clash)
			continue
		}
		entry, err := listEntry(mt, t)
		if err != nil {
			toolRefused(mt.Name, err.Error())
			continue
		}
		app.tools = append(app.tools, t)
		app.byName[t.Name] = t
		app.listed = append(app.listed, entry)
		sum.Write(entry)
	}
	if len(refused) > 0 {
		return nil, refused
	}
	if len(app.tools) == 0 {
		return nil, refuse("declares no tools")
	}
	app.hash = hex.EncodeToString(sum.Sum(nil))[:16]
	return app, nil
}

func compileTool(a *manifest.App, mt manifest.Tool, retry retryPolicy) (*Tool, error) {
	if mt.Operation == nil {
		return nil, errors.New("has no operation (a rest tool makes one REST call)")
	}
	schema, err := parseInputSchema(mt.InputSchema)
	if err != nil {
		return nil, err
	}
	origin, host, err := vendorOrigin(schema.origin, a.Vendor.Domains)
	if err != nil {
		return nil, err
	}
	t := &Tool{Name: mt.Name, annotations: mt.Annotations, method: mt.Operation.Method,
		origin: origin, host: host, path: mt.Operation.Path, schema: schema, maxBytes: DefaultMaxBytes, retry: retry}
	t.structured = len(mt.OutputSchema) > 0
	if r := mt.Response; r != nil {
		t.unwrap, t.fields = r.Unwrap, r.Fields
		if r.MaxBytes > 0 {
			t.maxBytes = r.MaxBytes
		}
	}
	// The annotations must not contradict the method: the
	// generator refuses these, and the runtime refuses them again at load, so
	// a hand-edited tools.generated.json is not served as read-only.
	switch m := t.method; {
	case mt.Annotations.ReadOnlyHint && (m == "PUT" || m == "PATCH" || m == "DELETE"):
		return nil, fmt.Errorf("annotations.readOnlyHint is true on a %s, which writes, so it is never read-only", m)
	case m == "DELETE" && !mt.Annotations.DestructiveHint:
		return nil, errors.New("annotations.destructiveHint is false on a DELETE, which is always destructive")
	}
	if !strings.HasPrefix(t.path, "/") {
		// The URL is the origin followed by the path: without the "/", the
		// path would name the host ("@attacker.example/x").
		return nil, fmt.Errorf("operation.path does not start with /, so it would move the request off %s", keyOrigin)
	}
	if strings.ContainsAny(placeholderRE.ReplaceAllString(t.path, ""), "{}?#") {
		return nil, fmt.Errorf("operation.path %s has an unbalanced placeholder, a query or a fragment", t.path)
	}
	for _, m := range placeholderRE.FindAllStringSubmatch(t.path, -1) {
		name := strings.TrimPrefix(m[1], "+")
		in := schema.inputs[name]
		if in == nil {
			return nil, fmt.Errorf("operation.path names {%s}, which is not an input", name)
		}
		if !contains(schema.required, name) && in.constVal == nil {
			return nil, fmt.Errorf("path input %s is not required", name)
		}
		if strings.HasPrefix(m[1], "+") {
			pattern, err := compileResourcePattern(in)
			if err != nil {
				return nil, fmt.Errorf("path input %s: %w", name, err)
			}
			if t.reserved == nil {
				t.reserved = map[string]*regexp.Regexp{}
			}
			t.reserved[name] = pattern
			if in.constVal != nil {
				if _, err := resourcePath(name, in.constVal, pattern); err != nil {
					return nil, err
				}
			}
		}
		t.pathParams = append(t.pathParams, name)
	}
	if in := schema.inputs[bodyInput]; in != nil {
		switch {
		case contains(t.pathParams, bodyInput):
			return nil, errors.New("the body input cannot also be a path input")
		case t.method == "GET" || t.method == "DELETE":
			return nil, fmt.Errorf("a %s carries no body, so the body input could never be sent", t.method)
		}
	}
	return t, nil
}

// vendorOrigin checks a tool's x-origin: https, a host name (never an IP
// literal) under one of the app's vendor domains, no path, query, fragment
// or user info. A refused origin is described, never echoed: it may carry
// user info or a query from the server a spec declared.
func vendorOrigin(raw string, domains []string) (origin, host string, err error) {
	if raw == "" {
		return "", "", fmt.Errorf("inputSchema has no %s: the runtime sends a call only to the origin the generator named", keyOrigin)
	}
	u, err := url.Parse(raw)
	if err != nil {
		return "", "", fmt.Errorf("%s is not a URL", keyOrigin)
	}
	host = strings.ToLower(u.Hostname())
	switch {
	case u.Scheme != "https":
		return "", "", fmt.Errorf("%s for host %s is not https", keyOrigin, host)
	case u.User != nil:
		return "", "", fmt.Errorf("%s for host %s carries user info; it must be a scheme and host only", keyOrigin, host)
	case u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || u.Opaque != "":
		return "", "", fmt.Errorf("%s for host %s has a path, a query or a fragment; it must be a scheme and host only", keyOrigin, host)
	case net.ParseIP(host) != nil:
		return "", "", fmt.Errorf("%s host %s is an IP address; it must be a vendor host name", keyOrigin, host)
	case !hostNameRE.MatchString(host):
		// A zoned IPv6 literal is no IP to net.ParseIP and its zone can end in
		// a vendor domain; a non-ASCII name may map to another host when it is
		// dialled. Only an ASCII host name passes (the manifest's hostname
		// pattern), and the refused host is not echoed.
		return "", "", fmt.Errorf("%s host is not a host name (an ASCII DNS name); it must be a vendor host name", keyOrigin)
	}
	for _, d := range domains {
		d = strings.ToLower(d)
		if host == d || strings.HasSuffix(host, "."+d) {
			return "https://" + strings.ToLower(u.Host), host, nil
		}
	}
	return "", "", fmt.Errorf("%s host %s is not under the app's vendor.domains %v", keyOrigin, host, domains)
}

// listEntry is the tool as tools/list serves it: name, title, description,
// the inputSchema without the generator's annotations, the outputSchema and
// all four annotations as the manifest declares them.
func listEntry(mt manifest.Tool, t *Tool) (json.RawMessage, error) {
	entry := map[string]any{
		"name":        mt.Name,
		"inputSchema": json.RawMessage(t.schema.listed),
		"annotations": map[string]any{
			"readOnlyHint":    mt.Annotations.ReadOnlyHint,
			"destructiveHint": mt.Annotations.DestructiveHint,
			"idempotentHint":  mt.Annotations.IdempotentHint,
			"openWorldHint":   mt.Annotations.OpenWorldHint,
		},
	}
	if mt.Title != "" {
		entry["title"] = mt.Title
		entry["annotations"].(map[string]any)["title"] = mt.Title
	}
	if mt.Description != "" {
		entry["description"] = mt.Description
	}
	if len(mt.OutputSchema) > 0 {
		entry["outputSchema"] = stripAnnotations(decodeAny(mt.OutputSchema), false)
	}
	return json.Marshal(entry)
}

// Tool returns the app's tool with the name, or nil.
func (a *App) Tool(name string) *Tool { return a.byName[name] }

// ToolCount is the number of tools the app serves.
func (a *App) ToolCount() int { return len(a.tools) }

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
