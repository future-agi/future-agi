/**
 * Settings → Sandboxes, as pure functions of `GET /workspace/sandboxes/settings`
 * ({ settings, operator, caps, canEdit }). A workspace can only narrow what the
 * operator offers, and only among what this deployment can actually run:
 *
 * - a size is a CHOICE when the binding and the host can run it (its caps offer
 *   is available, or refused only by this workspace's own list). A size the
 *   deployment's limit or the host refuses is shown unchecked and disabled with
 *   its reason, and is never sent: offering it would offer nothing.
 * - a template is a choice when its image is on the host and the operator
 *   offers it. Whether it can be used also depends on the sizes chosen in the
 *   form (one of them must be at least its minimum), so the default template
 *   must fit them too.
 *
 * Saving sends a list (sizes, templates, images) only when it is narrower than
 * every choice; leaving every choice on keeps the workspace following the
 * operator when more becomes runnable later.
 */
const SIZE_ORDER = ["small", "medium", "large", "xl"];
const rank = (id) => SIZE_ORDER.indexOf(id);

/** The operator's sizes as caps report them, each marked `selectable`. */
export function sizeChoices(view) {
  const { operator, caps } = view;
  if (!Array.isArray(caps?.sizes)) {
    return operator.sizes.map((id) => ({ id, label: id, selectable: true }));
  }
  return caps.sizes
    .filter((size) => operator.sizes.includes(size.id))
    .map((size) => ({
      ...size,
      selectable: size.available || size.refusedBy === "workspace",
    }));
}

/** Why a template is not offered; `refusedBy` from EE, else read from an older EE's reason. */
function templateRefusal(template) {
  if (template.available) return null;
  if (template.refusedBy) return template.refusedBy;
  if (template.reason?.startsWith("Not offered in this workspace"))
    return "workspace";
  if (template.reason?.startsWith("Needs at least")) return "size";
  if (template.reason?.startsWith("Not offered on this deployment"))
    return "operator";
  return "host";
}

/** The operator's templates as caps report them, each marked `selectable`. */
export function templateChoices(view) {
  const { operator, caps } = view;
  return (caps?.templates || [])
    .filter((template) => operator.templates.includes(template.id))
    .map((template) => {
      const refusedBy = templateRefusal(template);
      return {
        ...template,
        selectable:
          refusedBy === null ||
          refusedBy === "workspace" ||
          refusedBy === "size",
      };
    });
}

/** Whether one of `sizes` is at least the template's minimum size. */
export function templateFits(template, sizes) {
  if (!template?.minSize) return sizes.length > 0;
  return sizes.some((id) => rank(id) >= rank(template.minSize));
}

const ids = (choices) =>
  choices.filter((choice) => choice.selectable).map((choice) => choice.id);

export function settingsFormFrom(view) {
  const { settings = {}, operator, caps } = view;
  const sizeIds = ids(sizeChoices(view));
  const templates = templateChoices(view);
  const templateIds = ids(templates);
  const sizes = (settings.sizes ?? operator.sizes).filter((id) =>
    sizeIds.includes(id),
  );
  const chosenTemplates = (settings.templates ?? operator.templates).filter(
    (id) => templateIds.includes(id),
  );
  const pick = (wanted, among) =>
    wanted.find((id) => id && among.includes(id)) ?? among[0] ?? "";
  const fitting = chosenTemplates.filter((id) =>
    templateFits(
      templates.find((template) => template.id === id),
      sizes,
    ),
  );
  return {
    sizes: orderSizes(sizes),
    templates: chosenTemplates,
    images: [...(settings.images ?? operator.images)],
    defaultSize: pick(
      [settings.defaultSize, operator.defaultSize, caps?.defaultSize],
      sizes,
    ),
    defaultTemplate: pick(
      [
        settings.defaultTemplate,
        operator.defaultTemplate,
        caps?.defaultTemplate,
      ],
      fitting,
    ),
    allowNetwork: operator.allowNetwork && settings.allowNetwork !== false,
    maxActive: String(settings.maxActive ?? operator.maxActivePerWorkspace),
    idleTtlSeconds: settings.idleTtlSeconds ?? operator.idleTtlSeconds,
  };
}

/**
 * Keep the defaults among what the form offers: a default size the form no
 * longer offers moves to the first size offered, and a default template that is
 * unchecked (or no longer fits the sizes) moves to the first one that fits.
 */
export function reconcileDefaults(form, view) {
  const templates = templateChoices(view);
  const fitting = form.templates.filter((id) =>
    templateFits(
      templates.find((template) => template.id === id),
      form.sizes,
    ),
  );
  return {
    ...form,
    defaultSize: form.sizes.includes(form.defaultSize)
      ? form.defaultSize
      : form.sizes[0] ?? "",
    defaultTemplate: fitting.includes(form.defaultTemplate)
      ? form.defaultTemplate
      : fitting[0] ?? "",
  };
}

