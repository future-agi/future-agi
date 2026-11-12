package cost

import (
	"fmt"
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A token total cannot silently erase a positive, negative or unknown image
// quote. These tests use the existing pricing seam so the base fails behaviour,
// not compilation. An inclusive provider total is a separate supported source.
func TestDimensionalCardNeverDropsImagePrice(t *testing.T) {
	for _, price := range []float64{0.000002, -1, math.NaN(), math.Inf(1)} {
		t.Run(fmt.Sprint(price), func(t *testing.T) {
			a := &models.Attempt{CredentialType: models.CredentialManaged}
			u := &models.Usage{PromptTokens: 1000, CompletionTokens: 200}
			card := models.PriceCard{Prompt: 0.000002, Completion: 0.00001, Image: price}
			up, billed, landed, source, ok := PriceAttempt(a, u, card, 500, 0)
			if ok || up != 0 || billed != 0 || landed != 0 || source != "unpriced" {
				t.Fatalf("unsupported dimension priced as tokens: up=%d billed=%d landed=%d source=%s ok=%t", up, billed, landed, source, ok)
			}
		})
	}
}

func TestDimensionalInclusiveTotalIsNotChargedTwice(t *testing.T) {
	total := 0.017723
	u := &models.Usage{PromptTokens: 1000, CompletionTokens: 200, Cost: &total}
	a := &models.Attempt{CredentialType: models.CredentialManaged}
	card := models.PriceCard{Prompt: 0.000002, Completion: 0.00001, Image: 0.000002}
	up, billed, _, source, ok := PriceAttempt(a, u, card, 500, 0)
	if !ok || up != 17723 || billed != 18609 || source != "provider_reported" {
		t.Fatalf("inclusive total: up=%d billed=%d source=%s ok=%t", up, billed, source, ok)
	}
}
