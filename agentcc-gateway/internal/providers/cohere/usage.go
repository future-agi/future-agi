package cohere

import "github.com/futureagi/agentcc-gateway/internal/models"

func (u *cohereUsage) toUsage() *models.Usage {
	var prompt, completion *int
	if u.Tokens != nil {
		prompt, completion = u.Tokens.InputTokens, u.Tokens.OutputTokens
	}
	if u.BilledUnits != nil {
		if prompt == nil {
			prompt = u.BilledUnits.InputTokens
		}
		if completion == nil {
			completion = u.BilledUnits.OutputTokens
		}
	}
	return models.NewUsage(prompt, completion)
}
