import fs from "node:fs";
import path from "node:path";
import {
  collectSourceIcons,
  collectFrontendIcons,
} from "./collect-offline-icons.mjs";
import { backendIconConsumers } from "./backend-icon-consumers.mjs";

const consumerPrefix = "virtual:offline-icons/consumer/";
const iconPrefix = "virtual:offline-icons/icon/";
const internal = (id) => `\0${id}`;
const js = JSON.stringify;

export function iconsForConsumer(root, file, text) {
  const found = collectSourceIcons(root, path.join(root, file), text);
  if (found.unresolved.length)
    throw new Error(`Unresolved icons in ${file}: ${js(found.unresolved)}`);
  return [
    ...new Set([
      ...found.names,
      ...Object.entries(backendIconConsumers)
        .filter(([, consumers]) => consumers.includes(file))
        .map(([name]) => name),
    ]),
  ].sort();
}

export function staticClosure(id, moduleInfo) {
  const seen = new Set();
  function visit(next) {
    if (seen.has(next)) return;
    seen.add(next);
    for (const dependency of moduleInfo(next)?.importedIds || [])
      visit(dependency);
  }
  visit(id);
  return seen;
}

/**
 * Ownership follows Vite's resolved module graph in each mode, not a guessed
 * route list. Each real consumer imports a virtual registration module, whose
 * static imports share one module per icon. Registration is synchronous and
 * cannot race React's first render. Rollup retains existing lazy boundaries.
 */
