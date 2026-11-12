package server

import (
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Generation prices cannot be expressed as zero token prices. This additive
// field uses an exact USD decimal after the same two-stage 5% calculation as
// settlement. Token pricing is absent, not zero, on paid generation listings.
func generationPrice(m *catalog.Model, source string) *models.GenerationPricingAPI {
	q := m.InclusivePrice(source)
	if q == nil || q.Generation == "" {
		return nil
	}
	_, micros, err := inclusive.Charge(q.Generation)
	if err != nil {
		return nil
	}
	return &models.GenerationPricingAPI{Unit: "generation", Currency: "USD", Amount: fmt.Sprintf("%d.%06d", micros/1000000, micros%1000000)}
}
