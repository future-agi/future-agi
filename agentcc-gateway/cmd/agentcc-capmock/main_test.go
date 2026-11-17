package main

import (
	"strings"
	"testing"
)

// The mock refuses an environment holding a variable named like a vendor
// key, names it, and never prints its value.
func TestRefusesVendorKeyEnv(t *testing.T) {
	err := checkEnv([]string{"PATH=/bin", "TAVILY_API_KEY=tvly-not-a-real-key"})
	if err == nil || !strings.Contains(err.Error(), "TAVILY_API_KEY") || strings.Contains(err.Error(), "tvly-not-a-real-key") {
		t.Fatalf("checkEnv: %v", err)
	}
	if err := checkEnv([]string{"PATH=/bin", "HOME=/home/x", "SCN_REDIS=127.0.0.1:16379"}); err != nil {
		t.Errorf("a clean environment refused: %v", err)
	}
}

func TestFlags(t *testing.T) {
	if _, err := parseFlags(nil); err == nil {
		t.Error("no --addr accepted")
	}
	o, err := parseFlags([]string{"--addr", "127.0.0.1:0", "--watch-pid", "42"})
	if err != nil || o.addr != "127.0.0.1:0" || o.watchPID != 42 {
		t.Errorf("parseFlags: %+v %v", o, err)
	}
}
