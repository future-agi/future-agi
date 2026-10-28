package contractwatch

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// bedrockFoundationModel is one FoundationModelSummary of ListFoundationModels
// (GET /foundation-models on the bedrock control plane).
type bedrockFoundationModel struct {
	ModelID                    string   `json:"modelId"`
	ModelName                  string   `json:"modelName"`
	ProviderName               string   `json:"providerName"`
	InputModalities            []string `json:"inputModalities"`
	OutputModalities           []string `json:"outputModalities"`
	ResponseStreamingSupported *bool    `json:"responseStreamingSupported"`
	InferenceTypesSupported    []string `json:"inferenceTypesSupported"`
	ModelLifecycle             struct {
		Status        string `json:"status"`
		EndOfLifeTime string `json:"endOfLifeTime"`
	} `json:"modelLifecycle"`
}

// bedrockInferenceProfile is one InferenceProfileSummary of
// ListInferenceProfiles (GET /inference-profiles).
type bedrockInferenceProfile struct {
	InferenceProfileID   string `json:"inferenceProfileId"`
	InferenceProfileName string `json:"inferenceProfileName"`
	Status               string `json:"status"`
	Type                 string `json:"type"`
	CreatedAt            string `json:"createdAt"`
	Models               []struct {
		ModelArn string `json:"modelArn"`
	} `json:"models"`
}

// BedrockLister lists a region's foundation models (ListFoundationModels)
// and system-defined inference profiles (ListInferenceProfiles, paginated
// with nextToken) on the control plane, bedrock.<region>.amazonaws.com,
// signed with SigV4 for service "bedrock". Customers call profile-only
// models by profile ID, so both lists matter: the fixtures key on whichever
// ID the gateway sends.
type BedrockLister struct {
	// BaseURL defaults to https://bedrock.<Region>.amazonaws.com.
	BaseURL string
	Region  string
	Creds   awsCredentials
	Client  *http.Client
	// Now is the signing clock; nil means time.Now.
	Now func() time.Time
	// PageSize is maxResults for the profile listing; 0 means 1000.
	PageSize int
}

// List implements Lister.
func (l BedrockLister) List(ctx context.Context) ([]Model, error) {
	base := l.BaseURL
	if base == "" {
		base = "https://bedrock." + l.Region + ".amazonaws.com"
	}
	now := l.Now
	if now == nil {
		now = time.Now
	}
	sign := func(r *http.Request) { signV4(r, l.Creds, l.Region, "bedrock", now()) }
	f := fetcher{client: l.Client, secrets: []string{l.Creds.SecretAccessKey, l.Creds.SessionToken}}

	var fms struct {
		ModelSummaries []bedrockFoundationModel `json:"modelSummaries"`
	}
	if err := f.getJSON(ctx, endpoint(base, "/foundation-models", nil), nil, sign, &fms); err != nil {
		return nil, fmt.Errorf("bedrock ListFoundationModels: %w", err)
	}
	byID := map[string]bedrockFoundationModel{}
	var out []Model
	for _, m := range fms.ModelSummaries {
		byID[m.ModelID] = m
		out = append(out, m.model())
	}

	size := l.PageSize
	if size <= 0 {
		size = 1000
	}
	token := ""
	seen := map[string]bool{}
	for pages := 0; ; pages++ {
		if pages == maxPages {
			return nil, fmt.Errorf("bedrock ListInferenceProfiles: more than %d pages; giving up", maxPages)
		}
		q := url.Values{"maxResults": {fmt.Sprint(size)}, "type": {"SYSTEM_DEFINED"}}
		if token != "" {
			q.Set("nextToken", token)
		}
		var page struct {
			InferenceProfileSummaries []bedrockInferenceProfile `json:"inferenceProfileSummaries"`
			NextToken                 string                    `json:"nextToken"`
		}
		if err := f.getJSON(ctx, endpoint(base, "/inference-profiles", q), nil, sign, &page); err != nil {
			return nil, fmt.Errorf("bedrock ListInferenceProfiles: %w", err)
		}
		for _, p := range page.InferenceProfileSummaries {
			out = append(out, p.model(byID))
		}
		if page.NextToken == "" {
			return out, nil
		}
		if seen[page.NextToken] {
			return nil, fmt.Errorf("bedrock ListInferenceProfiles: nextToken repeats; giving up")
		}
		seen[page.NextToken] = true
		token = page.NextToken
	}
}

func (m bedrockFoundationModel) model() Model {
	mm := Model{
		ID:          m.ModelID,
		DisplayName: m.ModelName,
		OwnedBy:     m.ProviderName,
		Lifecycle:   m.ModelLifecycle.Status,
		Retires:     m.ModelLifecycle.EndOfLifeTime,
	}
	mm.Capabilities = m.capabilities()
	mm.AutoIgnore = m.unusable()
	if mm.AutoIgnore == "" && !contains(m.InferenceTypesSupported, "ON_DEMAND") {
		if contains(m.InferenceTypesSupported, "INFERENCE_PROFILE") {
			mm.AutoIgnore = "invocable only through an inference profile; the profile IDs are listed and tracked instead"
		} else {
			mm.AutoIgnore = fmt.Sprintf("dedicated capacity only (inferenceTypesSupported %v)", m.InferenceTypesSupported)
		}
	}
	return mm
}

// unusable is why the model cannot be a chat fixture whatever ID calls it.
func (m bedrockFoundationModel) unusable() string {
	switch {
	case m.ModelLifecycle.Status == "LEGACY":
		return "lifecycle LEGACY (deprecated)"
	case !contains(m.OutputModalities, "TEXT"):
		return fmt.Sprintf("output modalities %v: not a text model", m.OutputModalities)
	}
	return ""
}

func (m bedrockFoundationModel) capabilities() []string {
	var caps []string
	for _, x := range m.InputModalities {
		caps = append(caps, "input:"+strings.ToLower(x))
	}
	for _, x := range m.OutputModalities {
		caps = append(caps, "output:"+strings.ToLower(x))
	}
	for _, x := range m.InferenceTypesSupported {
		caps = append(caps, "inference:"+strings.ToLower(x))
	}
	if m.ResponseStreamingSupported != nil && *m.ResponseStreamingSupported {
		caps = append(caps, "streaming")
	}
	return caps
}

func (p bedrockInferenceProfile) model(fms map[string]bedrockFoundationModel) Model {
	mm := Model{ID: p.InferenceProfileID, DisplayName: p.InferenceProfileName, Lifecycle: p.Status}
	if t, err := time.Parse(time.RFC3339, p.CreatedAt); err == nil {
		t = t.UTC()
		mm.Created = &t
	}
	// A profile routes to the same model in several regions; the first one
	// this region lists carries the metadata.
	for _, ref := range p.Models {
		_, id, ok := strings.Cut(ref.ModelArn, ":foundation-model/")
		if !ok {
			continue
		}
		if fm, ok := fms[id]; ok {
			mm.OwnedBy = fm.ProviderName
			mm.Capabilities = append(fm.capabilities(), "profile-of:"+id)
			mm.AutoIgnore = fm.unusable()
			if fm.ModelLifecycle.Status != "" {
				mm.Lifecycle = fm.ModelLifecycle.Status
			}
			break
		}
	}
	if mm.AutoIgnore == "" && p.Status != "" && p.Status != "ACTIVE" {
		mm.AutoIgnore = "inference profile status " + p.Status
	}
	return mm
}
