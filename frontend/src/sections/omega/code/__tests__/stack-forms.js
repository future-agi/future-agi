// The create-drawer forms a live stack's drawer offers, built from that
// stack's own caps the way the drawer builds them (initialForm, chooseSize),
// so the live run sends only what that stack's drawer would: network on only
// where the stack offers it, a size only when it is available, an image only
// when the image source is shown. Each form passes the drawer's own checks.
import {
  CUSTOM_SIZE,
  canUseCustomLimits,
  canUseNetwork,
  chooseSize,
  extraImages,
  initialForm,
  offeredSizes,
  sourceChoices,
  validateForm,
} from "../../sandboxes/lib/createForm";

export function stackForms(caps) {
  const base = initialForm(caps);
  const forms = [base, { ...base, name: "it's a snippet" }];
  const other = offeredSizes(caps).find((size) => size.id !== base.size);
  if (other) forms.push({ ...base, ...chooseSize(caps, other.id) });
  if (canUseCustomLimits(caps) && base.size !== CUSTOM_SIZE) {
    forms.push({ ...base, ...chooseSize(caps, CUSTOM_SIZE) });
  }
  if (canUseNetwork(caps)) {
    forms.push({ ...base, network: base.network === "full" ? "none" : "full" });
  }
  const images = extraImages(caps);
  if (
    base.source !== "image" &&
    sourceChoices(caps).includes("image") &&
    images.length > 0
  ) {
    forms.push({ ...base, source: "image", image: images[0] });
  }
  return forms.filter(
    (form) => Object.keys(validateForm(form, caps)).length === 0,
  );
}
