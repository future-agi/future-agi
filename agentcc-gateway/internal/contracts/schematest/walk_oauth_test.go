package schematest

import (
	"strings"
	"testing"
)

// REST-02: a temporary refresh outage is not a request for new consent.
func TestWalkOAuthUpstreamTokenRefusals(t *testing.T) {
	const schema = "mcp-upstream-token-v1.schema.json"
	for _, outcome := range []string{"no_credential", "needs_reauth", "revoked", "expired", "unavailable"} {
		t.Run(outcome, func(t *testing.T) {
			if errs := ValidateDef(t, schema, "Refusal", map[string]any{"error": outcome}); len(errs) != 0 {
				t.Fatalf("valid token refusal rejected: %v", errs)
			}
		})
	}
	for name, body := range map[string]map[string]any{
		"missing error": {},
		"unknown error": {"error": "temporary_typo"},
		"extra secret":  {"error": "unavailable", "access_token": "not-a-real-token"},
	} {
		t.Run(name, func(t *testing.T) {
			if errs := ValidateDef(t, schema, "Refusal", body); len(errs) == 0 {
				t.Fatal("invalid token refusal accepted")
			}
		})
	}
}

// Microsoft's hold (HTTP 409, error microsoft_coming_soon) is a Refusal of
// both credential answers, with an optional sentence for the caller of 1 to
// 300 characters.
func TestMicrosoftHoldRefusalCarriesAnOptionalMessage(t *testing.T) {
	const hold = "microsoft_coming_soon"
	for _, schema := range []string{"mcp-upstream-token-v1.schema.json", "mcp-vendor-credential-v1.schema.json"} {
		for name, body := range map[string]map[string]any{
			"no message":     {"error": hold},
			"a message":      {"error": hold, "message": "Microsoft connections are coming soon."},
			"300 characters": {"error": hold, "message": strings.Repeat("é", 300)},
		} {
			t.Run(schema+"/"+name, func(t *testing.T) {
				if errs := ValidateDef(t, schema, "Refusal", body); len(errs) != 0 {
					t.Fatalf("valid hold refusal rejected: %v", errs)
				}
			})
		}
		for name, body := range map[string]map[string]any{
			"empty message":  {"error": hold, "message": ""},
			"301 characters": {"error": hold, "message": strings.Repeat("é", 301)},
			"not a string":   {"error": hold, "message": 7},
			"extra secret":   {"error": hold, "message": "Held.", "access_token": "not-a-real-token"},
		} {
			t.Run(schema+"/"+name, func(t *testing.T) {
				if errs := ValidateDef(t, schema, "Refusal", body); len(errs) == 0 {
					t.Fatal("invalid hold refusal accepted")
				}
			})
		}
	}
}
