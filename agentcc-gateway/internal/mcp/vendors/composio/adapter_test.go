package composio

import (
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors/grantclient"
	"testing"
)

func TestComposioMintedURLOnlySameOrigin(t *testing.T) {
	for _, endpoint := range []string{"https://backend.composio.dev/tool_router/fixture-one/mcp", "https://backend.composio.dev/tool_router/fixture-two/mcp"} {
		binding, err := Prepare(grantclient.Grant{Vendor: "composio", APIKey: "fixture-project-not-real", Endpoint: endpoint, CredentialVersion: 1}, "https://backend.composio.dev/")
		if err != nil || binding.Endpoint != endpoint || len(binding.Header) != 1 || binding.Header.Get("x-api-key") == "" {
			t.Fatal("minted endpoint not retained")
		}
	}
	for _, endpoint := range []string{"https://app.composio.dev/mcp", "http://backend.composio.dev/mcp", "https://backend.composio.dev:444/mcp", "https://backend.composio.dev/mcp?user=someone", "https://user@backend.composio.dev/mcp"} {
		if _, err := Prepare(grantclient.Grant{Vendor: "composio", APIKey: "fixture-project-not-real", Endpoint: endpoint, CredentialVersion: 1}, "https://backend.composio.dev/"); err == nil {
			t.Fatal("off-origin or query endpoint accepted")
		}
	}
}
