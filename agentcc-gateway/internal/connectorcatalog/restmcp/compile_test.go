package restmcp

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// TestCompileCN4ExampleCatalog: every app of the rest example catalog, one or
// more per spec format the generator reads (Discovery, OpenAPI 3, Swagger 2,
// Postman 2.1), compiles: every tool names a vendor origin under its app's
// domains and a path the runtime can fill.
func TestCompileCN4ExampleCatalog(t *testing.T) {
	cat := cn4Catalog(t)
	apps, err := Compile(cat)
	if err != nil {
		t.Fatalf("Compile: %v", err)
	}
	want := map[string]bool{"adyen": true, "gmail": true, "google-calendar": true, "microsoft-outlook": true,
		"microsoft-teams": true, "slack": true}
	for _, a := range apps {
		if !want[a.ID] {
			t.Errorf("compiled an unexpected app %s", a.ID)
		}
		delete(want, a.ID)
		if a.Path != "/"+a.ID+"/mcp" {
			t.Errorf("%s is served at %s", a.ID, a.Path)
		}
		if a.ToolCount() < 8 {
			t.Errorf("%s compiled %d tools; the example overlays declare 8 or more", a.ID, a.ToolCount())
		}
	}
	for id := range want {
		t.Errorf("app %s was not compiled", id)
	}
}

// TestRepoCatalogCompiles: the repository's catalog, as embedded, compiles,
// so a rest provider the runtime could not serve fails here, before it
// ships.
func TestRepoCatalogCompiles(t *testing.T) {
	cat, err := connectors.Embedded()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Compile(cat.Records); err != nil {
		t.Fatalf("the embedded catalog's rest providers do not compile: %v", err)
	}
	dir, err := connectors.LoadDir(repoPath(t, "agentcc-gateway/catalog/connectors"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Compile(dir.Records); err != nil {
		t.Fatalf("catalog/connectors' rest providers do not compile: %v", err)
	}
}

// gmailCopy is the example Gmail app with its provider and tools, for tests that
// change one thing.
func gmailCopy(t *testing.T) (*manifest.App, *manifest.Provider) {
	t.Helper()
	cat := cn4Catalog(t)
	src := cat.App("gmail")
	if src == nil {
		t.Fatal("no gmail app in the rest example catalog")
	}
	app := *src
	p := *src.Providers[0]
	p.Tools.Items = append([]manifest.Tool(nil), p.AllTools()...)
	p.GeneratedTools = nil
	app.Providers = []*manifest.Provider{&p}
	return &app, &p
}

// withInputSchema returns tool i with its inputSchema edited as JSON.
func editSchema(t *testing.T, tool *manifest.Tool, edit func(map[string]any)) {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(tool.InputSchema, &m); err != nil {
		t.Fatal(err)
	}
	edit(m)
	raw, _ := json.Marshal(m)
	tool.InputSchema = raw
}

func toolNamed(t *testing.T, p *manifest.Provider, name string) *manifest.Tool {
	t.Helper()
	for i := range p.Tools.Items {
		if p.Tools.Items[i].Name == name {
			return &p.Tools.Items[i]
		}
	}
	t.Fatalf("no tool %s", name)
	return nil
}

// TestCompileRefusesWhatItCannotServe: a provider is refused whole, naming
// the app, the provider and the tool, when a tool's origin is not https, is
// an IP address or is outside the app's domains, when its path names an
// input it lacks or one that is optional, when a GET has a body input, and
// when the auth kind is not one the runtime serves.
func TestCompileRefusesWhatItCannotServe(t *testing.T) {
	cases := []struct {
		name, want string
		edit       func(*manifest.App, *manifest.Provider)
	}{
		{"origin outside vendor.domains", "is not under the app's vendor.domains", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) { m["x-origin"] = "https://gmail.example.com" })
		}},
		{"origin on a lookalike domain", "is not under the app's vendor.domains", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) { m["x-origin"] = "https://evilgoogleapis.com" })
		}},
		{"plaintext origin", "is not https", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) { m["x-origin"] = "http://gmail.googleapis.com" })
		}},
		{"metadata IP origin", "is an IP address", func(a *manifest.App, p *manifest.Provider) {
			a.Vendor.Domains = append(a.Vendor.Domains, "169.254.169.254")
			editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) { m["x-origin"] = "https://169.254.169.254" })
		}},
		// A zoned IPv6 literal is no IP for net.ParseIP and its
		// zone can end in a vendor domain, so only the host-name pattern refuses
		// it; a non-ASCII host name is refused the same way.
		{"zoned public IPv6 origin", "is not a host name", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) {
				m["x-origin"] = "https://[2606:4700::1%25x.googleapis.com]"
			})
		}},
		{"zoned loopback IPv6 origin", "is not a host name", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) {
				m["x-origin"] = "https://[::1%25x.gmail.googleapis.com]"
			})
		}},
		{"non-ASCII host name", "is not a host name", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) {
				m["x-origin"] = "https://attacker.example\u3002gmail.googleapis.com"
			})
		}},
		{"origin with a path", "scheme and host only", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) { m["x-origin"] = "https://gmail.googleapis.com/x" })
		}},
		{"no origin", "has no x-origin", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) { delete(m, "x-origin") })
		}},
		{"path input missing", "which is not an input", func(_ *manifest.App, p *manifest.Provider) {
			toolNamed(t, p, "get_profile").Operation.Path = "/gmail/v1/users/{userId}/profile"
		}},
		{"optional path input", "is not required", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "get_thread"), func(m map[string]any) { m["required"] = []string{} })
		}},
		{"GET with a body", "carries no body", func(_ *manifest.App, p *manifest.Provider) {
			editSchema(t, toolNamed(t, p, "list_labels"), func(m map[string]any) {
				m["properties"] = map[string]any{"body": map[string]any{"type": "object", "description": "x"}}
			})
		}},
		{"basic auth", "serves oauth2 and api_key providers only", func(_ *manifest.App, p *manifest.Provider) {
			p.Auth = manifest.Auth{Kind: manifest.AuthBasic}
		}},
		{"credential in a URL variable", "in a header or a query parameter only", func(_ *manifest.App, p *manifest.Provider) {
			p.Auth = manifest.Auth{Kind: manifest.AuthAPIKey, CredentialParts: []manifest.CredentialPart{{In: "url_variable", Name: "key", Secret: true}}}
		}},
		{"credential in a transport header", "a header the transport sets itself", func(_ *manifest.App, p *manifest.Provider) {
			p.Auth = manifest.Auth{Kind: manifest.AuthAPIKey, CredentialParts: []manifest.CredentialPart{{In: "header", Name: "Mcp-Session-Id", Secret: true}}}
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			app, p := gmailCopy(t)
			c.edit(app, p)
			_, err := Compile(&manifest.Catalog{Apps: []*manifest.App{app}})
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("Compile error = %v, want one containing %q", err, c.want)
			}
			if !strings.HasPrefix(err.Error(), "gmail/futureagi-rest: ") {
				t.Errorf("the error does not name the app and provider: %v", err)
			}
			if strings.Contains(err.Error(), "2606:4700") || strings.Contains(err.Error(), "%") ||
				strings.Contains(err.Error(), "attacker") {
				t.Errorf("the refusal echoes the refused origin: %v", err)
			}
		})
	}
}
