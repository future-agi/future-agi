package contentmod

import (
	"context"
	"math"
	"strings"
	"testing"
)

func TestSelectedOrderR4DoesNotMutateLegacyEvaluator(t *testing.T) {
	g := New(map[string]interface{}{"category_threshold": 17, "weights": map[string]interface{}{"toxicity": .1, "hate_speech": .2, "violence": .3, "sexual": .4, "self_harm": .5}})
	// Force a noncanonical legacy order, independent of randomized map iteration.
	cats := map[string]categoryChecker{}
	var phrases []string
	for _, cat := range g.categories {
		cats[cat.name] = cat
		phrases = append(phrases, cat.phrases...)
	}
	order := []string{"self_harm", "sexual", "violence", "hate_speech", "toxicity"}
	for i, name := range order {
		g.categories[i] = cats[name]
	}
	input := makeInput(strings.Join(phrases, "; "))
	before := g.Check(context.Background(), input)
	selected := g.SelectedPolicyGuardrail().(*ContentModerationGuardrail)
	if selected == g || &selected.categories[0] == &g.categories[0] {
		t.Fatal("selected adapter shares mutable category ordering")
	}
	selectedOrder := []string{"toxicity", "hate_speech", "violence", "sexual", "self_harm"}
	for i, cat := range selected.categories {
		if cat.name != selectedOrder[i] || g.categories[i].name != order[i] {
			t.Fatal("selected order missing or legacy order changed")
		}
	}
	const maximum = 0x1.b3b3b3b3b3b3bp-1
	if selected.ValidateSelectedThreshold(maximum) == nil || selected.ValidateSelectedThreshold(math.Nextafter(maximum, 0)) != nil {
		t.Fatal("selected strict-boundary readiness diverged")
	}
	after := g.Check(context.Background(), input)
	if math.Float64bits(before.Score) != math.Float64bits(after.Score) || before.Message != after.Message {
		t.Fatal("selected readiness changed legacy score or category message order")
	}
	if got := selected.Check(context.Background(), input).Score; math.Float64bits(got) != math.Float64bits(maximum) {
		t.Fatalf("selected execution=%x maximum=%x", math.Float64bits(got), math.Float64bits(maximum))
	}
}
