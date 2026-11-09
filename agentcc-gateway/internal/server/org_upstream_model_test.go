package server

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/anthropic"
	"github.com/futureagi/agentcc-gateway/internal/providers/bedrock"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

// sentBy is the id adapter p sends its host for model.
func sentBy(p providers.Provider, model string) string {
	if n, ok := p.(providers.UpstreamModelNamer); ok {
		return n.UpstreamModelName(model)
	}
	return model
}

// orgModelFor hands each real adapter the form under which its host receives
// the id the org registered, exactly: the bare id to an adapter that sends
// it as it is or leaves it alone, and the requested "<provider>/<id>" to one
// that would otherwise cut an id with a slash of its own a second time. An
// exact match is sent as it is.
func TestOrgModelForCutsThePrefixExactlyOnce(t *testing.T) {
	oai, err := openai.New("openai_org_o", config.ProviderConfig{BaseURL: "https://api.openai.com", APIKey: "k", APIFormat: "openai"})
	if err != nil {
		t.Fatal(err)
	}
	ant, err := anthropic.New("anthropic_org_o", config.ProviderConfig{BaseURL: "https://api.anthropic.com", APIKey: "k", APIFormat: "anthropic"})
	if err != nil {
		t.Fatal(err)
	}
	brk, err := bedrock.New("bedrock_org_o", config.ProviderConfig{BaseURL: "https://bedrock-runtime.us-east-1.amazonaws.com",
		AWSRegion: "us-east-1", AWSAccessKeyID: "AKIAEXAMPLE", AWSSecretAccessKey: "secret", APIFormat: "bedrock"})
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name            string
		p               providers.Provider
		requested, host string
		want            string
	}{
		{"openai, prefixed", oai, "openai/gpt-4o-mini", "gpt-4o-mini", "gpt-4o-mini"},
		{"openai, prefix in another case", oai, "OpenAI/gpt-4o-mini", "gpt-4o-mini", "gpt-4o-mini"},
		{"openai-format, id with a slash", oai, "together/meta-llama/Llama-3.3-70B-Instruct-Turbo", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
		{"openai-format, exact slash id", oai, "openai/gpt-oss-120b", "openai/gpt-oss-120b", "openai/gpt-oss-120b"},
		{"anthropic, bare id", ant, "anthropic/claude-haiku-4-5", "claude-haiku-4-5", "claude-haiku-4-5"},
		{"anthropic, id with a slash", ant, "myrouter/anthropic/claude-sonnet-4", "anthropic/claude-sonnet-4", "myrouter/anthropic/claude-sonnet-4"},
		{"anthropic, exact bare id", ant, "claude-haiku-4-5", "claude-haiku-4-5", "claude-haiku-4-5"},
		{"bedrock, ARN", brk, "bedrock/" + okARN, okARN, "bedrock/" + okARN},
		{"bedrock, profile id", brk, "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0", "us.anthropic.claude-haiku-4-5-20251001-v1:0", "us.anthropic.claude-haiku-4-5-20251001-v1:0"},
		{"no registered id", oai, "gpt-4o-mini", "", "gpt-4o-mini"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := orgModelFor(c.p, c.requested, c.host)
			if got != c.want {
				t.Errorf("orgModelFor(%q, %q) = %q, want %q", c.requested, c.host, got, c.want)
			}
			if c.host != "" && sentBy(c.p, got) != c.host {
				t.Errorf("the adapter sends its host %q for %q; want the registered id %q", sentBy(c.p, got), got, c.host)
			}
		})
	}
}

// The chain of a request the org's own provider took under its registered id
// is looked up under the model it was routed with; the model a native body
// names is the registered id; neither changes for any other request.
func TestOrgUpstreamModelMetadataHelpers(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	if got := fallbackChainModel(rc, "x-ai/grok-5"); got != "x-ai/grok-5" {
		t.Errorf("no org match: fallbackChainModel = %q, want the routed x-ai/grok-5", got)
	}
	if got := passThroughModel(rc, "x-ai/grok-5"); got != "x-ai/grok-5" {
		t.Errorf("no org match: passThroughModel = %q, want the routed x-ai/grok-5", got)
	}
	noteOrgUpstreamModel(rc, "gpt-4o-mini", "gpt-4o-mini")
	if _, ok := rc.Metadata[metaOrgUpstreamModel]; ok {
		t.Error("an exact match recorded org_upstream_model; it records only an id other than the routed model")
	}
	rc.Metadata["org_provider_model_match"] = "openai/gpt-4o-mini"
	noteOrgUpstreamModel(rc, "openai/gpt-4o-mini", "gpt-4o-mini")
	if got := fallbackChainModel(rc, "gpt-4o-mini"); got != "openai/gpt-4o-mini" {
		t.Errorf("fallbackChainModel = %q, want the routed openai/gpt-4o-mini", got)
	}
	if got := passThroughModel(rc, "openai/gpt-4o-mini"); got != "gpt-4o-mini" {
		t.Errorf("passThroughModel = %q, want the registered gpt-4o-mini", got)
	}
}
