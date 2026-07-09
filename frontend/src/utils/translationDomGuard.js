import logger from "./logger";

let applied = false;

export function applyTranslationDomGuard() {
  if (applied) return;
  if (typeof Node !== "function" || !Node.prototype) return;
  applied = true;

  const originalRemoveChild = Node.prototype.removeChild;
  Node.prototype.removeChild = function (child) {
    if (child && child.parentNode !== this) {
      logger.debug(
        "translationDomGuard: skipped removeChild on detached node",
      );
      return child;
    }
    return originalRemoveChild.apply(this, arguments);
  };

  const originalInsertBefore = Node.prototype.insertBefore;
  Node.prototype.insertBefore = function (newNode, referenceNode) {
    if (referenceNode && referenceNode.parentNode !== this) {
      logger.debug(
        "translationDomGuard: skipped insertBefore on detached reference",
      );
      return newNode;
    }
    return originalInsertBefore.apply(this, arguments);
  };
}
