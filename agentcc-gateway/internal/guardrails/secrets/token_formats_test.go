package secrets

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCurrentTokenFormatsMatchCompleteValue(t *testing.T) {
	for _, tc := range []struct {
		name, kind, token string
	}{
		{"openai_project", "openai_key", "sk-proj-" + strings.Repeat("Synthetic_9-", 8) + "_end-"},
		{"openai_service_account", "openai_key", "sk-svcacct-" + strings.Repeat("Synthetic_9-", 8) + "_end_"},
		{"github_fine_grained", "github_token", "github_pat_" + strings.Repeat("Synthetic_9", 8)},
		{"github_user", "github_token", "ghu_" + strings.Repeat("Synthetic9", 4)},
		{"legacy_openai", "openai_key", "sk-" + strings.Repeat("Synthetic9", 4)},
		{"legacy_github", "github_token", "ghp_" + strings.Repeat("Synthetic9", 4)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := New(map[string]interface{}{"types": []interface{}{tc.kind}})
			for _, text := range []string{tc.token, "(\"" + tc.token + "\"),", "before\n" + tc.token + "; after"} {
				matches := g.detectors[0].pattern.FindAllString(text, -1)
				if len(matches) != 1 || matches[0] != tc.token {
					t.Errorf("detector must match one complete synthetic token; got %q", matches)
				}
				for _, multipart := range []bool{false, true} {
					input := makeInput(text)
					if multipart {
						input.Request.Messages[0].Content, _ = json.Marshal([]map[string]string{{"type": "text", "text": "intro"}, {"type": "text", "text": text}})
					}
					for _, output := range []bool{false, true} {
						check := input
						if output {
							check = &guardrails.CheckInput{Response: &models.ChatCompletionResponse{Choices: []models.Choice{{Message: input.Request.Messages[0]}}}}
						}
						r := g.Check(context.Background(), check)
						if r.Pass {
							t.Errorf("token accepted (multipart=%v output=%v)", multipart, output)
							continue
						}
						assertSecretType(t, r, tc.kind)
						if r.Details["secrets_found"] != 1 {
							t.Errorf("expected exactly one detection, got %v", r.Details)
						}
					}
				}
			}
		})
	}
}

func TestCurrentTokenFormatsIgnoreOrdinaryText(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"openai_key", "github_token"}})
	for _, text := range []string{
		"Please describe GitHub fine-grained tokens and OpenAI projects.",
		"sk-proj-short", "sk-svcacct-short", "github_pat_short", "ghu_short",
		"sk-proj-", "sk-svcacct-", "github_pat_", "ghu_",
		"notgithub_pat_" + strings.Repeat("A", 50),
		"notghu_" + strings.Repeat("A", 50),
		"notsk-proj-" + strings.Repeat("A", 50),
	} {
		if r := g.Check(context.Background(), makeInput(text)); !r.Pass {
			t.Errorf("ordinary or malformed text detected: %q", text)
		}
	}
}
