package restmcp

import "testing"

func TestCN28Batch(t *testing.T) {
	checkV10Batch(t, []string{"google-slides", "google-meet", "microsoft-onedrive"})
}
