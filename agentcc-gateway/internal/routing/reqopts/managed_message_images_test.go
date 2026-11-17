package reqopts

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestManagedMessageImagesAreResponseOnly(t *testing.T) {
	for _, raw := range []string{`[{"image_url":{"url":"private-image-canary"}}]`, `[]`, `null`, `{"private-image-canary":1}`} {
		req := request(t, `{"model":"image","messages":[{"role":"assistant","content":"hi","images":`+raw+`}]}`)
		var opts models.RoutingOptions
		for _, card := range []*models.PriceCard{nil, {Inclusive: &models.InclusivePrice{Model: "image", Image: "0.01", ImageOutput: true}}} {
			err := CheckManaged(req, &opts, card)
			if err == nil || err.Status != 400 || err.Code != CodeUnsupportedFieldForManaged || err.Param == nil || *err.Param != "messages[0].images" || strings.Contains(err.Message, "private-image-canary") {
				t.Errorf("missing value-free images refusal: %+v", err)
			}
		}
		removed := StripForManaged(req, nil)
		if !slices.Equal(removed, []string{"messages[0].images"}) {
			t.Errorf("removed fields=%v", removed)
		}
		data, err := json.Marshal(req)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(data), `"images"`) {
			t.Fatal("unpriced images survived managed fallback rewrite")
		}
	}
}
