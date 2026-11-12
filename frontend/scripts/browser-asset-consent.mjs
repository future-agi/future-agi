// CLOUD ONLY: exercise the exact emitted components, never rewritten copies.
import fs from "node:fs";
import path from "node:path";
import { parse } from "acorn";

export function consentBindings(dist) {
  const assets = path.join(dist, "assets");
  const files = fs.readdirSync(assets);
  const one = (prefix) => {
    const matches = files.filter(
      (f) => f.startsWith(prefix) && f.endsWith(".js"),
    );
    if (matches.length !== 1)
      throw new Error(`Expected one emitted ${prefix} chunk`);
    return matches[0];
  };
  const preview = fs.readFileSync(
    path.join(assets, one("PdfPreviewDrawer-")),
    "utf8",
  );
  const hook = /\{openPdfPreviewDrawer:\w+,closePreview:\w+\}=([\w$]+)\(/.exec(
    preview,
  )?.[1];
  if (!hook)
    throw new Error(
      "Production preview-store call changed; review its binding",
    );
  const ast = parse(preview, { ecmaVersion: "latest", sourceType: "module" });
  const declaration = ast.body.find(
    (n) =>
      n.type === "ImportDeclaration" &&
      n.specifiers.some((s) => s.local.name === hook),
  );
  const specifier = declaration?.specifiers.find((s) => s.local.name === hook);
  if (specifier?.type !== "ImportSpecifier")
    throw new Error("Preview-store import not identified");
  const consentName = one("ExternalContentFrame-");
  const consentAst = parse(
    fs.readFileSync(path.join(assets, consentName), "utf8"),
    { ecmaVersion: "latest", sourceType: "module" },
  );
  const exports = consentAst.body
    .filter((n) => n.type === "ExportNamedDeclaration")
    .flatMap((n) => n.specifiers);
  if (exports.length !== 1) throw new Error("Consent component export changed");
  return {
    mui: `/assets/${one("mui-")}`,
    consent: `/assets/${consentName}`,
    consentExport: exports[0].exported.name,
    storeModule: `/assets/${path.basename(declaration.source.value)}`,
    storeExport: specifier.imported.name,
  };
}

export async function openOfficePreview(page, bindings) {
  await page.evaluate(async (b) => {
    const mui = await import(b.mui);
    const react = Object.values(mui).find(
      (v) => v?.createElement && v?.useEffect,
    );
    const dom = Object.values(mui).find((v) => v?.createRoot);
    const usePreview = (await import(b.storeModule))[b.storeExport];
    if (!react || !dom || typeof usePreview !== "function")
      throw new Error("Production React/preview exports missing");
    function OpenPreview() {
      const open = usePreview((s) => s.setOpenPreviewPdfDrawer);
      react.useEffect(() => {
        open({
          url: "http://127.0.0.1:5188/fixture.docx",
          type: "docx",
          name: "Office consent fixture.docx",
        });
      }, [open]);
      return null;
    }
    const host = document.createElement("div");
    document.body.append(host);
    dom.createRoot(host).render(react.createElement(OpenPreview));
  }, bindings);
}

export async function mountYoutubeConsent(page, bindings) {
  await page.evaluate(async (b) => {
    const mui = await import(b.mui);
    const react = Object.values(mui).find(
      (v) => v?.createElement && v?.useEffect,
    );
    const dom = Object.values(mui).find((v) => v?.createRoot);
    const component = (await import(b.consent))[b.consentExport];
    if (!react || !dom || typeof component !== "function")
      throw new Error("Production consent/React exports missing");
    const host = document.createElement("div");
    host.style.cssText =
      "position:fixed;inset:100px 200px;background:white;z-index:2000";
    document.body.append(host);
    dom.createRoot(host).render(
      react.createElement(component, {
        src: "https://www.youtube.com/embed/6PLdnbMeC7k?si=IGY_DSHjkBrnMpZX",
        title: "YouTube consent fixture",
        width: "100%",
        height: "100%",
      }),
    );
  }, bindings);
}
