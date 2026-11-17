package main

import (
	"log/slog"
	"os"
	"strings"
	"testing"
)

const testPepper = "fi-collector-cmd-test-pepper-0123456789abcdef"

// TestMain gives every config test the pepper a real deployment must set; the
// tests below unset it to prove the collector then refuses to start.
func TestMain(m *testing.M) {
	if os.Getenv("FI_API_SECRET_PEPPER") == "" {
		os.Setenv("FI_API_SECRET_PEPPER", testPepper)
	}
	os.Exit(m.Run())
}

func TestPepperFromEnvironmentReachesAuthConfig(t *testing.T) {
	t.Setenv("FI_API_SECRET_PEPPER", testPepper)
	t.Setenv("FI_API_SECRET_PEPPERS_RETIRED", " , an-old-pepper-that-is-long-enough-000001 ")
	var cfg rootConfig
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatalf("applyEnvOverrides: %v", err)
	}
	if cfg.Auth.Peppers == nil {
		t.Fatal("the pepper did not reach the auth config")
	}
	stored := cfg.Auth.Peppers.HashSecret("s3cret")
	if !cfg.Auth.Peppers.VerifySecret(stored, "", "s3cret") {
		t.Fatal("a hash under the configured pepper must verify")
	}
}

func TestCollectorRefusesToStartWithoutAPepperOutsideLocal(t *testing.T) {
	t.Setenv("FI_API_SECRET_PEPPER", "")
	t.Setenv("ENV_TYPE", "production")
	t.Setenv("SECRET_KEY", "a-secret-key")
	var cfg rootConfig
	err := applyEnvOverrides(slog.Default(), &cfg)
	if err == nil || !strings.Contains(err.Error(), "FI_API_SECRET_PEPPER") {
		t.Fatalf("got %v, want an error naming FI_API_SECRET_PEPPER", err)
	}
}

func TestLocalInstallDerivesThePepperLikeDjango(t *testing.T) {
	t.Setenv("FI_API_SECRET_PEPPER", "")
	t.Setenv("ENV_TYPE", "local")
	t.Setenv("SECRET_KEY", "golden-secret-key")
	var cfg rootConfig
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatalf("applyEnvOverrides: %v", err)
	}
	// Django's value for SECRET_KEY "golden-secret-key" (pkg/auth golden vector).
	want := "hmac-sha256$b9df05ee$d577a3daef0ced43290fb3a11c8d9ab5f76bb70d7729894d3950c937d8152f17"
	if got := cfg.Auth.Peppers.HashSecret("golden-secret-4f1c2b"); got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}
