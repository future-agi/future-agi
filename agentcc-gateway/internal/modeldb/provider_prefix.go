package modeldb

// providerDBPrefixes maps a provider id the console's Add Provider presets
// give an org's provider to the prefix the bundled model database files that
// provider's models under, where the two differ: Together's models are
// "together_ai/<model>", Fireworks' "fireworks_ai/<model>", Vertex AI's
// "vertex_ai/<model>" and Gemini's "gemini/<model>".
var providerDBPrefixes = map[string]string{
	"together":  "together_ai",
	"fireworks": "fireworks_ai",
	"vertex":    "vertex_ai",
	"google":    "gemini",
	"x-ai":      "xai",
}

// DBPrefix is the prefix the model database files providerID's own models
// under: providerID itself unless a preset id maps to another ("together" is
// filed as "together_ai").
func DBPrefix(providerID string) string {
	if p, ok := providerDBPrefixes[providerID]; ok {
		return p
	}
	return providerID
}

// ForProvider returns model's entry as providerID serves it: the provider's
// own "<provider>/<model>" entry, then the entry under the provider's model
// database prefix when its id is a preset that maps to another
// ("together_ai/<model>" for "together"), then the model's general entry
// (Get).
func (db *ModelDB) ForProvider(providerID, model string) (*ModelInfo, bool) {
	if db == nil {
		return nil, false
	}
	if providerID != "" {
		if info, ok := db.Get(providerID + "/" + model); ok {
			return info, true
		}
		if prefix := DBPrefix(providerID); prefix != providerID {
			if info, ok := db.models[prefix+"/"+model]; ok {
				return info, true
			}
		}
	}
	return db.Get(model)
}
