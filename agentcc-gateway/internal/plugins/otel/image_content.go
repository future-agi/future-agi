package otel

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// contentMode mirrors request logging: prefer step 5's resolved policy, and
// resolve the same sources for endpoints without that step. This observer must
// not write rc.Privacy: post-response plugins read the context concurrently.
func (p *Plugin) contentMode(rc *models.RequestContext) models.ContentMode {
	if rc.Privacy.ContentMode != "" {
		return rc.Privacy.ContentMode
	}
	src := privacy.ContentModeSources{
		Key:     rc.Metadata[privacy.KeyLogContentRequestMetadata],
		Request: privacy.RequestContentMode(rc.RequestHeaders),
		Default: privacy.DefaultContentMode(),
	}
	if p.tenantStore != nil {
		orgID := rc.Metadata["org_id"]
		if orgID == "" {
			orgID = rc.Metadata[tenant.MetadataKeyOrgID]
		}
		if orgID != "" {
			src.Org = p.tenantStore.Get(orgID).ContentMode()
		}
	}
	return privacy.ResolveContentMode(src).ContentMode
}

// imageBodyJSON uses the ordinary body builder, but replaces generated-image
// references before that builder drops base64 payloads to byte counts. A none
// policy must not retain even those counts. Revised prompts stay unchanged
// unless privacy masking suppresses the whole non-chat body.
func imageBodyJSON(rc *models.RequestContext, mode models.ContentMode, r *privacy.Redactor) (string, string) {
	if mode == models.ContentModeFull || rc.EndpointType != "image" || rc.ImageResponse == nil {
		return bodyJSONWithRedaction(rc, r)
	}
	response := *rc.ImageResponse
	response.Data = append([]models.ImageData(nil), response.Data...)
	for i := range response.Data {
		image := &response.Data[i]
		if image.URL != "" {
			image.URL = imageURLInfo(image.URL).placeholder(mode)
		}
		if image.B64JSON != "" {
			image.URL = imageInfo{data: image.B64JSON, base64: true}.placeholder(mode)
			image.B64JSON = ""
		}
	}
	// The body builder reads only the endpoint type, image request/response and,
	// if both payloads are absent, chat request/response. A literal carries
	// exactly those fields; copying *rc would copy its sync.Mutex (go vet copylocks).
	view := &models.RequestContext{
		EndpointType:  rc.EndpointType,
		ImageRequest:  rc.ImageRequest,
		ImageResponse: &response,
		Request:       rc.Request,
		Response:      rc.Response,
	}
	return bodyJSONWithRedaction(view, r)
}
