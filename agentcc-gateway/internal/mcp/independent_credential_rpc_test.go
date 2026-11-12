package mcp

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"testing"
)

func TestIndependentPersonalReadRetainsCredentialRefusalObject(t *testing.T) {
	for _, method := range []string{"resources/read", "prompts/get"} {
		t.Run(method, func(t *testing.T) {
			cc := &CallContext{ctx: context.Background(), msg: &Message{ID: json.RawMessage(`1`)}}
			// This is the readResource/getPrompt path after the tenancy guard refuses
			// a grant whose lifetime ended after client acquisition.
			cc.Decision = protocolFailure(upstreamFailure(cc, method+" failed", CredentialRequired("issues", "expired")))
			response := httptest.NewRecorder()
			writeDenialRPC(response, cc)
			var body struct {
				Error struct {
					Data struct {
						Code     string `json:"code"`
						Metadata struct {
							Server string `json:"server_id"`
							Reason string `json:"reason"`
						} `json:"metadata"`
					} `json:"data"`
				} `json:"error"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if body.Error.Data.Code != "auth_required" || body.Error.Data.Metadata.Server != "issues" || body.Error.Data.Metadata.Reason != "expired" {
				t.Fatal("personal read refusal lost the existing auth_required object and its lifecycle metadata")
			}
		})
	}
}
