package bedrock

import "github.com/futureagi/agentcc-gateway/internal/models"

func (u *converseUsage) toUsage() *models.Usage {
	if u == nil {
		return nil
	}
	return models.NewUsage(u.InputTokens, u.OutputTokens)
}
