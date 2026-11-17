package anthropicfmt

import "testing"

func TestPaymentRequiredMapsToBillingError(t *testing.T) {
	if got := MapStatusToErrorType(402); got != "billing_error" {
		t.Fatalf("402 maps to %q", got)
	}
}
