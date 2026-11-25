package middleware

import "testing"

func TestAuthorizationAPIKeyR4SharedContract(t *testing.T) {
	for _, tc := range []struct {
		header, key string
		present     bool
	}{
		{"Bearer synthetic", "synthetic", true},
		{"bearer synthetic", "synthetic", true},
		{"BEARER synthetic", "synthetic", true},
		{"bEaReR synthetic", "synthetic", true},
		{"Token synthetic", "synthetic", true},
		{"token synthetic", "synthetic", true},
		{"Bearer   synthetic  ", "synthetic", true},
		{"Bearer ", "", true},
		{"bearer ", "", true},
		{"Bearer", "", false},
		{"Basic synthetic", "", false},
		{"", "", false},
	} {
		t.Run(tc.header, func(t *testing.T) {
			key, present := AuthorizationAPIKey(tc.header)
			if key != tc.key || present != tc.present {
				t.Fatalf("got key=%q present=%v; want key=%q present=%v", key, present, tc.key, tc.present)
			}
		})
	}
}
