package config

import (
	"encoding/json"
	"gopkg.in/yaml.v3"
	"os"
	"strings"
	"testing"
	"time"
)

func validVideo() *Config {
	c := DefaultConfig()
	c.Video.Enabled = true
	c.Redis.Enabled = true
	c.Video.CorrelationSecret = "test-correlation"
	c.Video.Providers["byteplus"] = VideoProviderConfig{Enabled: true, APIKey: "test-key", BaseURL: "https://ark.ap-southeast.bytepluses.com/api/v3", Region: "ap-southeast-1", AccountRef: "test", Models: []string{"dreamina-seedance-2-5-260628"}, Limits: VideoProviderLimits{SubmitRPM: 10, PollQPS: 10, MaxActiveTasks: 5}, Deadlines: VideoProviderDeadlines{Connect: 10 * time.Second, Read: 30 * time.Second, Submit: 30 * time.Second, Run: 2 * time.Hour, Reconcile: 15 * time.Minute}, Poll: VideoProviderPoll{BaseInterval: 5 * time.Second}, TariffRevision: "byteplus-2026-10-02", SafetyIdentifierMode: "org_key_hmac", ExecutionExpiresAfter: 7200, AcknowledgePublicOutput: true}
	return c
}
func TestVideoDefaults(t *testing.T) {
	c := DefaultConfig()
	if c.Video.Enabled || c.Video.KillSwitch || c.Video.AllowNonDurableStore || c.Video.Reconcile.AllowAbsentResubmit || c.Video.Artifacts.Presign.Enabled {
		t.Fatal("defaults enable video")
	}
	if c.Video.Copy.Deadline != 10*time.Minute || c.Video.Copy.MaxConcurrent != 8 || c.Video.Retention.JobMetadata != 720*time.Hour {
		t.Fatal(c.Video)
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
}
func TestVideoValidation(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func(*Config)
		field  string
	}{
		{"redis", func(c *Config) { c.Redis.Enabled = false }, "redis.enabled"},
		{"memory", func(c *Config) { c.Video.Store = "memory" }, "allow_non_durable_store"},
		{"store", func(c *Config) { c.Video.Store = "other" }, "video.store"},
		{"submit deadline", func(c *Config) { c.Video.Submit.Deadline = 0 }, "submit.deadline"},
		{"sync wait", func(c *Config) { c.Video.Submit.SyncWait = time.Hour }, "sync_wait"},
		{"lease", func(c *Config) { c.Video.Submit.LeaseTTL = 0 }, "lease_ttl"},
		{"reconcile", func(c *Config) { c.Video.Submit.UnknownReconcileDeadline = 0 }, "unknown_reconcile_deadline"},
		{"poll concurrency", func(c *Config) { c.Video.Poll.MaxConcurrentTotal = 0 }, "max_concurrent_total"},
		{"provider concurrency", func(c *Config) { c.Video.Poll.PerProviderMaxConcurrent = 0 }, "per_provider_max_concurrent"},
		{"poll interval", func(c *Config) { c.Video.Poll.MaxInterval = 0 }, "max_interval"},
		{"poll failures", func(c *Config) { c.Video.Poll.MaxConsecutiveFailures = 0 }, "max_consecutive_failures"},
		{"unknown status", func(c *Config) { c.Video.Poll.MaxUnknownStatus = 0 }, "max_unknown_status"},
		{"timeout reconcile", func(c *Config) { c.Video.Poll.TimeoutReconcileWindow = 0 }, "timeout_reconcile_window"},
		{"copy deadline", func(c *Config) { c.Video.Copy.Deadline = 0 }, "copy.deadline"},
		{"copy concurrency", func(c *Config) { c.Video.Copy.MaxConcurrent = 0 }, "copy.max_concurrent"},
		{"inline", func(c *Config) { c.Video.Media.MaxInlineBytes = 0 }, "max_inline_bytes"},
		{"fetch", func(c *Config) { c.Video.Media.FetchTimeout = 0 }, "fetch_timeout"},
		{"ingress", func(c *Config) { c.Video.Media.TotalIngressTimeout = 0 }, "total_ingress_timeout"},
		{"pixels", func(c *Config) { c.Video.Media.MaxDecodedPixels = 0 }, "max_decoded_pixels"},
		{"artifact ttl", func(c *Config) { c.Video.Artifacts.TTL = 0 }, "artifacts.ttl"},
		{"artifact bytes", func(c *Config) { c.Video.Artifacts.MaxBytes = 0 }, "artifacts.max_bytes"},
		{"artifact backend", func(c *Config) { c.Video.Artifacts.Backend = "other" }, "artifacts.backend"},
		{"disk root", func(c *Config) { c.Video.Artifacts.Disk.Root = "" }, "disk.root"},
		{"metadata retention", func(c *Config) { c.Video.Retention.JobMetadata = 0 }, "job_metadata"},
		{"idem retention", func(c *Config) { c.Video.Retention.Idempotency = 0 }, "idempotency"},
		{"active", func(c *Config) { c.Video.Limits.OrgMaxActiveJobs = 0 }, "org_max_active_jobs"},
		{"rpm", func(c *Config) { c.Video.Limits.OrgSubmitRPM = 0 }, "org_submit_rpm"},
		{"correlation", func(c *Config) { c.Video.CorrelationSecret = "" }, "correlation_secret"},
		{"presign", func(c *Config) { c.Video.Artifacts.Presign.Enabled = true }, "presign"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := validVideo()
			tc.change(c)
			if err := c.Validate(); err == nil || !strings.Contains(err.Error(), tc.field) {
				t.Fatalf("want %s: %v", tc.field, err)
			}
		})
	}
	c := validVideo()
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	c.Video.Store = "memory"
	c.Video.AllowNonDurableStore = true
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
}
func TestVideoProviderValidation(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func(*VideoProviderConfig)
		field  string
	}{
		{"credentials", func(p *VideoProviderConfig) { p.APIKey = "" }, "credentials"},
		{"bad scheme", func(p *VideoProviderConfig) { p.APIKey = "secret://bad" }, "credentials"},
		{"models empty", func(p *VideoProviderConfig) { p.Models = nil }, "models"},
		{"model unknown", func(p *VideoProviderConfig) { p.Models = []string{"unknown"} }, "models"},
		{"region", func(p *VideoProviderConfig) { p.Region = "us" }, "region"},
		{"base url", func(p *VideoProviderConfig) { p.BaseURL = "http://example.com" }, "base_url"},
		{"account", func(p *VideoProviderConfig) { p.AccountRef = "" }, "account_ref"},
		{"rpm", func(p *VideoProviderConfig) { p.Limits.SubmitRPM = 0 }, "submit_rpm"},
		{"qps", func(p *VideoProviderConfig) { p.Limits.PollQPS = 0 }, "poll_qps"},
		{"active", func(p *VideoProviderConfig) { p.Limits.MaxActiveTasks = 0 }, "max_active_tasks"},
		{"connect", func(p *VideoProviderConfig) { p.Deadlines.Connect = 0 }, "connect"},
		{"read", func(p *VideoProviderConfig) { p.Deadlines.Read = 0 }, "read"},
		{"submit", func(p *VideoProviderConfig) { p.Deadlines.Submit = 0 }, "submit"},
		{"run", func(p *VideoProviderConfig) { p.Deadlines.Run = 0 }, "run"},
		{"reconcile", func(p *VideoProviderConfig) { p.Deadlines.Reconcile = 0 }, "reconcile"},
		{"reconcile retention", func(p *VideoProviderConfig) { p.Deadlines.Reconcile = 7 * 24 * time.Hour }, "reconcile"},
		{"poll", func(p *VideoProviderConfig) { p.Poll.BaseInterval = 0 }, "base_interval"},
		{"tariff", func(p *VideoProviderConfig) { p.TariffRevision = "" }, "tariff_revision"},
		{"tariff revision", func(p *VideoProviderConfig) { p.TariffRevision = "unknown" }, "tariff_revision"},
		{"acl", func(p *VideoProviderConfig) { p.AcknowledgePublicOutput = false }, "acknowledge_public_output"},
		{"expires", func(p *VideoProviderConfig) { p.ExecutionExpiresAfter = 3600 }, "execution_expires_after"},
		{"mode", func(p *VideoProviderConfig) { p.SafetyIdentifierMode = "bad" }, "safety_identifier_mode"},
		{"min tokens", func(p *VideoProviderConfig) { p.MinTokensWithVideoInput = -1 }, "min_tokens_with_video_input"},
		{"ref", func(p *VideoProviderConfig) { p.APIKey = ""; p.CredentialRef = "provider:missing" }, "credential_ref"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := validVideo()
			p := c.Video.Providers["byteplus"]
			tc.change(&p)
			c.Video.Providers["byteplus"] = p
			if err := c.Validate(); err == nil || !strings.Contains(err.Error(), tc.field) {
				t.Fatalf("want %s: %v", tc.field, err)
			}
		})
	}
	for _, key := range []string{"vault://test/key", "aws-sm://test/key", "gcp-sm://test/key", "azure-kv://test/key"} {
		c := validVideo()
		p := c.Video.Providers["byteplus"]
		p.APIKey = key
		c.Video.Providers["byteplus"] = p
		if err := c.Validate(); err != nil {
			t.Fatal(err)
		}
	}
	c := validVideo()
	p := c.Video.Providers["byteplus"]
	p.TariffRevision = ""
	p.AllowUnpricedModels = true
	c.Video.Providers["byteplus"] = p
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	b, err := json.Marshal(c.Video)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(b), "test-key") || strings.Contains(string(b), "test-correlation") {
		t.Fatal("secret JSON exposure")
	}
}
func TestVideoEnv(t *testing.T) {
	for _, v := range []string{"true", "1", "false", "0"} {
		t.Run(v, func(t *testing.T) {
			t.Setenv("AGENTCC_VIDEO_ENABLED", v)
			t.Setenv("AGENTCC_VIDEO_KILL_SWITCH", v)
			c := DefaultConfig()
			loadFromEnv(c)
			want := v == "true" || v == "1"
			if c.Video.Enabled != want || c.Video.KillSwitch != want {
				t.Fatal(c.Video)
			}
		})
	}
}
func TestVideoExample(t *testing.T) {
	t.Setenv("AGENTCC_VIDEO_ENABLED", "false")
	if _, err := Load("../../config.example.yaml"); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile("../../config.example.yaml")
	if err != nil {
		t.Fatal(err)
	}
	_, block, ok := strings.Cut(string(b), "# video:\n")
	if !ok {
		t.Fatal("missing video example")
	}
	var lines []string
	for _, line := range strings.Split(block, "\n") {
		if strings.HasPrefix(line, "# ") {
			lines = append(lines, strings.TrimPrefix(line, "# "))
		}
	}
	var c Config
	if err := yaml.Unmarshal([]byte("video:\n"+strings.Join(lines, "\n")), &c); err != nil {
		t.Fatal(err)
	}
	if c.Video.Enabled || c.Video.Store != "redis" {
		t.Fatal(c.Video)
	}
}
