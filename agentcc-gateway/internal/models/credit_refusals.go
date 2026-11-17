package models

import "fmt"

// The messages of the Future AGI credit refusals (402
// managed_credits_exhausted and managed_credits_insufficient). The product
// is "Future AGI credits" wherever a customer reads it; "managed" is the
// gateway's own word and never reaches the message. Where to add credit is
// named; on an install where credit is only granted by an operator (credit
// purchase not installed, managed.grant_only_credits) the message says so
// instead of sending the caller to buy credit.

const (
	// creditAddHosted is where a hosted organization adds credit.
	creditAddHosted = "Add credit on the Settings Billing page"
	// creditAddGrantOnly is the remedy where credit purchase is not
	// installed.
	creditAddGrantOnly = "Credit purchase is not available on this self-hosted install, so credit is granted by its operator: ask them to grant Future AGI credit"
	// creditOwnKeys is the other way to call a model.
	creditOwnKeys = "use your own provider keys on the Gateway Providers page"
)

// CreditsExhaustedMessage is the message of the 402
// managed_credits_exhausted: the org has no Future AGI credit to hold a
// call on. neverFunded is an org that was never granted any, which is never
// told it has none "left"; grantOnly is an install without credit purchase.
func CreditsExhaustedMessage(neverFunded, grantOnly bool) string {
	state := "Your organization has used all of its Future AGI credit."
	if neverFunded {
		state = "Your organization has no Future AGI credit."
	}
	return fmt.Sprintf("%s %s, or %s.", state, creditAdd(grantOnly), creditOwnKeys)
}

// CreditsInFlightMessage is the message of the 402
// managed_credits_insufficient for credit that requests in flight hold.
func CreditsInFlightMessage(grantOnly bool) string {
	if grantOnly {
		return "Your organization's Future AGI credit is held by requests in flight. Retry shortly."
	}
	return "Your organization's Future AGI credit is held by requests in flight. Retry shortly, or add credit on the Settings Billing page."
}

// CreditsInsufficientMessage is the message of the 402
// managed_credits_insufficient for credit that does not cover a call's
// worst-case cost: what names the call ("request", "call"), available and
// required are in USD, and maxTokens offers lowering max_tokens, for a
// model request.
func CreditsInsufficientMessage(what string, available, required float64, maxTokens, grantOnly bool) string {
	lower := ","
	if maxTokens {
		lower = ", lower max_tokens,"
	}
	return fmt.Sprintf("Your organization's Future AGI credit ($%.6f) does not cover this %s's worst-case cost ($%.6f). "+
		"%s%s or %s.", available, what, required, creditAdd(grantOnly), lower, creditOwnKeys)
}

// creditAdd is where to add credit, by install.
func creditAdd(grantOnly bool) string {
	if grantOnly {
		return creditAddGrantOnly
	}
	return creditAddHosted
}
