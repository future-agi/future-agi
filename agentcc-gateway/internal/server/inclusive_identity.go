package server

import "github.com/futureagi/agentcc-gateway/internal/models"

// Capture the upstream identity before the client response ID/model rewrite.
func servedIdentity(resp any) (model, generation string) {
	switch r := resp.(type) {
	case *models.ChatCompletionResponse:
		if r != nil {
			return r.Model, r.ID
		}
	case *models.StreamChunk:
		if r != nil {
			return r.Model, r.ID
		}
	}
	return "", ""
}
