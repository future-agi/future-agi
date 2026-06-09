package edge

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func testConfig() config.EdgeConfig {
	return config.EdgeConfig{
		Enabled: true,
		Regions: []config.EdgeRegionConfig{
			{Name: "us-east", Backend: "https://gateway-us-east.example.com", Weight: 50},
			{Name: "eu-west", Backend: "https://gateway-eu-west.example.com", Weight: 50},
		},
		Cache: config.EdgeCacheConfig{
			Enabled:    true,
			DefaultTTL: 300,
			MaxSize:    1048576,
		},
	}
}

func TestGenerateDisabled(t *testing.T) {
	cfg := config.EdgeConfig{Enabled: false}
	result := Generate(cfg)
	if !strings.Contains(result, "disabled") {
		t.Fatalf("expected disabled message, got: %s", result)
	}
}

func TestGenerateNoRegions(t *testing.T) {
	cfg := config.EdgeConfig{Enabled: true}
	result := Generate(cfg)
	if !strings.Contains(result, "no regions") {
		t.Fatalf("expected no regions message, got: %s", result)
	}
}

func TestGenerateContainsBackends(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, "https://gateway-us-east.example.com") {
		t.Fatal("expected us-east backend URL in output")
	}
	if !strings.Contains(result, "https://gateway-eu-west.example.com") {
		t.Fatal("expected eu-west backend URL in output")
	}
}

func TestGenerateContainsRegionNames(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, `"us-east"`) {
		t.Fatal("expected us-east region name in output")
	}
	if !strings.Contains(result, `"eu-west"`) {
		t.Fatal("expected eu-west region name in output")
	}
}

func TestGenerateContainsWeights(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, "weight: 50") {
		t.Fatal("expected weight: 50 in output")
	}
}

func TestGenerateContainsCacheConfig(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, "CACHE_ENABLED = true") {
		t.Fatal("expected CACHE_ENABLED = true")
	}
	if !strings.Contains(result, "CACHE_DEFAULT_TTL = 300") {
		t.Fatal("expected CACHE_DEFAULT_TTL = 300")
	}
	if !strings.Contains(result, "CACHE_MAX_SIZE = 1048576") {
		t.Fatal("expected CACHE_MAX_SIZE = 1048576")
	}
}

func TestGenerateContainsCacheLogic(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, "caches.default") {
		t.Fatal("expected Cache API usage in output")
	}
	if !strings.Contains(result, "x-agentcc-edge-cache") {
		t.Fatal("expected edge cache header")
	}
	if !strings.Contains(result, "x-agentcc-cache-ttl") {
		t.Fatal("expected cache TTL header check")
	}
}

func TestGenerateNoCacheWhenDisabled(t *testing.T) {
	cfg := testConfig()
	cfg.Cache.Enabled = false
	result := Generate(cfg)
	if !strings.Contains(result, "CACHE_ENABLED = false") {
		t.Fatal("expected CACHE_ENABLED = false")
	}
	// Should not have cache logic blocks.
	if strings.Contains(result, "caches.default") {
		t.Fatal("expected no cache logic when cache disabled")
	}
}

func TestGenerateContainsSelectBackend(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, "selectBackend") {
		t.Fatal("expected selectBackend function")
	}
	if !strings.Contains(result, "TOTAL_WEIGHT") {
		t.Fatal("expected TOTAL_WEIGHT constant")
	}
}

func TestGenerateContainsStreamDetection(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, "isStream") {
		t.Fatal("expected stream detection")
	}
	if !strings.Contains(result, "parsed.stream") {
		t.Fatal("expected stream field check")
	}
}

func TestGenerateContainsHeaderForwarding(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, "request.headers") {
		t.Fatal("expected header forwarding")
	}
	if !strings.Contains(result, "x-agentcc-edge-region") {
		t.Fatal("expected edge region header")
	}
}

func TestGenerateIsValidJS(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, "export default") {
		t.Fatal("expected ES module export")
	}
	if !strings.Contains(result, "async fetch") {
		t.Fatal("expected fetch handler")
	}
}

func TestGenerateSingleRegion(t *testing.T) {
	cfg := config.EdgeConfig{
		Enabled: true,
		Regions: []config.EdgeRegionConfig{
			{Name: "us-east", Backend: "https://api.example.com", Weight: 100},
		},
		Cache: config.EdgeCacheConfig{Enabled: false},
	}
	result := Generate(cfg)
	if !strings.Contains(result, "https://api.example.com") {
		t.Fatal("expected single backend URL")
	}
}

func TestGenerateAutoGenComment(t *testing.T) {
	result := Generate(testConfig())
	if !strings.Contains(result, "Auto-generated") {
		t.Fatal("expected auto-generated comment")
	}
	if !strings.Contains(result, "/-/admin/edge/config") {
		t.Fatal("expected admin endpoint reference")
	}
}
