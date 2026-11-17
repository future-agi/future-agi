package manifest

import (
	"os"
	"testing"
)

// TestRepoCatalogListsUserKeyServersInOSS pins the owner's 2026-11-16 decision
// on providers that were hidden from OSS without a named reason: each one
// below uses the user's own key at a fixed vendor address and the bearer
// setup OSS Omega already supports, so the OSS emitter lists it. Providers
// hidden for a named reason (a regional or tenant address, or a vendor usage
// restriction) stay out. GitHub's, Linear's and Notion's token providers take
// OSS's built-in ids for their URL. Airtable's token provider is not listed:
// as its app's default it would rename the listed OAuth server airtable.
func TestRepoCatalogListsUserKeyServersInOSS(t *testing.T) {
	cat, err := validateRepoCatalog(os.DirFS(repoCatalogRoot))
	if err != nil {
		t.Fatalf("catalog/connectors is not valid:\n%v", err)
	}
	listed := func(app, provider string) (string, bool) {
		a := cat.App(app)
		if a == nil {
			t.Fatalf("no app %s", app)
		}
		p := a.Provider(provider)
		if p == nil {
			t.Fatalf("app %s has no provider %s", app, provider)
		}
		for _, s := range OSSListed(a) {
			if s.Provider == p {
				return s.ID, true
			}
		}
		return "", false
	}
	for _, c := range []struct{ app, provider string }{
		{"apify", "api-key"},
		{"assemblyai", "api-key"},
		{"atlassian", "service-account-key"},
		{"bannerbear", "api-key"},
		{"baseten", "api-key"},
		{"better-stack", "api-key"},
		{"bland-ai", "api-key"},
		{"brevo", "api-key"},
		{"brex", "api-key"},
		{"bright-data", "api-key"},
		{"browserless", "api-key"},
		{"buffer", "api-key"},
		{"buildkite", "api-key"},
		{"checkly", "api-key"},
		{"circleci", "api-key"},
		{"cloudflare", "api-token"},
		{"cockroachdb-cloud", "api-key"},
		{"context7", "api-key"},
		{"crisp", "api-key"},
		{"cronitor", "api-key"},
		{"diffbot", "api-key"},
		{"digitalocean", "api-key"},
		{"easypost", "api-key"},
		{"firecrawl", "api-key"},
		{"fireflies", "api-key"},
		{"fivetran", "api-key"},
		{"github", "remote-pat"},
		{"hcp-terraform", "api-key"},
		{"honeycomb", "api-key"},
		{"hugging-face", "api-key"},
		{"incident-io", "api-key"},
		{"jam-dev", "api-key"},
		{"jina-ai", "api-key"},
		{"kagi", "api-key"},
		{"linear", "api-key"},
		{"linkup", "api-key"},
		{"mailchimp", "api-key"},
		{"medusa", "api-key"},
		{"meta-ads", "access-token"},
		{"mistral-ai", "api-key"},
		{"monday-com", "api-token"},
		{"motherduck", "api-key"},
		{"neon", "api-key"},
		{"northflank", "api-key"},
		{"notion", "mcp-token"},
		{"orb", "api-key"},
		{"oxylabs", "api-key"},
		{"paddle", "api-key"},
		{"parallel-search", "api-key"},
		{"parallel-task", "api-key"},
		{"perplexity", "api-key"},
		{"planetscale", "api-key"},
		{"posthog", "api-key"},
		{"pulumi", "api-key"},
		{"render", "api-key"},
		{"resend", "api-key"},
		{"retell-ai", "api-key"},
		{"rootly", "api-key"},
		{"sanity", "api-key"},
		{"scraperapi", "api-key"},
		{"semgrep", "api-key"},
		{"serpapi", "api-key"},
		{"spider-cloud", "api-key"},
		{"storyblok", "api-key"},
		{"teamwork-com", "api-key"},
		{"telnyx", "api-key"},
		{"ticktick", "api-key"},
		{"tinybird", "api-key"},
		{"typefully", "api-key"},
		{"upstash", "api-key"},
		{"uptimerobot", "api-key"},
		{"vapi", "api-key"},
		{"weights-and-biases", "api-key"},
		{"wrike", "api-key"},
		{"x-twitter", "app-bearer"},
		{"xata", "api-key"},
		{"you-com", "api-key"},
		{"zapier", "api-key"},
	} {
		if _, ok := listed(c.app, c.provider); !ok {
			t.Errorf("OSS does not list %s/%s", c.app, c.provider)
		}
	}
	if id, ok := listed("airtable", "mcp"); !ok || id != "airtable" {
		t.Errorf("airtable/mcp is listed as %q (listed %v); want it to keep airtable, which its token provider must not take", id, ok)
	}
	// OSS's built-in http ids move to the token sign-in OSS users actually have;
	// their OAuth siblings, which OSS cannot complete, leave the OSS plane.
	for _, c := range []struct{ app, provider, id, oauth string }{
		{"github", "remote-pat", "github-remote", "remote"},
		{"linear", "api-key", "linear", "mcp"},
		{"notion", "mcp-token", "notion", "mcp"},
	} {
		if id, ok := listed(c.app, c.provider); !ok || id != c.id {
			t.Errorf("%s/%s is listed as %q (listed %v); want %s", c.app, c.provider, id, ok, c.id)
		}
		if id, ok := listed(c.app, c.oauth); ok {
			t.Errorf("OSS lists %s/%s as %s; its OAuth sign-in is not one OSS can complete", c.app, c.oauth, id)
		}
	}
	if id, ok := listed("context7", "api-key"); !ok || id != "context7-remote" {
		t.Errorf("context7/api-key is listed as %q (listed %v); want context7-remote, since OSS's own stdio server is context7", id, ok)
	}
	for _, c := range []struct{ app, provider string }{
		{"arize", "api-key"},
		{"braintrust", "api-key"},
		{"coralogix", "api-key"},
		{"dbt-cloud", "api-key"},
		{"langfuse", "api-key"},
		{"make", "api-key"},
		{"recall-ai", "api-key"},
		{"saleor", "api-key"},
		{"smartsheet", "api-key"},
		{"sonarcloud", "api-key"},
		{"supabase", "pat"},
		{"weaviate", "api-key"},
	} {
		if id, ok := listed(c.app, c.provider); ok {
			t.Errorf("OSS lists %s/%s as %s; its record names a reason to keep it out", c.app, c.provider, id)
		}
	}
}
