package eligibility

import "github.com/futureagi/agentcc-gateway/internal/servertools"

// CodeServerToolUnsupported refuses a candidate that cannot run a
// provider-run tool the request carries (rule server_tools).
const CodeServerToolUnsupported = "server_tool_unsupported"

// checkServerTools is rule 11: a request carrying an Anthropic
// server tool (web_search_*, web_fetch_*, code_execution_*) is never sent
// where that version cannot run: Bedrock runs none, Google Cloud only the
// first web search, Azure-hosted Foundry the first web search and web
// fetch, and a provider of another type serving its own vendor's model
// (GPT, Gemini), like a Gemini, Vertex AI or Cohere provider whatever its
// model, none at all (servertools.Support, RouteSource). The route is
// judged by the serving provider's type and, for an org's own credentialed
// provider, its base URL. An aggregator chooses its own upstream for the caller's provider
// object (a managed request with provider-run tools is refused by the
// routing-options check); a provider of unknown type, and an
// OpenAI-compatible one serving Claude or a model of no known vendor (it may
// be a proxy that runs the tool), are not refused. It allocates nothing
// unless it refuses.
func checkServerTools(c *checker) (string, string) {
	req := c.in.Request
	if req == nil || len(req.Tools) == 0 || c.in.Aggregator {
		return "", ""
	}
	source, classified := "", false
	for i := range req.Tools {
		typ := req.Tools[i].Type
		tool, version, ok := servertools.ParseTool(typ)
		if !ok {
			continue
		}
		if !classified {
			source, classified = servertools.RouteSource(c.in.Host, c.orgBaseURL(), c.in.ProviderID, c.in.Model), true
		}
		if !servertools.Runs(source, tool, version) {
			return CodeServerToolUnsupported, "model " + c.in.Model + " on " + c.in.ProviderID + " (" + source +
				") cannot run the server tool " + typ
		}
	}
	return "", ""
}

// orgBaseURL is the base URL of the org's own config for the candidate's
// provider, "" when it has none. Only an enabled config that holds
// credentials counts, as for the request itself (the server's
// orgProviderConfig): the request is otherwise served by the configured
// provider, whose base URL Input does not carry.
func (c *checker) orgBaseURL() string {
	if c.in.Org == nil || c.in.Org.Providers == nil {
		return ""
	}
	if pc := c.in.Org.Providers[c.in.ProviderID]; pc != nil && pc.Enabled && pc.HasCredentials() {
		return pc.BaseURL
	}
	return ""
}
