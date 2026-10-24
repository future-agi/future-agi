package routing

// AutoPolicy.Require (the agent context's require) sets
// aside every candidate lacking one of the capabilities, for each value
// models.AgentRequireCaps accepts, and names the requirement when none is
// left.

import (
	"errors"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestRequireCapabilityChooseAuto(t *testing.T) {
	req := &models.ChatCompletionRequest{Messages: []models.Message{userMsg("hi, what's the capital of France?")}}
	small := autoInfo(0.3, 1.2, allCaps)
	small.MaxInputTokens = 32000
	audio := autoInfo(2, 8, modeldb.CapabilityFlags{AudioInput: true})
	pool := []AutoCandidate{
		{Model: "vendor/nano", ProviderID: "or", Info: autoInfo(0.05, 0.2, modeldb.CapabilityFlags{})},
		{Model: "vendor/mini-32k", ProviderID: "or", Info: small},
		{Model: "vendor/frontier", ProviderID: "or", Info: autoInfo(5, 25, allCaps)},
		{Model: "vendor/voice", ProviderID: "or", Info: audio},
	}

	for _, c := range []struct {
		require []string
		want    []string // the eligible models, in any order
	}{
		{nil, []string{"vendor/nano", "vendor/mini-32k", "vendor/frontier", "vendor/voice"}},
		{[]string{"tools"}, []string{"vendor/mini-32k", "vendor/frontier"}},
		{[]string{"json_schema"}, []string{"vendor/mini-32k", "vendor/frontier"}},
		{[]string{"vision"}, []string{"vendor/mini-32k", "vendor/frontier"}},
		{[]string{"reasoning"}, []string{"vendor/mini-32k", "vendor/frontier"}},
		{[]string{"audio"}, []string{"vendor/voice"}},
		{[]string{"long_context"}, []string{"vendor/nano", "vendor/frontier", "vendor/voice"}},
		{[]string{"vision", "long_context"}, []string{"vendor/frontier"}},
	} {
		d, err := ChooseAuto(req, pool, AutoPolicy{Require: c.require})
		if err != nil {
			t.Fatalf("require %v: %v", c.require, err)
		}
		var got []string
		for _, r := range d.Ranked {
			got = append(got, r.Model)
		}
		if len(got) != len(c.want) {
			t.Fatalf("require %v: eligible %v, want %v", c.require, got, c.want)
		}
		for _, m := range c.want {
			if !contains(got, m) {
				t.Fatalf("require %v: eligible %v, want %v", c.require, got, c.want)
			}
		}
		for _, r := range d.Rejected {
			if !strings.HasPrefix(r.Reason, "lacks required ") {
				t.Fatalf("require %v: %s rejected for %q", c.require, r.Model, r.Reason)
			}
		}
		if len(c.require) > 0 && !strings.Contains(d.Reason(), "requires "+strings.Join(c.require, ", ")) {
			t.Fatalf("require %v: reason %q does not name it", c.require, d.Reason())
		}
	}

	_, err := ChooseAuto(req, pool, AutoPolicy{Require: []string{"audio", "vision"}})
	var none *NoAutoCandidateError
	if !errors.As(err, &none) || !strings.Contains(err.Error(), "required audio, vision") {
		t.Fatalf("require audio and vision: err %v; want NoAutoCandidateError naming both", err)
	}
	if none != nil && len(none.Rejected) != len(pool) {
		t.Fatalf("rejected %d of %d", len(none.Rejected), len(pool))
	}
}
