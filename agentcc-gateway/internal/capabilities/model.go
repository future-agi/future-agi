package capabilities

import (
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

// knownModels are the models of one provider the gateway can describe.
type knownModels struct {
	// ids are the provider's own model ids, which managed_allowed prices.
	ids []string
	// infos describe the models the features are narrowed by.
	infos  []*modeldb.ModelInfo
	source string
}

// knownModelsOf is p's catalog snapshot when it has one (source catalog),
// else the models config lists for it as the model database describes them
// (source config), each as the caller's org sees it (orgView). Only catalog
// models the gateway serves at now count: a blocked model, or one past its
// expiration date, neither widens the features nor makes managed traffic
// allowed; a degraded one does.
func (in Inputs) knownModelsOf(p Provider) knownModels {
	if snap := snapshotOf(in.Catalog, p.ID); snap != nil && len(snap.Models) > 0 {
		k := knownModels{source: SourceCatalog}
		for _, m := range snap.Sorted() {
			if !m.Servable(in.Now) {
				continue
			}
			k.ids = append(k.ids, m.ID)
			k.infos = append(k.infos, in.orgView(p.ID, m.ID, m.ModelInfo(p.ID+"/"+m.ID, p.ID)))
		}
		return k
	}
	k := knownModels{ids: p.Models, source: SourceConfig}
	for _, id := range p.Models {
		if info := in.orgView(p.ID, id, modelInfo(in.ModelDB, p.ID, id)); info != nil {
			k.infos = append(k.infos, info)
		}
	}
	return k
}

// orgView is base as the caller's org sees model (Inputs.OrgView), or base.
func (in Inputs) orgView(providerID, model string, base *modeldb.ModelInfo) *modeldb.ModelInfo {
	if in.OrgView == nil {
		return base
	}
	return in.OrgView(providerID, model, base)
}

func snapshotOf(cat *catalog.Catalog, providerID string) *catalog.Snapshot {
	for _, s := range cat.Snapshots() {
		if s.ProviderID == providerID {
			return s
		}
	}
	return nil
}

// modelInfo is the model database's entry for model, filed under the
// provider first (modeldb.ForProvider: then under the provider's model
// database prefix, "together_ai/" for "together", then the general entry).
func modelInfo(db *modeldb.ModelDB, providerID, model string) *modeldb.ModelInfo {
	if info, ok := db.ForProvider(providerID, model); ok {
		return info
	}
	return nil
}

// ModelView is GET /v1/capabilities?model=<id>: what one model can do through
// each provider that serves it.
type ModelView struct {
	GeneratedAt time.Time            `json:"generated_at"`
	Model       string               `json:"model"`
	Providers   map[string]ModelCell `json:"providers"`
}

// ModelCell is one model through one provider.
type ModelCell struct {
	CredentialType string `json:"credential_type"`
	// Source is catalog when the managed catalog lists the model, else config.
	Source string `json:"source"`
	// Flags are the model's features that the provider's chat adapter carries.
	Flags           Features                 `json:"flags"`
	ContextLength   int                      `json:"context_length"`
	MaxOutputTokens int                      `json:"max_output_tokens"`
	Modalities      Modalities               `json:"modalities"`
	Endpoints       map[string]ModelEndpoint `json:"endpoints"`
}

// Modalities are what a model takes in and puts out.
type Modalities struct {
	Input  []string `json:"input"`
	Output []string `json:"output"`
}

// ModelEndpoint is one launch endpoint for one model through one provider.
type ModelEndpoint struct {
	Status         string `json:"status"`
	ManagedAllowed bool   `json:"managed_allowed"`
}

// BuildModel describes model through every provider in in that serves it: a
// managed catalog that lists it (as "<model>" or "<provider>/<model>"), or a
// config that names it. Providers is empty when none does.
func BuildModel(in Inputs, model string) *ModelView {
	v := &ModelView{GeneratedAt: in.Now.UTC(), Model: model, Providers: map[string]ModelCell{}}
	for _, p := range in.Providers {
		if c, ok := in.modelCell(p, model); ok {
			v.Providers[p.ID] = c
		}
	}
	return v
}

func (in Inputs) modelCell(p Provider, model string) (ModelCell, bool) {
	local := strings.TrimPrefix(model, p.ID+"/")
	cell := ModelCell{CredentialType: p.CredentialType, Endpoints: map[string]ModelEndpoint{}}
	var info *modeldb.ModelInfo
	servable := true
	if m, ok := catalogModel(in.Catalog, p.ID, model, local); ok {
		local, servable = m.ID, m.Servable(in.Now)
		info = m.ModelInfo(p.ID+"/"+m.ID, p.ID)
		cell.Source, cell.ContextLength, cell.MaxOutputTokens = SourceCatalog, m.ContextLength, m.MaxOutputTokens
		if viewed := in.orgView(p.ID, m.ID, info); viewed != info {
			// The org's own limits, where it sets them, replace the catalog's.
			if viewed.MaxInputTokens != info.MaxInputTokens {
				cell.ContextLength = viewed.MaxInputTokens
			}
			if viewed.MaxOutputTokens != info.MaxOutputTokens {
				cell.MaxOutputTokens = viewed.MaxOutputTokens
			}
			info = viewed
		}
		cell.Modalities = Modalities{Input: m.InputModalities, Output: m.OutputModalities}
		if len(cell.Modalities.Output) == 0 {
			cell.Modalities.Output = []string{"text"}
		}
	} else if contains(p.Models, model) || contains(p.Models, local) {
		if !contains(p.Models, local) {
			local = model
		}
		cell.Source = SourceConfig
		if info = in.orgView(p.ID, local, modelInfo(in.ModelDB, p.ID, local)); info != nil {
			cell.ContextLength, cell.MaxOutputTokens = info.MaxInputTokens, info.MaxOutputTokens
			cell.Modalities = modalitiesOf(info)
		}
	} else {
		return ModelCell{}, false
	}

	cell.Flags = Features{Stream: adapterChatFeatures(p.APIFormat).Stream}
	known := knownModels{ids: []string{local}, source: cell.Source}
	if !servable {
		known.ids = nil // described, but not managed-allowed
	}
	if info != nil {
		cell.Flags = adapterChatFeatures(p.APIFormat).and(modelFeatures(info))
		known.infos = []*modeldb.ModelInfo{info}
	}
	for _, spec := range endpointSpecs {
		if spec.tier != TierLaunch {
			continue
		}
		c := in.cell(spec, p, known)
		cell.Endpoints[spec.id] = ModelEndpoint{Status: c.Status, ManagedAllowed: c.ManagedAllowed}
	}
	return cell, true
}

// catalogModel finds model in providerID's snapshot, as given or without the
// provider's prefix.
func catalogModel(cat *catalog.Catalog, providerID, model, local string) (*catalog.Model, bool) {
	if m, ok := cat.Get(providerID, model); ok {
		return m, true
	}
	return cat.Get(providerID, local)
}

// modalitiesOf reads a model database entry's modalities from its flags and mode.
func modalitiesOf(info *modeldb.ModelInfo) Modalities {
	in := []string{"text"}
	if info.Capabilities.Vision {
		in = append(in, "image")
	}
	if info.Capabilities.AudioInput {
		in = append(in, "audio")
	}
	if info.Capabilities.PDFInput {
		in = append(in, "file")
	}
	out := []string{"text"}
	switch info.Mode {
	case modeldb.ModeEmbedding:
		out = []string{"embeddings"}
	case modeldb.ModeImageGeneration:
		out = []string{"image"}
	case modeldb.ModeAudioSpeech:
		out = []string{"audio"}
	default:
		if info.Capabilities.AudioOutput {
			out = append(out, "audio")
		}
	}
	return Modalities{Input: in, Output: out}
}
