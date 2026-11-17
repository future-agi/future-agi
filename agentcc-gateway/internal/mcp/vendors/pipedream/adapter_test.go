package pipedream

import (
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors/grantclient"
	"testing"
	"time"
)

func TestPipedreamOnlyAllowedHeaders(t *testing.T) {
	grant := grantclient.Grant{Vendor: "pipedream", AccessToken: "fixture-bearer-not-real", ExpiresAt: time.Now().Add(time.Hour).UTC().Format(time.RFC3339), ProjectID: "proj_fixture", Environment: "development", AppSlug: "gmail", ExternalUserID: "fau_one", AccountID: "apn_fixture", CredentialVersion: 1}
	headers, err := Headers(grant)
	if err != nil || len(headers) != 6 || headers.Get("Authorization") != "Bearer fixture-bearer-not-real" || headers.Get("x-pd-external-user-id") != "fau_one" {
		t.Fatal("invalid header binding")
	}
	grant.ExternalUserID = "fau_two"
	second, err := Headers(grant)
	if err != nil || second.Get("x-pd-external-user-id") == headers.Get("x-pd-external-user-id") {
		t.Fatal("end users shared headers")
	}
	grant.AccessToken = "value\r\ninjected"
	if _, err := Headers(grant); err == nil {
		t.Fatal("header injection accepted")
	}
}
