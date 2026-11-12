package main

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// TestPrivacyWiringInstallsDefaultContentMode: the privacy step hands
// privacy.default_content_mode to the resolver, so a request nothing else
// restricts resolves to it; an unknown value fails the step.
func TestPrivacyWiringInstallsDefaultContentMode(t *testing.T) {
	t.Cleanup(func() { _, _ = privacy.SetDefaultContentMode("full") })

	cfg := &config.Config{}
	cfg.Privacy.DefaultContentMode = "metadata"
	if err := initPrivacy(&wiringDeps{cfg: cfg}); err != nil {
		t.Fatalf("initPrivacy: %v", err)
	}
	got := privacy.ResolveContentMode(privacy.ContentModeSources{Default: privacy.DefaultContentMode()})
	if got.ContentMode != models.ContentModeMetadata || got.Source != models.PrivacySourceDefault {
		t.Fatalf("resolved %+v, want metadata from the default", got)
	}

	cfg.Privacy.DefaultContentMode = "everything"
	if err := initPrivacy(&wiringDeps{cfg: cfg}); err == nil {
		t.Fatal("an unknown default content mode was accepted")
	}
}
