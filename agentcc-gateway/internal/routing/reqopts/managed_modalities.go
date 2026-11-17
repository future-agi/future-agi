package reqopts

import (
	"math/big"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// pricedModalities uses only the admitted hop's frozen card, never a fresh
// catalogue lookup. The image-family bound prices text tokens and output image
// occurrences; neither token cards, generation cards nor router universes gain
// output permission here. Settlement remains the single inclusive total.
func pricedModalities(req *models.ChatCompletionRequest, card *models.PriceCard) bool {
	if card == nil || card.Inclusive == nil {
		return false
	}
	q := card.Inclusive
	if q.Model != req.Model || q.Generation != "" || q.RouterUniverse != "" {
		return false
	}
	image, ok := new(big.Rat).SetString(q.Image)
	if !ok || image.Sign() <= 0 {
		return false
	}
	for _, modality := range req.Modalities {
		switch modality {
		case "text":
			rate, ok := new(big.Rat).SetString(q.Completion)
			if !q.TextOutput || !ok || rate.Sign() < 0 {
				return false
			}
		case "image":
			if !q.ImageOutput {
				return false
			}
		default:
			return false
		}
	}
	return true
}
