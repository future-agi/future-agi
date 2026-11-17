package reqopts

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// gatewayWire is Vercel's providerOptions.gateway object.
type gatewayWire struct {
	Order             []string `json:"order"`
	Only              []string `json:"only"`
	Sort              *string  `json:"sort"`
	Models            []string `json:"models"`
	Tags              []string `json:"tags"`
	User              *string  `json:"user"`
	SessionID         *string  `json:"sessionId"`
	ZeroDataRetention *bool    `json:"zeroDataRetention"`
}

// parseProviderOptions reads Vercel's providerOptions. Only its gateway
// namespace is the gateway's; the provider-specific namespaces (openai,
// anthropic, ...) are removed with it and never forwarded, as for every
// routing field on an upstream that is not OpenRouter.
func parseProviderOptions(raw json.RawMessage) (*models.GatewayOptions, *models.APIError) {
	const param = keyProviderOptions
	var ns map[string]json.RawMessage
	if !isJSONObject(raw) || json.Unmarshal(raw, &ns) != nil {
		return nil, fieldError(CodeInvalidProviderPreference, param, "providerOptions must be a JSON object")
	}
	g, ok := ns["gateway"]
	if !ok || isNull(g) {
		return nil, nil
	}
	return parseGatewayOptions(g)
}

func parseGatewayOptions(raw json.RawMessage) (*models.GatewayOptions, *models.APIError) {
	const param = keyProviderOptions + ".gateway"
	keys, e := objectKeys(raw, param, CodeInvalidProviderPreference)
	if e != nil {
		return nil, e
	}
	for _, k := range keys {
		if _, known := gatewayFieldByKey[k]; !known {
			return nil, fieldError(CodeInvalidProviderPreference, param+"."+k, param+"."+k+" is not a gateway option")
		}
	}
	var w gatewayWire
	if err := decodeStrict(raw, &w); err != nil {
		return nil, decodeError(param, err)
	}
	g := &models.GatewayOptions{Tags: w.Tags, ZeroDataRetention: w.ZeroDataRetention}
	if g.Order, e = slugList(w.Order, param+".order"); e != nil {
		return nil, e
	}
	if g.Only, e = slugList(w.Only, param+".only"); e != nil {
		return nil, e
	}
	if w.Sort != nil {
		c, ok := CanonicalSort(*w.Sort)
		if !ok {
			return nil, fieldError(CodeInvalidProviderPreference, param+".sort", param+".sort must be cost, ttft or tps")
		}
		g.Sort = c
	}
	if w.Models != nil {
		if g.Models, e = modelList(w.Models, param+".models"); e != nil {
			return nil, e
		}
	}
	if w.User != nil {
		g.User = *w.User
	}
	if w.SessionID != nil {
		g.SessionID = *w.SessionID
	}
	return g, nil
}

// gatewayFieldByKey maps each providerOptions.gateway key to its
// support-registry name.
var gatewayFieldByKey = map[string]string{
	"order": FieldGatewayOrder, "only": FieldGatewayOnly, "sort": FieldGatewaySort,
	"models": FieldGatewayModels, "tags": FieldGatewayTags, "user": FieldGatewayUser,
	"sessionId": FieldGatewaySessionID, "zeroDataRetention": FieldGatewayZeroDataRetention,
}

// gatewayFields are the support-registry names of the gateway options the
// caller set.
func gatewayFields(g *models.GatewayOptions) []string {
	if g == nil {
		return nil
	}
	var out []string
	add := func(set bool, f string) {
		if set {
			out = append(out, f)
		}
	}
	add(g.Order != nil, FieldGatewayOrder)
	add(g.Only != nil, FieldGatewayOnly)
	add(g.Sort != "", FieldGatewaySort)
	add(g.Models != nil, FieldGatewayModels)
	add(g.Tags != nil, FieldGatewayTags)
	add(g.User != "", FieldGatewayUser)
	add(g.SessionID != "", FieldGatewaySessionID)
	add(g.ZeroDataRetention != nil, FieldGatewayZeroDataRetention)
	return out
}
