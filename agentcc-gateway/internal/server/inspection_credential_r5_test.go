package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// These expectations are transcribed from dev's actual handler extractors:
// pipeline=setAuthMetadataFromRequest + extractBearerToken; stored=asyncCaller;
// GenAI=query/header/Authorization; speech=speechClientKey; realtime=raw bearer
// then query; MCP POST=case-insensitive Bearer only. No broadened normalization.
func TestRuntimeR5CredentialPrecedence(t *testing.T) {
	keys := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
		{Name: "a", Key: "sk-agentcc-r5-a"}, {Name: "b", Key: "sk-agentcc-r5-b"},
	}})
	for _, tc := range []struct {
		mode, authorization, api, google, query, want string
	}{
		{inspectionAuthStandard, "bearer sk-agentcc-r5-a", "", "", "", ""},
		{inspectionAuthStandard, "Token sk-agentcc-r5-a", "sk-agentcc-r5-b", "", "", ""},
		{inspectionAuthStandard, "Bearer invalid", "sk-agentcc-r5-b", "", "", ""},
		{inspectionAuthStandard, "Bearer sk-agentcc-r5-a", "sk-agentcc-r5-b", "", "", "key_1"},
		{inspectionAuthStandard, "", "sk-agentcc-r5-b", "", "", "key_2"},
		{inspectionAuthStored, "bearer sk-agentcc-r5-a", "sk-agentcc-r5-b", "", "", "key_2"},
		{inspectionAuthStored, "Bearer invalid", "sk-agentcc-r5-b", "", "", ""},
		{inspectionAuthStored, "Bearer ", "sk-agentcc-r5-b", "", "", ""},
		{inspectionAuthGenAI, "Bearer sk-agentcc-r5-a", "", "sk-agentcc-r5-b", "?key=invalid", ""},
		{inspectionAuthGenAI, "Bearer sk-agentcc-r5-a", "", "sk-agentcc-r5-b", "", "key_2"},
		{inspectionAuthGenAI, "Token sk-agentcc-r5-a", "sk-agentcc-r5-b", "", "", ""},
		{inspectionAuthGenAI, "Bearer sk-agentcc-r5-a", "", "invalid", "?key=sk-agentcc-r5-b", "key_2"},
		{inspectionAuthSpeech, "Token sk-agentcc-r5-a", "sk-agentcc-r5-b", "", "?api_key=invalid", "key_1"},
		{inspectionAuthSpeech, "token sk-agentcc-r5-a", "sk-agentcc-r5-b", "", "", "key_2"},
		{inspectionAuthSpeech, "Token ", "sk-agentcc-r5-b", "", "", ""},
		{inspectionAuthRealtime, "Bearer sk-agentcc-r5-a", "", "", "?api_key=sk-agentcc-r5-b", "key_1"},
		{inspectionAuthRealtime, "Bearer ", "", "", "?api_key=sk-agentcc-r5-b", "key_2"},
		{inspectionAuthRealtime, "bearer sk-agentcc-r5-a", "", "", "?api_key=sk-agentcc-r5-b", "key_2"},
		{inspectionAuthRealtime, "Bearer invalid", "", "", "?api_key=sk-agentcc-r5-b", ""},
		{inspectionAuthMCP, "bEaReR sk-agentcc-r5-a  ", "sk-agentcc-r5-b", "", "", "key_1"},
		{inspectionAuthMCP, "Token sk-agentcc-r5-a", "sk-agentcc-r5-b", "", "", ""},
		{inspectionAuthMCP, "Bearer invalid", "sk-agentcc-r5-b", "", "", ""},
		{inspectionAuthMiddleware, "Token sk-agentcc-r5-a", "sk-agentcc-r5-b", "", "", "key_1"},
		{inspectionAuthMiddleware, "Bearer invalid", "sk-agentcc-r5-b", "", "", "key_2"},
		{inspectionAuthMiddleware, "Basic invalid", "invalid", "", "?key=sk-agentcc-r5-a&api_key=sk-agentcc-r5-b", "key_1"},
		{inspectionAuthCatalog, "Bearer invalid", "sk-agentcc-r5-b", "", "", "key_2"},
	} {
		t.Run(fmt.Sprintf("%s/%s/%s", tc.mode, tc.authorization, tc.query), func(t *testing.T) {
			r := httptest.NewRequest(http.MethodGet, "/synthetic"+tc.query, nil)
			r.Header.Set("Authorization", tc.authorization)
			r.Header.Set("x-api-key", tc.api)
			r.Header.Set("x-goog-api-key", tc.google)
			key, authorization := inspectionCredential(r, tc.mode, keys)
			got := ""
			if key != nil {
				got = key.ID
			}
			if got != tc.want || (key != nil && !strings.HasPrefix(authorization, "Bearer ")) {
				t.Fatalf("principal=%q want=%q", got, tc.want)
			}
		})
	}
}
