package otel

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The image prompt is the request's content: under full redaction the span's
// gen_ai.image.prompt carries none of it, exactly as input.value; patterns mode
// pattern-redacts it; with redaction off it is exported unchanged.

const (
	imagePromptOrg      = "image-prompt-org"
	imagePromptText     = "draw jane.doe@example.invalid riding a red bicycle"
	imagePromptPatterns = "draw [REDACTED:email] riding a red bicycle"
	imageRevisedText    = "jane.doe@example.invalid on a red bicycle at dusk"
	imageRevisedPattern = "[REDACTED:email] on a red bicycle at dusk"
)

var imagePromptPatternConfig = []privacy.PatternConfig{
	{Name: "email", Pattern: `[a-z.]+@example\.invalid`},
}

func imagePromptRC(contentMode models.ContentMode) *models.RequestContext {
	rc := newTestRC()
	rc.Request = nil
	rc.EndpointType = "image"
	rc.Privacy.ContentMode = contentMode
	rc.ImageRequest = &models.ImageRequest{Model: rc.Model, Prompt: imagePromptText, Size: "1024x1024"}
	rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{RevisedPrompt: imageRevisedText}}}
	return rc
}

// imagePromptPolicy selects the redactor the way the server does: the org's
// own (with the mode the handler copies into the request metadata) or the
// gateway-wide one.
func imagePromptPolicy(p *Plugin, rc *models.RequestContext, source, mode string) {
	if source == "org" {
		patterns := make([]*tenant.RedactPatternConfig, len(imagePromptPatternConfig))
		for i, pattern := range imagePromptPatternConfig {
			patterns[i] = &tenant.RedactPatternConfig{Name: pattern.Name, Pattern: pattern.Pattern}
		}
		store := tenant.NewStore()
		if err := store.Set(imagePromptOrg, &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{
			Enabled: true, Mode: mode, ContentMode: string(rc.Privacy.ContentMode), Patterns: patterns,
		}}); err != nil {
			panic(err)
		}
		p.SetTenantStore(store)
		p.SetRedactor(privacy.New(privacy.ModeNone, nil))
		rc.Metadata[tenant.MetadataKeyOrgID] = imagePromptOrg
		rc.Metadata["org_privacy_enabled"] = "true"
		rc.Metadata["org_privacy_mode"] = mode
		return
	}
	p.SetRedactor(privacy.New(mode, imagePromptPatternConfig))
}

func imagePromptExport(t *testing.T, source, mode string, contentMode models.ContentMode) map[string]any {
	t.Helper()
	p, _ := bodyPlugin(t, true)
	t.Cleanup(p.Close)
	rc := imagePromptRC(contentMode)
	if mode != "absent" {
		imagePromptPolicy(p, rc, source, mode)
	} else {
		p.SetRedactor(nil)
	}
	return c26Export(t, p, rc)
}

// decodedInputValue is input.value as text: full redaction replaces the whole
// body with the bare marker, patterns keep the body's JSON form.
func decodedInputValue(t *testing.T, attrs map[string]any) any {
	t.Helper()
	raw, ok := attrs["input.value"].(string)
	if !ok {
		t.Fatalf("input.value missing: %v", attrs["input.value"])
	}
	var decoded any
	if err := json.Unmarshal([]byte(raw), &decoded); err != nil {
		return raw
	}
	return decoded
}

var imagePromptContentModes = []models.ContentMode{
	models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone,
}

func TestImagePromptUnderFullRedactionCarriesNoText(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, contentMode := range imagePromptContentModes {
			t.Run(source+"/"+string(contentMode), func(t *testing.T) {
				attrs := imagePromptExport(t, source, privacy.ModeFull, contentMode)
				if got := attrs["gen_ai.image.prompt"]; got != "[REDACTED]" {
					t.Errorf("gen_ai.image.prompt = %v, want [REDACTED]", got)
				}
				if got := attrs["gen_ai.image.revised_prompt"]; got != "[REDACTED]" {
					t.Errorf("gen_ai.image.revised_prompt = %v, want [REDACTED]", got)
				}
				if got := decodedInputValue(t, attrs); got != attrs["gen_ai.image.prompt"] {
					t.Errorf("input.value = %v, gen_ai.image.prompt = %v: not the same redaction", got, attrs["gen_ai.image.prompt"])
				}
				if attrs["gen_ai.image.size"] != "1024x1024" {
					t.Errorf("gen_ai.image.size changed: %v", attrs["gen_ai.image.size"])
				}
			})
		}
	}
}

func TestImagePromptUnderPatternsRedactsOnlyMatches(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, contentMode := range imagePromptContentModes {
			t.Run(source+"/"+string(contentMode), func(t *testing.T) {
				attrs := imagePromptExport(t, source, privacy.ModePatterns, contentMode)
				if got := attrs["gen_ai.image.prompt"]; got != imagePromptPatterns {
					t.Errorf("gen_ai.image.prompt = %v, want %q", got, imagePromptPatterns)
				}
				if got := attrs["gen_ai.image.revised_prompt"]; got != imageRevisedPattern {
					t.Errorf("gen_ai.image.revised_prompt = %v, want %q", got, imageRevisedPattern)
				}
				// The opaque request body stays whole-redacted under masking.
				if got := decodedInputValue(t, attrs); got != "[REDACTED]" {
					t.Errorf("input.value = %v, want [REDACTED]", got)
				}
			})
		}
	}
}

func TestImagePromptWithRedactionOffIsUnchanged(t *testing.T) {
	for _, mode := range []string{privacy.ModeNone, "absent"} {
		for _, contentMode := range imagePromptContentModes {
			t.Run(mode+"/"+string(contentMode), func(t *testing.T) {
				attrs := imagePromptExport(t, "global", mode, contentMode)
				if got := attrs["gen_ai.image.prompt"]; got != imagePromptText {
					t.Errorf("gen_ai.image.prompt = %v, want the prompt unchanged", got)
				}
				if got := attrs["gen_ai.image.revised_prompt"]; got != imageRevisedText {
					t.Errorf("gen_ai.image.revised_prompt = %v, want it unchanged", got)
				}
			})
		}
	}
}
