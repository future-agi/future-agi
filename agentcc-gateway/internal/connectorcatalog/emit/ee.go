package emit

import (
	"errors"
	"fmt"
	"slices"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// Schema identifiers of the EE outputs.
const (
	EEAppsSchema     = "connector-ee-apps/v1"
	EESettingsSchema = "connector-ee-settings/v1"
)

// EEMaxSettingsApps is EE's cap on its settings file (EE:apps.ts:184 at
// 49321478e4): EE refuses to boot past it.
const EEMaxSettingsApps = 64

// eeApps writes every app EE can use as EE's IntegrationAppDefinition
// (EE:packages/server/src/workspace/integrations/types.ts:36-48), keys in
// its order, source "built-in": the table OM5 generates EE's built-in apps
// from and checks against (§2.7). A rest app has no mcpUrl: the operator
// supplies <envPrefix>_MCP_URL, as for EE's Google apps today.
func eeApps(cat *manifest.Catalog, meta Meta) (Files, error) {
	apps := []any{}
	var errs []error
	for _, a := range cat.Apps {
		ee, ok := manifest.EEAppFor(a)
		if !ok {
			continue
		}
		def := eeDefinition(ee)
		def = append(def, canonjson.Member{Key: "source", Value: "built-in"})
		if err := checkEEDefinition(def); err != nil {
			errs = append(errs, fmt.Errorf("app %s: %w", a.ID, err))
			continue
		}
		apps = append(apps, def)
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	doc := append(envelope(EEAppsSchema, meta), canonjson.Member{Key: "apps", Value: apps})
	data, err := jsonFile(doc)
	if err != nil {
		return nil, err
	}
	return Files{"ee-apps.json": data}, nil
}

// eeSettings writes the settings file an operator points
// OMEGA_INTEGRATIONS_APPS_FILE at (§2.7, the interim path): every app EE
// takes through a vendor MCP server with a fixed URL, since EE requires an
// mcpUrl in that file (EE:apps.ts:218-219). Each app still needs its client
// id and secret in EE's env. It holds at most 64 apps, and the file's bytes
// are read back with EE's own rules before they are returned.
func eeSettings(cat *manifest.Catalog, meta Meta) (Files, error) {
	apps := []any{}
	for _, a := range cat.Apps {
		p, ok := manifest.EligibleForEE(a)
		if !ok || p.Kind != manifest.KindVendorMCP {
			continue
		}
		ee, _ := manifest.EEAppFor(a)
		if ee.MCPURL == "" {
			continue // a URL with variables: EE's file needs a fixed one
		}
		apps = append(apps, eeDefinition(ee))
	}
	if len(apps) > EEMaxSettingsApps {
		return nil, fmt.Errorf("%d apps qualify for EE's settings file, which holds at most %d (EE:apps.ts:184): set x-omega.ee: exclude on the rest", len(apps), EEMaxSettingsApps)
	}
	doc := append(envelope(EESettingsSchema, meta), canonjson.Member{Key: "apps", Value: apps})
	data, err := jsonFile(doc)
	if err != nil {
		return nil, err
	}
	if err := CheckEESettingsFile(data); err != nil {
		return nil, err
	}
	return Files{"ee-settings.json": data}, nil
}

// eeDefinition renders an EE app definition, keys in EE's order, without
// source (EE sets it for a settings app).
func eeDefinition(ee manifest.EEApp) canonjson.Object {
	oauth := canonjson.Object{
		{Key: "authorizeUrl", Value: ee.OAuth.AuthorizeURL},
		{Key: "tokenUrl", Value: ee.OAuth.TokenURL},
		{Key: "scopes", Value: ee.OAuth.Scopes},
		{Key: "pkce", Value: ee.OAuth.PKCE},
		{Key: "tokenBody", Value: ee.OAuth.TokenBody},
		{Key: "clientAuth", Value: ee.OAuth.ClientAuth},
	}
	if params := ee.OAuth.ExtraAuthorizeParams; len(params) > 0 {
		names := make([]string, 0, len(params))
		for k := range params {
			names = append(names, k)
		}
		slices.Sort(names)
		extra := canonjson.Object{}
		for _, k := range names {
			extra = append(extra, canonjson.Member{Key: k, Value: params[k]})
		}
		oauth = append(oauth, canonjson.Member{Key: "extraAuthorizeParams", Value: extra})
	}
	def := canonjson.Object{
		{Key: "id", Value: ee.ID},
		{Key: "name", Value: ee.Name},
		{Key: "description", Value: ee.Description},
		{Key: "category", Value: ee.Category},
		{Key: "group", Value: ee.Group},
		{Key: "oauth", Value: oauth},
	}
	if ee.MCPURL != "" {
		def = append(def, canonjson.Member{Key: "mcpUrl", Value: ee.MCPURL})
	}
	return append(def, canonjson.Member{Key: "envPrefix", Value: ee.EnvPrefix})
}
