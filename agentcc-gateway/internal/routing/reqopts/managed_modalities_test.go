package reqopts

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestManagedModalitiesUseExactInclusiveCard(t *testing.T) {
	image := models.InclusivePrice{Model: "google/gemini-3-pro-image", Prompt: "0.000002", Completion: "0.000012", Image: "0.000002", TextOutput: true, ImageOutput: true}
	for _, tc := range []struct {
		name, fields string
		change       func(*models.InclusivePrice)
		noCard       bool
		allow        bool
	}{
		{name: "image and text", fields: `"modalities":["image","text"]`, allow: true},
		{name: "image only", fields: `"modalities":["image"]`, allow: true},
		{name: "text only", fields: `"modalities":["text"]`, allow: true},
		{name: "hoisted", fields: `"extra_body":{"modalities":["image","text"]}`, allow: true},
		{name: "audio", fields: `"modalities":["text","audio"]`},
		{name: "unknown", fields: `"modalities":["image","secret-canary"]`},
		{name: "case sensitive", fields: `"modalities":["Image"]`},
		{name: "missing card", fields: `"modalities":["image"]`, noCard: true},
		{name: "different listing", fields: `"modalities":["image"]`, change: func(q *models.InclusivePrice) { q.Model = "other" }},
		{name: "input image price only", fields: `"modalities":["image"]`, change: func(q *models.InclusivePrice) { q.ImageOutput = false }},
		{name: "unadvertised text", fields: `"modalities":["text"]`, change: func(q *models.InclusivePrice) { q.TextOutput = false }},
		{name: "missing price", fields: `"modalities":["image"]`, change: func(q *models.InclusivePrice) { q.Image = "" }},
		{name: "zero price", fields: `"modalities":["image"]`, change: func(q *models.InclusivePrice) { q.Image = "0.0" }},
		{name: "invalid price", fields: `"modalities":["image"]`, change: func(q *models.InclusivePrice) { q.Image = "invalid" }},
		{name: "missing text price", fields: `"modalities":["text"]`, change: func(q *models.InclusivePrice) { q.Completion = "" }},
		{name: "generation card", fields: `"modalities":["image"]`, change: func(q *models.InclusivePrice) { q.Generation = "0.08" }},
		{name: "router card", fields: `"modalities":["image"]`, change: func(q *models.InclusivePrice) { q.RouterUniverse = "[]" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			q := image
			if tc.change != nil {
				tc.change(&q)
			}
			card := &models.PriceCard{Inclusive: &q}
			if tc.noCard {
				card = nil
			}
			req := request(t, `{"model":"google/gemini-3-pro-image","messages":[{"role":"user","content":"hi"}],`+tc.fields+`}`)
			parsed, err := Parse(req)
			if err != nil {
				t.Fatal(err)
			}
			want := slices.Clone(req.Modalities)
			apiErr := CheckManaged(req, &parsed.Options, card)
			if tc.allow {
				if apiErr != nil {
					t.Fatalf("priced modalities refused: %+v", apiErr)
				}
			} else if apiErr == nil || apiErr.Status != 400 || apiErr.Code != CodeUnsupportedFieldForManaged || apiErr.Param == nil || *apiErr.Param != "modalities" || strings.Contains(apiErr.Message, "secret-canary") {
				t.Fatalf("missing value-free refusal: %+v", apiErr)
			}
			removed := StripForManaged(req, card)
			if tc.allow {
				if len(removed) != 0 || !slices.Equal(req.Modalities, want) {
					t.Fatalf("priced modalities stripped: %v %v", removed, req.Modalities)
				}
			} else if !slices.Equal(removed, []string{"modalities"}) || req.Modalities != nil {
				t.Fatalf("unpriced fallback retained modalities: %v %v", removed, req.Modalities)
			}
		})
	}
}

func TestManagedModalitiesCannotOverrideBoundThroughExtra(t *testing.T) {
	req := &models.ChatCompletionRequest{Model: "image", Modalities: []string{"image"}, Extra: map[string]json.RawMessage{"modalities": json.RawMessage(`["audio"]`)}}
	card := &models.PriceCard{Inclusive: &models.InclusivePrice{Model: "image", Image: "0.01", ImageOutput: true}}
	var opts models.RoutingOptions
	if err := CheckManaged(req, &opts, card); err == nil || err.Code != CodeUnsupportedFieldForManaged {
		t.Fatalf("raw override bypassed admission: %+v", err)
	}
	StripForManaged(req, card)
	if _, exists := req.Extra["modalities"]; exists || !slices.Equal(req.Modalities, []string{"image"}) {
		t.Fatal("raw override survived managed rewrite")
	}
	// The fallback uses its own card: a paid primary cannot lend permission.
	StripForManaged(req, &models.PriceCard{Prompt: 1, Completion: 1})
	if req.Modalities != nil {
		t.Fatal("token fallback inherited image permission")
	}
}
