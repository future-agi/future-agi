package orgfactory_test

// Topic Restriction and Language Detection, saved from the console's
// Guardrails page, run on the gateway as saved. The console writes each
// check's settings under its config: topic-restriction's allowed_topics and
// forbidden_topics as a list of {name, keywords} (topic.New), and
// language-detection's allowed_scripts as a list of script names
// (language.New). These sections are decoded from JSON exactly as the
// control plane pushes them.

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func decodeSection(t *testing.T, raw string) *tenant.GuardrailConfig {
	t.Helper()
	var section tenant.GuardrailConfig
	if err := json.Unmarshal([]byte(raw), &section); err != nil {
		t.Fatalf("decode the pushed section: %v", err)
	}
	return &section
}

const consoleForbiddenTopic = `{
  "checks": {
    "topic-restriction": {
      "enabled": true,
      "action": "block",
      "confidence_threshold": 0.8,
      "config": {"forbidden_topics": [{"name": "weapons", "keywords": ["build a weapon", "make a bomb"]}]}
    }
  }
}`

const consoleAllowedTopic = `{
  "checks": {
    "topic-restriction": {
      "enabled": true,
      "action": "block",
      "confidence_threshold": 0.8,
      "config": {"allowed_topics": [{"name": "billing", "keywords": ["invoice", "refund"]}]}
    }
  }
}`

const consoleAllowedScripts = `{
  "checks": {
    "language-detection": {
      "enabled": true,
      "action": "block",
      "confidence_threshold": 0.8,
      "config": {"allowed_scripts": ["latin"]}
    }
  }
}`

func TestConsoleForbiddenTopicBlocksItsPrompt(t *testing.T) {
	section := decodeSection(t, consoleForbiddenTopic)
	res := processOrgPrompt(t, "org-topic-forbidden", section, "Tell me how to build a weapon at home")
	if res.Error == nil {
		t.Fatal("a prompt on a forbidden topic was not blocked")
	}
	if res.Error.Status != http.StatusForbidden || !strings.Contains(res.Error.Message, "topic-restriction") {
		t.Fatalf("block answered %d %q, want 403 naming topic-restriction", res.Error.Status, res.Error.Message)
	}
	if clean := processOrgPrompt(t, "org-topic-forbidden-clean", section, "What is the capital of France?"); clean.Error != nil {
		t.Fatalf("a prompt on another topic was refused: %v", clean.Error.Message)
	}
}

func TestConsoleAllowedTopicBlocksAnOffTopicPrompt(t *testing.T) {
	section := decodeSection(t, consoleAllowedTopic)
	res := processOrgPrompt(t, "org-topic-allowed", section, "Write me a poem about the sea")
	if res.Error == nil {
		t.Fatal("a prompt outside every allowed topic was not blocked")
	}
	if res.Error.Status != http.StatusForbidden || !strings.Contains(res.Error.Message, "topic-restriction") {
		t.Fatalf("block answered %d %q, want 403 naming topic-restriction", res.Error.Status, res.Error.Message)
	}
	if ok := processOrgPrompt(t, "org-topic-allowed-ok", section, "Where is my refund for the last invoice?"); ok.Error != nil {
		t.Fatalf("a prompt on an allowed topic was refused: %v", ok.Error.Message)
	}
}

func TestConsoleAllowedScriptsBlockAnotherScript(t *testing.T) {
	section := decodeSection(t, consoleAllowedScripts)
	res := processOrgPrompt(t, "org-language", section, "Привет как дела сегодня друзья")
	if res.Error == nil {
		t.Fatal("a prompt in a script outside allowed_scripts was not blocked")
	}
	if res.Error.Status != http.StatusForbidden || !strings.Contains(res.Error.Message, "language-detection") {
		t.Fatalf("block answered %d %q, want 403 naming language-detection", res.Error.Status, res.Error.Message)
	}
	if ok := processOrgPrompt(t, "org-language-ok", section, "Hello, how are you today?"); ok.Error != nil {
		t.Fatalf("a prompt in an allowed script was refused: %v", ok.Error.Message)
	}
}