export function toggle(list, value, order) {
  const next = list.includes(value)
    ? list.filter((item) => item !== value)
    : [...list, value];
  return order ? order.filter((item) => next.includes(item)) : next;
}

export function orderSizes(list) {
  return SIZE_ORDER.filter((id) => list.includes(id));
}

function sizeLabel(view, id) {
  return (view.caps?.sizes || []).find((size) => size.id === id)?.label || id;
}

/**
 * "Needs at least Medium, which this workspace does not offer", or null when it
 * fits. With no size offered at all the sizes say so; no template repeats it.
 */
export function templateFitNote(view, template, sizes) {
  if (sizes.length === 0 || templateFits(template, sizes)) return null;
  return `Needs at least ${sizeLabel(view, template.minSize)}, which this workspace does not offer`;
}

/** -> { field: message }; empty when the form can be saved. */
export function validateSettings(form, view) {
  const { operator } = view;
  const errors = {};
  const sizeIds = ids(sizeChoices(view));
  // With no size this deployment can run at all, the refusal is the
  // deployment's (shown above the form); other settings can still be saved.
  if (sizeIds.length > 0 && form.sizes.length === 0) {
    errors.sizes = "Offer at least one size this deployment can run";
  } else if (form.sizes.length > 0 && !form.sizes.includes(form.defaultSize)) {
    errors.defaultSize = "The default must be one of the sizes offered";
  }
  const templates = templateChoices(view);
  // Templates are judged against the sizes offered; with none offered, the
  // sizes' own error is the one thing to fix.
  if (ids(templates).length > 0 && form.sizes.length > 0) {
    const find = (id) => templates.find((template) => template.id === id);
    const fitting = form.templates.filter((id) =>
      templateFits(find(id), form.sizes),
    );
    if (fitting.length === 0 && form.images.length === 0) {
      errors.templates =
        form.templates.length === 0
          ? "Offer at least one template"
          : "None of these templates fits the sizes offered; offer a bigger size or another template";
    } else if (fitting.length > 0) {
      if (!form.templates.includes(form.defaultTemplate)) {
        errors.defaultTemplate =
          "The default must be one of the templates offered";
      } else if (!fitting.includes(form.defaultTemplate)) {
        errors.defaultTemplate = templateFitNote(
          view,
          find(form.defaultTemplate),
          form.sizes,
        );
      }
    }
  }
  const max = Number(form.maxActive);
  if (
    !Number.isInteger(max) ||
    max < 1 ||
    max > operator.maxActivePerWorkspace
  ) {
    errors.maxActive = `A whole number from 1 to ${operator.maxActivePerWorkspace}`;
  }
  return errors;
}

const sameSet = (a, b) =>
  a.length === b.length && a.every((item) => b.includes(item));

/** Whether the stored list left out something the operator offers. */
const narrowed = (stored, operatorList) =>
  stored !== undefined && !operatorList.every((id) => stored.includes(id));

/**
 * The PUT body: only what narrows the operator's layer. Every list holds only
 * what the form shows checked: a size or template that cannot run here is
 * never sent.
 */
export function buildSettingsPayload(form, view) {
  const { operator, settings = {} } = view;
  const body = {};
  const sizeIds = ids(sizeChoices(view));
  if (
    sizeIds.length > 0 &&
    (!sameSet(form.sizes, sizeIds) || narrowed(settings.sizes, operator.sizes))
  )
    body.sizes = orderSizes(form.sizes);
  if (form.defaultSize && form.defaultSize !== operator.defaultSize)
    body.defaultSize = form.defaultSize;
  const templateIds = ids(templateChoices(view));
  if (templateIds.length > 0) {
    if (
      !sameSet(form.templates, templateIds) ||
      narrowed(settings.templates, operator.templates)
    )
      body.templates = templateIds.filter((id) => form.templates.includes(id));
    if (
      form.defaultTemplate &&
      form.templates.includes(form.defaultTemplate) &&
      form.defaultTemplate !== operator.defaultTemplate
    )
      body.defaultTemplate = form.defaultTemplate;
  }
  if (operator.images.length > 0 && !sameSet(form.images, operator.images)) {
    body.images = form.images;
  }
  if (operator.allowNetwork && !form.allowNetwork) body.allowNetwork = false;
  const max = Number(form.maxActive);
  if (max !== operator.maxActivePerWorkspace) body.maxActive = max;
  if (form.idleTtlSeconds !== operator.idleTtlSeconds)
    body.idleTtlSeconds = form.idleTtlSeconds;
  return body;
}
