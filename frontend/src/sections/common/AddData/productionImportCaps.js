import { launchState, surfaceShownIn } from "src/launch/launchSwitch";

export function productionImportCaps(real) {
  return {
    listFromTracesCommand:
      !real ||
      surfaceShownIn("dataset.fromProduction", {
        ...launchState(),
        launch: true,
      }),
  };
}
