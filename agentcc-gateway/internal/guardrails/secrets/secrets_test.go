package secrets

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeInput(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4o",
			Messages: []models.Message{
				{Role: "user", Content: raw},
			},
		},
		Metadata: map[string]string{},
	}
}

func TestDetect_AWSAccessKey(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"aws_access_key"}})
	r := g.Check(context.Background(), makeInput("My key is AKIAIOSFODNN7EXAMPLE"))
	if r.Pass {
		t.Fatal("expected AWS access key detection")
	}
	assertSecretType(t, r, "aws_access_key")
}

func TestDetect_AWSSecretKey(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"aws_secret_key"}})
	r := g.Check(context.Background(), makeInput("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY1"))
	if r.Pass {
		t.Fatal("expected AWS secret key detection")
	}
}

func TestDetect_GitHubToken(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"github_token"}})
	r := g.Check(context.Background(), makeInput("Token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijkl"))
	if r.Pass {
		t.Fatal("expected GitHub token detection")
	}
}

func TestDetect_GitLabToken(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"gitlab_token"}})
	r := g.Check(context.Background(), makeInput("Use token glpat-ABCDEFGHIJKLMNOPQRST"))
	if r.Pass {
		t.Fatal("expected GitLab token detection")
	}
}

func TestDetect_SlackToken(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"slack_token"}})
	r := g.Check(context.Background(), makeInput("Slack: xoxb-123456789-abcdefgh"))
	if r.Pass {
		t.Fatal("expected Slack token detection")
	}
}

func TestDetect_StripeKey(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"stripe_key"}})
	r := g.Check(context.Background(), makeInput("sk_live_ABCDEFGHIJKLMNOPQRSTuvwx"))
	if r.Pass {
		t.Fatal("expected Stripe key detection")
	}
}

func TestDetect_OpenAIKey(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"openai_key"}})
	r := g.Check(context.Background(), makeInput("Use sk-abcdefghijklmnopqrstuvwxyz for auth"))
	if r.Pass {
		t.Fatal("expected OpenAI key detection")
	}
}

func TestDetect_PrivateKey(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"private_key"}})
	r := g.Check(context.Background(), makeInput("-----BEGIN RSA PRIVATE KEY-----\nMIIE..."))
	if r.Pass {
		t.Fatal("expected private key detection")
	}
}

func TestDetect_ConnectionString(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"connection_string"}})
	r := g.Check(context.Background(), makeInput("Connect to postgres://admin:secretpass@db.example.com:5432/mydb"))
	if r.Pass {
		t.Fatal("expected connection string detection")
	}
}

func TestDetect_JWT(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"jwt"}})
	r := g.Check(context.Background(), makeInput("Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"))
	if r.Pass {
		t.Fatal("expected JWT detection")
	}
}

func TestDetect_Password(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"password_assign"}})
	r := g.Check(context.Background(), makeInput("Set password=MyS3cretP@ss!"))
	if r.Pass {
		t.Fatal("expected password detection")
	}
}

func TestCleanText(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeInput("Please help me write a function to sort an array"))
	if !r.Pass {
		t.Fatal("clean text should pass")
	}
}

func TestTypeFiltering(t *testing.T) {
	g := New(map[string]interface{}{
		"types": []interface{}{"aws_access_key"},
	})
	// OpenAI key should not trigger when only aws_access_key is enabled.
	r := g.Check(context.Background(), makeInput("sk-abcdefghijklmnopqrstuvwxyz"))
	if !r.Pass {
		t.Fatal("openai key should not trigger when only aws_access_key enabled")
	}
}

func TestScore(t *testing.T) {
	g := New(map[string]interface{}{
		"types":       []interface{}{"openai_key"},
		"max_secrets": 1,
	})
	r := g.Check(context.Background(), makeInput("Use sk-abcdefghijklmnopqrstuvwxyz"))
	if r.Score != 1.0 {
		t.Errorf("expected score 1.0, got %f", r.Score)
	}
}

func TestRedaction(t *testing.T) {
	g := New(map[string]interface{}{"types": []interface{}{"openai_key"}})
	r := g.Check(context.Background(), makeInput("Use sk-abcdefghijklmnopqrstuvwxyz"))
	if r.Pass {
		t.Fatal("expected detection")
	}
	detections := r.Details["detections"].([]interface{})
	det := detections[0].(map[string]interface{})
	redacted := det["redacted"].(string)
	if strings.Contains(redacted, "abcdefghijklmnopqrstuvwxyz") {
		t.Error("full secret should not be in redacted value")
	}
	if !strings.Contains(redacted, "...") {
		t.Error("redacted value should contain ...")
	}
}

func TestNilInput(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), nil)
	if !r.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestName(t *testing.T) {
	g := New(nil)
	if g.Name() != "secret-detection" {
		t.Errorf("expected secret-detection, got %s", g.Name())
	}
}

func TestStage(t *testing.T) {
	g := New(nil)
	if g.Stage() != guardrails.StagePre {
		t.Error("expected StagePre")
	}
}

func TestMultipleSecrets(t *testing.T) {
	g := New(map[string]interface{}{
		"types": []interface{}{"aws_access_key", "openai_key"},
	})
	r := g.Check(context.Background(), makeInput("Keys: AKIAIOSFODNN7EXAMPLE and sk-abcdefghijklmnopqrstuvwxyz"))
	if r.Pass {
		t.Fatal("expected multiple secrets detected")
	}
	count := r.Details["secrets_found"].(int)
	if count < 2 {
		t.Errorf("expected 2+ secrets, got %d", count)
	}
}

func assertSecretType(t *testing.T, r *guardrails.CheckResult, secretType string) {
	t.Helper()
	detections := r.Details["detections"].([]interface{})
	for _, d := range detections {
		det := d.(map[string]interface{})
		if det["type"] == secretType {
			return
		}
	}
	t.Errorf("secret type %q not found", secretType)
}
