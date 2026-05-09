package rbac

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/rbac"
)

func testStore() *rbac.Store {
	return rbac.NewStore(config.RBACConfig{
		Enabled:     true,
		DefaultRole: "member",
		Roles: map[string]config.RBACRoleConfig{
			"admin": {Permissions: []string{"*"}},
			"member": {Permissions: []string{
				"models:gpt-4o", "models:gpt-3.5-turbo",
				"providers:openai",
			}},
			"viewer": {Permissions: []string{
				"models:gpt-3.5-turbo", "providers:openai",
			}},
		},
		Teams: map[string]config.RBACTeamConfig{
			"eng": {
				Role:   "member",
				Models: []string{"gpt-4o"},
				Members: map[string]config.RBACMemberConfig{
					"alice": {Role: "admin"},
				},
			},
		},
	})
}

func makeRC(model, provider string, metadata map[string]string) *models.RequestContext {
	rc := &models.RequestContext{
		Model:    model,
		Provider: provider,
		Metadata: make(map[string]string),
	}
	for k, v := range metadata {
		rc.Metadata[k] = v
	}
	return rc
}

func TestPlugin_Disabled(t *testing.T) {
	p := New(testStore(), false)
	rc := makeRC("gpt-4o", "openai", nil)
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("disabled plugin should continue")
	}
}

func TestPlugin_NilStore(t *testing.T) {
	p := New(nil, true)
	rc := makeRC("gpt-4o", "openai", nil)
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("nil store should continue")
	}
}

func TestPlugin_MemberAllowedModel(t *testing.T) {
	p := New(testStore(), true)
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner": "bob",
		"key_team":       "eng",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("member should access gpt-4o: %s", result.Error.Message)
	}
	if rc.Metadata["rbac_role"] != "member" {
		t.Errorf("role = %q", rc.Metadata["rbac_role"])
	}
}

func TestPlugin_MemberDeniedModel(t *testing.T) {
	p := New(testStore(), true)
	rc := makeRC("llama-3", "openai", map[string]string{
		"auth_key_owner": "bob",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("member should be denied llama-3")
	}
	if result.Error.Status != 403 {
		t.Errorf("status = %d", result.Error.Status)
	}
}

func TestPlugin_MemberDeniedProvider(t *testing.T) {
	p := New(testStore(), true)
	rc := makeRC("gpt-4o", "anthropic", map[string]string{
		"auth_key_owner": "bob",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("member should be denied anthropic provider")
	}
}

func TestPlugin_AdminAllowsAll(t *testing.T) {
	p := New(testStore(), true)
	rc := makeRC("llama-3", "custom", map[string]string{
		"auth_key_owner": "alice",
		"key_team":       "eng",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("admin should access anything: %s", result.Error.Message)
	}
}

func TestPlugin_TeamModelRestriction(t *testing.T) {
	p := New(testStore(), true)
	// Member has permission for gpt-3.5-turbo, but eng team restricts to gpt-4o only.
	rc := makeRC("gpt-3.5-turbo", "openai", map[string]string{
		"auth_key_owner": "bob",
		"key_team":       "eng",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("eng team should restrict to gpt-4o only")
	}
}

func TestPlugin_GuardrailOverrideDenied(t *testing.T) {
	p := New(testStore(), true)
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner":     "bob",
		"x-guardrail-policy": "log-only",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("member should be denied guardrail override")
	}
}

func TestPlugin_GuardrailOverrideAllowedForAdmin(t *testing.T) {
	p := New(testStore(), true)
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner":     "alice",
		"key_team":           "eng",
		"x-guardrail-policy": "log-only",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("admin should be allowed guardrail override: %s", result.Error.Message)
	}
}

func TestPlugin_KeyRoleOverride(t *testing.T) {
	p := New(testStore(), true)
	// No team, but key has role=viewer.
	rc := makeRC("gpt-3.5-turbo", "openai", map[string]string{
		"key_role": "viewer",
	})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("viewer should access gpt-3.5-turbo: %s", result.Error.Message)
	}
	if rc.Metadata["rbac_role"] != "viewer" {
		t.Errorf("role = %q", rc.Metadata["rbac_role"])
	}
}

func TestPlugin_DefaultRole(t *testing.T) {
	p := New(testStore(), true)
	rc := makeRC("gpt-4o", "openai", map[string]string{})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("default member should access gpt-4o: %s", result.Error.Message)
	}
	if rc.Metadata["rbac_role"] != "member" {
		t.Errorf("role = %q", rc.Metadata["rbac_role"])
	}
}

func TestPlugin_EmptyModelAndProvider(t *testing.T) {
	p := New(testStore(), true)
	rc := makeRC("", "", map[string]string{})
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatal("empty model/provider should pass")
	}
}

func TestPlugin_ProcessResponse(t *testing.T) {
	p := New(testStore(), true)
	rc := makeRC("gpt-4o", "openai", nil)
	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("ProcessResponse should always continue")
	}
}

func TestPlugin_NameAndPriority(t *testing.T) {
	p := New(testStore(), true)
	if p.Name() != "rbac" {
		t.Errorf("name = %q", p.Name())
	}
	if p.Priority() != 30 {
		t.Errorf("priority = %d", p.Priority())
	}
}
