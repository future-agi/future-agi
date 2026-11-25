package modeldb

import (
	_ "embed"
	"log"
)

//go:embed litellm.json
var liteLLMData []byte

var BundledModels map[string]*ModelInfo
var bundledZero = make(priceDeclarations)

func init() {
	var err error
	BundledModels, err = loadLiteLLM(liteLLMData, false, bundledZero)
	if err != nil {
		log.Fatalf("failed to parse embedded litellm pricing data: %v", err)
	}
	addDecisionModels(BundledModels)
}
