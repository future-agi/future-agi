package manifest

import (
	"os"
	"slices"
	"strings"
	"testing"
)

// TestRepoCatalogListsVendorConnectors checks the connectors added from the
// vendors' own setup pages: each record validates with the rest of the
// catalog, declares exactly the transport and authentication its vendor
// documents (citing that page for the fact), and is listed by the OSS
// emitter under its server id. Values come from the vendor pages read on
// 2026-11-16; none of them holds a credential.
func TestRepoCatalogListsVendorConnectors(t *testing.T) {
	cat, err := validateRepoCatalog(os.DirFS(repoCatalogRoot))
	if err != nil {
		t.Fatalf("catalog/connectors is not valid:\n%v", err)
	}
	for _, c := range []struct {
		app, provider, kind, transport string
		url, command                   string
		args, env                      []string
		auth                           string
		header, prefix                 string
		ossID                          string
		fact, source                   string
		warningEnv                     []string // Credential/profile variables requiring permission warnings.
	}{
		{
			app: "superhuman-docs", provider: "api-key", kind: KindVendorMCP, transport: "http",
			url:  "https://docs.superhuman.com/apis/mcp",
			auth: AuthAPIKey, header: "Authorization", prefix: "Bearer ",
			ossID: "superhuman-docs",
			fact:  "auth.credentialParts", source: "https://help.superhuman.com/hc/en-us/articles/46210076980365-Connect-to-the-Superhuman-Docs-MCP",
		},
		{
			app: "klaviyo", provider: "local", kind: KindVendorLocal, transport: "stdio",
			command: "uvx", args: []string{"klaviyo-mcp-server@latest"}, env: []string{"PRIVATE_API_KEY"},
			auth:  AuthLocalSetup,
			ossID: "klaviyo",
			fact:  "transport.command", source: "https://developers.klaviyo.com/en/docs/connect_to_the_klaviyo_mcp_server",
		},
		{
			app: "todoist", provider: "local", kind: KindVendorLocal, transport: "stdio",
			command: "npx", args: []string{"-p", "@todoist/mcp", "todoist-mcp"}, env: []string{"TODOIST_API_KEY"},
			auth:  AuthLocalSetup,
			ossID: "todoist-local",
			fact:  "transport.command", source: "https://github.com/todoist/todoist-mcp/blob/main/docs/mcp-server.md",
		},
		{
			app: "google-analytics", provider: "local", kind: KindVendorLocal, transport: "stdio",
			command: "pipx", args: []string{"run", "analytics-mcp"}, env: []string{"GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"},
			auth:  AuthLocalSetup,
			ossID: "google-analytics-local",
			fact:  "transport.command", source: "https://github.com/googleanalytics/google-analytics-mcp",
		},
		{
			app: "microsoft-azure", provider: "local", kind: KindVendorLocal, transport: "stdio",
			command: "npx", args: []string{"-y", "@azure/mcp@latest", "server", "start"}, env: []string{"AZURE_TOKEN_CREDENTIALS", "AZURE_SUBSCRIPTION_ID"},
			auth:  AuthLocalSetup,
			ossID: "microsoft-azure",
			fact:  "transport.command", source: "https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/concepts?accept=text/markdown",
			warningEnv: []string{"AZURE_TOKEN_CREDENTIALS"},
		},
		{
			app: "aws", provider: "local", kind: KindVendorLocal, transport: "stdio",
			command: "uvx", args: []string{"mcp-proxy-for-aws-cli@latest", "https://aws-mcp.us-east-1.api.aws/mcp"}, env: []string{"AWS_PROFILE", "AWS_REGION"},
			auth:  AuthLocalSetup,
			ossID: "aws",
			fact:  "transport.command", source: "https://raw.githubusercontent.com/aws/mcp-proxy-for-aws/main/README.md",
			warningEnv: []string{"AWS_PROFILE"},
		},
		{
			app: "paypal", provider: "local", kind: KindVendorLocal, transport: "stdio",
			command: "npx", args: []string{"-y", "@paypal/mcp", "--tools=all"}, env: []string{"PAYPAL_ACCESS_TOKEN", "PAYPAL_ENVIRONMENT"},
			auth:  AuthLocalSetup,
			ossID: "paypal",
			fact:  "transport.command", source: "https://developer.paypal.com/ai-tools/mcp-server",
			warningEnv: []string{"PAYPAL_ACCESS_TOKEN"},
		},
		{
			app: "square", provider: "local", kind: KindVendorLocal, transport: "stdio",
			command: "npx", args: []string{"square-mcp-server", "start"}, env: []string{"ACCESS_TOKEN", "SANDBOX"},
			auth:  AuthLocalSetup,
			ossID: "square-local",
			fact:  "transport.command", source: "https://raw.githubusercontent.com/square/square-mcp-server/HEAD/README.md",
			warningEnv: []string{"ACCESS_TOKEN"},
		},
	} {
		t.Run(c.app+"/"+c.provider, func(t *testing.T) {
			app := cat.App(c.app)
			if app == nil {
				t.Fatalf("no app %s", c.app)
			}
			p := app.Provider(c.provider)
			if p == nil {
				t.Fatalf("app %s has no provider %s", c.app, c.provider)
			}
			if p.Kind != c.kind || p.Publisher != "vendor" || p.Transport.Kind != c.transport || p.Auth.Kind != c.auth {
				t.Errorf("kind %s publisher %s transport %s auth %s; want %s vendor %s %s",
					p.Kind, p.Publisher, p.Transport.Kind, p.Auth.Kind, c.kind, c.transport, c.auth)
			}
			if p.Trusted {
				t.Error("a new vendor record is not trusted before a maintainer review")
			}
			if p.Hosted != (c.transport == "http") {
				t.Errorf("hosted %v for a %s transport", p.Hosted, c.transport)
			}
			if p.Transport.URL != c.url || p.Transport.Command != c.command || !slices.Equal(p.Transport.Args, c.args) {
				t.Errorf("url %q command %q args %q; want %q %q %q",
					p.Transport.URL, p.Transport.Command, p.Transport.Args, c.url, c.command, c.args)
			}
			for _, name := range c.env {
				if p.Transport.Env[name] == "" {
					t.Errorf("transport.env has no description for %s", name)
				}
			}
			for _, name := range c.warningEnv {
				description := strings.ToLower(p.Transport.Env[name])
				if !strings.Contains(description, "full") || !strings.Contains(description, "least") {
					t.Errorf("transport.env.%s must warn about full permissions and recommend least privilege", name)
				}
			}
			if c.header != "" {
				if len(p.Auth.CredentialParts) != 1 {
					t.Fatalf("credential parts %+v; want exactly one", p.Auth.CredentialParts)
				}
				part := p.Auth.CredentialParts[0]
				if part.In != "header" || part.Name != c.header || part.Prefix != c.prefix || !part.Secret || part.Value != "" {
					t.Errorf("credential part %+v; want a secret %s header with prefix %q and no value", part, c.header, c.prefix)
				}
			} else if len(p.Auth.CredentialParts) != 0 {
				t.Errorf("a local setup declares no credential part, got %+v", p.Auth.CredentialParts)
			}
			if !slices.ContainsFunc(p.Sources, func(s Source) bool { return s.Fact == c.fact && s.URL == c.source }) {
				t.Errorf("no source cites %s for %s", c.source, c.fact)
			}
			if !slices.ContainsFunc(OSSListed(app), func(s OSSServer) bool { return s.ID == c.ossID && s.Provider == p }) {
				t.Errorf("OSS does not list %s as %s", c.app+"/"+c.provider, c.ossID)
			}
		})
	}
}
