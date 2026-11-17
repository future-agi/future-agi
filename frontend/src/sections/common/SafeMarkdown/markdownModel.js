// W5-8 (EV-21): the pure rules of SafeMarkdown.jsx.

/** Every link a reason or comment renders carries this rel. */
export const LINK_REL = "noopener noreferrer nofollow";

/** remark plugin: HTML in the source becomes text (never markup). */
export function remarkHtmlAsText() {
  const visit = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.type === "html") {
      node.type = "text";
    }
    if (Array.isArray(node.children)) node.children.forEach(visit);
  };
  return (tree) => visit(tree);
}