export function offlineIconPlugin() {
  let root, registry, mode, command, registryFile, countriesFile;
  const owners = new Map();
  const readRegistry = () => {
    registry = JSON.parse(fs.readFileSync(registryFile, "utf8"));
  };
  function namesFor(file) {
    if (
      !file.startsWith("src/") ||
      file.split("/").includes("..") ||
      !/\.[jt]sx?$/.test(file)
    ) {
      throw new Error(`Invalid offline icon consumer: ${file}`);
    }
    if (!owners.has(file)) {
      owners.set(
        file,
        iconsForConsumer(
          root,
          file,
          fs.readFileSync(path.join(root, file), "utf8"),
        ),
      );
    }
    return owners.get(file);
  }
  const chunksOf = (bundle) =>
    Object.values(bundle)
      .filter((chunk) => chunk.type === "chunk")
      .map((chunk) => ({
        file: chunk.fileName,
        facade:
          chunk.facadeModuleId && path.relative(root, chunk.facadeModuleId),
        entry: chunk.isEntry,
        dynamicEntry: chunk.isDynamicEntry,
        imports: chunk.imports,
        dynamicImports: chunk.dynamicImports,
        icons: Object.keys(chunk.modules)
          .filter((id) => id.startsWith(internal(iconPrefix)))
          .map((id) => id.slice(internal(iconPrefix).length)),
        consumers: Object.keys(chunk.modules)
          .filter((id) => owners.get(path.relative(root, id))?.length)
          .map((id) => path.relative(root, id)),
      }));
  return {
    name: "offline-icon-ownership",
    enforce: "pre",
    configResolved(config) {
      root = config.root;
      mode = config.mode;
      command = config.command;
      registryFile = path.join(
        root,
        "src/components/iconify/offline-icons.json",
      );
      countriesFile = path.join(root, "src/assets/data/countries.js");
      readRegistry();
    },
    buildStart() {
      owners.clear();
      readRegistry();
      this.addWatchFile?.(registryFile);
      this.addWatchFile?.(countriesFile);
    },
    configureServer(server) {
      server.watcher.add([registryFile, countriesFile]);
    },
    resolveId(id) {
      // Object-form manual chunks re-resolve their entry IDs during output.
      const virtualId = id.startsWith("\0") ? id.slice(1) : id;
      if (
        virtualId.startsWith(consumerPrefix) ||
        virtualId.startsWith(iconPrefix)
      ) {
        return { id: internal(virtualId), moduleSideEffects: true };
      }
      return null;
    },
    load(id) {
      if (id.startsWith(internal(consumerPrefix))) {
        const file = decodeURIComponent(
          id.slice(internal(consumerPrefix).length),
        );
        return namesFor(file)
          .map((name) => `import ${js(iconPrefix + name)};`)
          .join("\n");
      }
      if (id.startsWith(internal(iconPrefix))) {
        const name = id.slice(internal(iconPrefix).length);
        if (!Object.hasOwn(registry, name))
          this.error(`Offline icon data missing: ${name}`);
        return `import { registerOfflineIcon } from ${js(path.join(root, "src/components/iconify/registry.js"))};\nregisterOfflineIcon(${js(name)}, ${js(registry[name])});`;
      }
      return null;
    },
    transform(code, id) {
      const file = path.relative(root, id).split(path.sep).join("/");
      if (
        !file.startsWith("src/") ||
        !/\.[jt]sx?$/.test(file) ||
        /(?:__tests__|\/tests\/|\.(?:test|spec)\.)/.test(file)
      )
        return null;
      const names = iconsForConsumer(root, file, code);
      owners.set(file, names);
      if (!names.length) return null;
      for (const name of names) {
        if (!Object.hasOwn(registry, name))
          this.error(`Offline icon data missing: ${name} (${file})`);
      }
      return {
        code: `import ${js(consumerPrefix + encodeURIComponent(file))};\n${code}`,
        map: null,
      };
    },
    handleHotUpdate(ctx) {
      if ([registryFile, countriesFile].includes(ctx.file)) {
        // These inputs are read by the collector, not imported by the app. A
        // regenerated glyph/flag subset must not leave stale virtual modules.
        readRegistry();
        owners.clear();
        ctx.server.moduleGraph.invalidateAll();
        ctx.server.ws.send({ type: "full-reload" });
        return [];
      }
      const file = path.relative(root, ctx.file).split(path.sep).join("/");
      owners.delete(file);
      const registration = ctx.server.moduleGraph.getModuleById(
        internal(consumerPrefix + encodeURIComponent(file)),
      );
      // Consumers can gain an icon during HMR: invalidate their cached import list.
      if (registration) {
        ctx.server.moduleGraph.invalidateModule(registration);
        return [...ctx.modules, registration];
      }
    },
    outputOptions(options) {
      // Rollup can merge a side-effect-only glyph shared by lazy consumers into
      // one consumer's application chunk. The other consumer then downloads all
      // of that chunk's dependencies just to register the glyph. Give shared
      // registrations explicit boundaries; single-owner glyphs stay colocated.
      const shared = [...this.getModuleIds()].filter(
        (id) =>
          id.startsWith(internal(iconPrefix)) &&
          this.getModuleInfo(id).importers.length > 1,
      );
      if (!shared.length) return null;
      const assignments = Object.fromEntries([
        // Claim the runtime separately so the first manual glyph chunk cannot
        // absorb it and make its glyph eager for every consumer.
        [
          "offline-icon-runtime",
          [path.join(root, "src/components/iconify/registry.js")],
        ],
        ...shared
          .sort()
          .map((id) => [
            `offline-icon-${id.slice(internal(iconPrefix).length).replace(":", "_")}`,
            [id],
          ]),
      ]);
      if (typeof options.manualChunks === "function") {
        const original = options.manualChunks;
        const names = new Map(
          Object.entries(assignments).map(([name, [id]]) => [id, name]),
        );
        return {
          ...options,
          manualChunks: (id, context) => names.get(id) ?? original(id, context),
        };
      }
      return {
        ...options,
        manualChunks: { ...options.manualChunks, ...assignments },
      };
    },
    generateBundle(_options, bundle) {
      const inventory = collectFrontendIcons(root);
      inventory.names = [
        ...new Set([...inventory.names, ...Object.keys(backendIconConsumers)]),
      ].sort();
      if (inventory.unresolved.length)
        this.error("Unresolved source icon templates");
      const modules = new Set(
        Object.values(bundle).flatMap((chunk) =>
          chunk.type === "chunk" ? Object.keys(chunk.modules) : [],
        ),
      );
      const emitted = [...owners].filter(
        ([file, names]) => names.length && modules.has(path.join(root, file)),
      );
      const consumers = emitted.map(([file, names]) => {
        const closure = staticClosure(path.join(root, file), (id) =>
          this.getModuleInfo(id),
        );
        const missing = names.filter(
          (name) => !closure.has(internal(iconPrefix + name)),
        );
        if (missing.length)
          this.error(
            `Registration absent from static closure: ${file}: ${missing}`,
          );
        return { file, names, missing };
      });
      const chunks = chunksOf(bundle);
      const emittedNames = new Set(chunks.flatMap((chunk) => chunk.icons));
      this.emitFile({
        type: "asset",
        fileName: "offline-icon-coverage.json",
        source: js({
          mode,
          command,
          backendIconConsumers,
          consumers,
          chunks,
          // Not deletion candidates: stories/prototype/dev paths retain their data.
          sourceOnlyNames: inventory.names.filter(
            (name) => !emittedNames.has(name),
          ),
          missingData: inventory.names.filter(
            (name) => !Object.hasOwn(registry, name),
          ),
        }),
      });
      if (inventory.names.some((name) => !Object.hasOwn(registry, name)))
        this.error("Source icon data missing");
    },
    writeBundle(options, bundle) {
      if (!options.dir) return;
      // Vite rewrites imports/hashes after generateBundle. Publish final edges,
      // not stale names that cannot be matched to the files served to browsers.
      const asset = bundle["offline-icon-coverage.json"];
      const coverage = JSON.parse(asset.source);
      coverage.chunks = chunksOf(bundle);
      asset.source = js(coverage);
      fs.writeFileSync(path.join(options.dir, asset.fileName), asset.source);
    },
  };
}
