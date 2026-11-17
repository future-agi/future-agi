package auth

import "testing"

// consoleRunAutoNames are the model names the gateway treats as asking it to
// choose (routing.AutoModelNames, read by routing.IsAutoModel). auth cannot
// import routing; the server package's
// TestConsoleRunGrantRefusesEveryAutoModelName checks this list against
// routing.AutoModelNames so the two cannot drift.
var consoleRunAutoNames = []string{"auto", "agentcc/auto", "omega-default", "omega/auto"}

// A console-run grant names the models a Console run may call. "auto" and
// every other name the gateway treats as auto choose a model the grant does
// not name, so a grant naming one, alone or beside a model, is refused:
// validModels is false and VerifyDelegated refuses the pair.
func TestEnvelopeConsoleRunGrantRefusesAutoModels(t *testing.T) {
	refused := []string{
		"auto",
		"agentcc/auto",
		"omega-default",
		"omega/auto",
		"auto,gpt-4o-mini",
		"agentcc/auto,gpt-4o-mini",
		"gpt-4o-mini,omega-default",
		"gpt-4o-mini,omega/auto",
		"anthropic/claude-sonnet-5.5,auto,gpt-4o-mini",
	}
	for _, models := range refused {
		if validModels(models) {
			t.Errorf("validModels(%q) = true, want false (an auto name)", models)
		}
		if _, _, err := verifyConsoleRunGrant("console-run", models); err == nil {
			t.Errorf("an fe1c console-run grant naming %q was accepted", models)
		}
	}
	for _, name := range consoleRunAutoNames {
		if !consoleRunAutoModel(name) {
			t.Errorf("consoleRunAutoModel(%q) = false, want true", name)
		}
	}

	// A name whose last path segment is "auto", in any case, asks a router to
	// choose ("openrouter/auto"): refused. A name that only resembles one is a
	// model like any other.
	for _, models := range []string{"Auto", "AUTO", "openrouter/auto", "OpenRouter/AUTO", "gpt-4o-mini,openrouter/auto", "vendor/team/Auto"} {
		if validModels(models) {
			t.Errorf("validModels(%q) = true, want false (its last segment is auto)", models)
		}
		if _, _, err := verifyConsoleRunGrant("console-run", models); err == nil {
			t.Errorf("an fe1c console-run grant naming %q was accepted", models)
		}
	}
	accepted := []string{
		"auto-mini",
		"autobot",
		"openai/auto-1",
		"gpt-4o-mini",
		"anthropic/claude-sonnet-5.5,gpt-4o-mini",
	}
	for _, models := range accepted {
		if !validModels(models) {
			t.Errorf("validModels(%q) = false, want true", models)
		}
		if _, _, err := verifyConsoleRunGrant("console-run", models); err != nil {
			t.Errorf("an fe1c console-run grant naming %q was refused: %v", models, err)
		}
	}
}
