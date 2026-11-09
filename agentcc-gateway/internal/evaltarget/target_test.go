package evaltarget

import "testing"

func Test733ServiceTargetNormalization(t *testing.T) {
	for _, tc := range []struct{ raw, want string }{
		{" HTTP://LOCALHOST:80/// ", "http://localhost"},
		{"https://LOCALHOST:443/", "https://localhost"},
		{"http://[::1]:8000/", "http://[::1]:8000"},
		{"http://localhost:080/", "http://localhost"},
		{"http://localhost:8000/", "http://localhost:8000"},
	} {
		got, err := Normalize(tc.raw)
		if err != nil || got != tc.want {
			t.Fatalf("normalization mismatch: got %q, err=%v", got, err)
		}
		if _, err := ServiceBase(tc.raw, tc.want); err != nil {
			t.Fatal(err)
		}
	}
	for _, raw := range []string{"", "//localhost", "ftp://localhost", "http://localhost/path", "http://localhost/Path", "http://localhost?", "http://localhost#", "http://user:password@localhost", "http://localhost:0", "http://localhost:", "http://localhost:65536", "http://localhost/%2f", "http://local\thost"} {
		if _, err := Normalize(raw); err == nil {
			t.Fatal("invalid origin accepted")
		}
		if _, err := ServiceBase("http://localhost", raw); err == nil {
			t.Fatal("invalid operator origin accepted")
		}
	}
}
