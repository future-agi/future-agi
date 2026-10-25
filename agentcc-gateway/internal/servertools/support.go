package servertools

import "strings"

// Endpoint sources: where an Anthropic model is served, as far as its server
// tools are concerned. The Claude API and Claude Platform on AWS are
// operated by Anthropic and run every version; Amazon Bedrock runs none;
// Google Cloud runs only the first web search version; Microsoft Foundry
// deployments hosted on Azure run the first web search version and web
// fetch, and refuse code execution with a 400 (per each provider's
// documentation as of 2026-10-15).
const (
	SourceClaudeAPI         = "claude_api"
	SourceClaudePlatformAWS = "claude_platform_aws"
	SourceBedrock           = "bedrock"
	SourceGoogleCloud       = "google_cloud"
	SourceAzureFoundry      = "azure_foundry"
	// SourceNone is a provider that runs no Anthropic server tool: an
	// OpenAI, Gemini, Azure OpenAI or Cohere endpoint serving its own
	// vendor's model, which would drop the tool or answer it with an error.
	SourceNone = "none"
	// SourceUnknown is a provider whose type is not known: nothing is
	// refused on it.
	SourceUnknown = ""
)

// firstWebSearch is the one web search version Google Cloud and
// Azure-hosted Foundry run.
const firstWebSearch = "20250305"

// SourceOf classifies a provider by its type (the gateway's api_format as
// modeldb.DeprecationHost reports it: anthropic, bedrock, gemini, vertex,
// openai, azure, cohere) and, for an Anthropic-format provider, its base
// URL, which tells Anthropic's own API apart from a cloud that speaks the
// same Messages API. An Anthropic-format provider whose base URL is unknown
// is taken to be Anthropic-operated. It allocates nothing.
func SourceOf(host, baseURL string) string {
	switch strings.ToLower(host) {
	case "":
		return SourceUnknown
	case "bedrock":
		return SourceBedrock
	case "anthropic":
	default:
		return SourceNone
	}
	u := hostOf(baseURL)
	switch {
	case strings.Contains(u, "bedrock"):
		// Bedrock's own Messages-compatible endpoint is still Bedrock.
		return SourceBedrock
	case strings.HasSuffix(u, "aiplatform.googleapis.com"):
		return SourceGoogleCloud
	case strings.HasSuffix(u, ".azure.com"):
		return SourceAzureFoundry
	case strings.HasSuffix(u, ".amazonaws.com") || strings.HasPrefix(u, "aws.") || strings.Contains(u, ".aws."):
		// Anthropic-format, Anthropic-operated, on AWS: Claude Platform on
		// AWS, a valid AWS route for every server tool (only Bedrock is not).
		return SourceClaudePlatformAWS
	default:
		return SourceClaudeAPI
	}
}

// RouteSource is SourceOf for model served by provider. A Gemini, Vertex AI
// or Cohere provider speaks its own API, which carries no Anthropic tool
// whatever model it is given, so it is SourceNone. A provider of another
// type (an OpenAI-compatible endpoint) is SourceNone only when it serves a
// model of a known vendor other than Anthropic (gpt-4o, gemini-3-pro), and
// SourceUnknown otherwise, since an OpenAI-compatible proxy in front of
// Claude may run the tool itself. It allocates nothing.
func RouteSource(host, baseURL, provider, model string) string {
	source := SourceOf(host, baseURL)
	if source == SourceNone && !ownAPIOnly(host) {
		if v := Vendor(provider, model); v == "" || v == VendorAnthropic {
			return SourceUnknown
		}
	}
	return source
}

// ownAPIOnly reports whether a provider of type host speaks only its own
// vendor's API, so that it can never be a proxy for an Anthropic model.
func ownAPIOnly(host string) bool {
	switch strings.ToLower(host) {
	case "gemini", "google", "vertex", "cohere":
		return true
	}
	return false
}

// hostOf is the lower-cased host of a base URL, without scheme, port or
// path, sliced from it with no allocation when it is already lower case.
func hostOf(baseURL string) string {
	s := baseURL
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	}
	if i := strings.IndexAny(s, "/?#"); i >= 0 {
		s = s[:i]
	}
	if i := strings.LastIndexByte(s, '@'); i >= 0 {
		s = s[i+1:]
	}
	if i := strings.LastIndexByte(s, ':'); i >= 0 && !strings.Contains(s[i:], "]") {
		s = s[:i]
	}
	for i := 0; i < len(s); i++ {
		if c := s[i]; c >= 'A' && c <= 'Z' {
			return strings.ToLower(s)
		}
	}
	return s
}

// Versions is the set of versions of one tool a source runs.
type Versions struct {
	// All is every version, those not shipped yet included: Anthropic's own
	// API runs a new version the day it ships.
	All bool
	// Only lists the versions, when All is false. Empty runs none.
	Only []string
}

// Has reports whether v runs version.
func (v Versions) Has(version string) bool {
	if v.All {
		return true
	}
	for _, o := range v.Only {
		if o == version {
			return true
		}
	}
	return false
}

// Support is the route support table: the versions of each server tool
// provider's models run on endpointSource (a Source constant). provider is
// the vendor whose tools these are; only Anthropic's are routed by type
// (OpenAI's hosted tools are Responses-only, and Gemini grounding is a
// native request field). A source that runs none of a tool has no entry for
// it. The map is the caller's own.
func Support(provider, endpointSource string) map[Tool]Versions {
	out := map[Tool]Versions{}
	if provider != "anthropic" {
		return out
	}
	for _, t := range anthropicTools {
		if v, ok := versionsOn(endpointSource, t); ok {
			out[t] = v
		}
	}
	return out
}

// Runs reports whether endpointSource runs version of tool. An unknown
// source runs everything: the gateway refuses only where it knows the tool
// cannot run. It allocates nothing.
func Runs(endpointSource string, tool Tool, version string) bool {
	if endpointSource == SourceUnknown {
		return true
	}
	v, ok := versionsOn(endpointSource, tool)
	return ok && v.Has(version)
}

var onlyFirstWebSearch = []string{firstWebSearch}

// versionsOn is one cell of the support table.
func versionsOn(source string, tool Tool) (Versions, bool) {
	switch source {
	case SourceClaudeAPI, SourceClaudePlatformAWS, SourceUnknown:
		return Versions{All: true}, true
	case SourceGoogleCloud:
		if tool == WebSearch {
			return Versions{Only: onlyFirstWebSearch}, true
		}
	case SourceAzureFoundry:
		switch tool {
		case WebSearch:
			return Versions{Only: onlyFirstWebSearch}, true
		case WebFetch:
			return Versions{All: true}, true
		}
	}
	return Versions{}, false
}
