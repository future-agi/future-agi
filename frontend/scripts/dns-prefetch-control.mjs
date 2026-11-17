import { JSDOM } from "jsdom";

// Parse HTML rather than matching comments, script strings, or quoted examples.
// No scripts or subresources are enabled in this inert document.
export function inspectDnsPrefetchControl(html) {
  const { window } = new JSDOM(html);
  try {
    const { document } = window;
    const controls = [...document.querySelectorAll("meta[http-equiv]")].filter(
      (meta) => meta.httpEquiv.toLowerCase() === "x-dns-prefetch-control",
    );
    const control = controls[0];
    const early =
      control?.parentElement === document.head &&
      [...document.head.children]
        .slice(0, [...document.head.children].indexOf(control))
        .every((node) => !["SCRIPT", "LINK", "STYLE"].includes(node.tagName));
    return {
      count: controls.length,
      content: control?.content ?? null,
      early,
      passed:
        controls.length === 1 &&
        control.content.toLowerCase() === "off" &&
        early,
    };
  } finally {
    window.close();
  }
}
