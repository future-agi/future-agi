/**
 * The create-drawer form, as pure functions of the caps EE returns with
 * `GET /workspace/sandboxes`. The form can only offer what the caps allow:
 *
 * - templates from caps.templates (curated images, each with what is installed
 *   and whether this workspace may use it), images from caps.images, or one of
 *   the workspace's container environments;
 * - sizes from caps.sizes (Small, Medium, Large, XL; each offered or not, with
 *   the reason), defaulting to caps.defaultSize, never to the ceiling; custom
 *   limits only when caps.customLimits, within caps.limits.min..max;
 * - network off unless the user turns it on and caps.networkModes allows it;
 * - idle timeouts within caps.idleTtlSeconds.min..max.
 *
 * Caps from an EE without sizes or templates (no `sizes`/`templates` fields)
 * fall back to the image list and free-form limits, as before.
 */
import { templateReason } from "./offerCopy";

const TTL_PRESETS = [300, 900, 1800, 3600, 4 * 3600, 24 * 3600, 7 * 24 * 3600];
const SIZE_ORDER = ["small", "medium", "large", "xl"];

export const CUSTOM_SIZE = "custom";

export const LIMIT_FIELDS = [
  { key: "cpus", label: "CPU", unit: "", step: 0.1, integer: false },
  { key: "memoryMb", label: "Memory", unit: "MiB", step: 64, integer: true },
  { key: "pidsLimit", label: "Processes", unit: "", step: 16, integer: true },
];

/** Idle-timeout choices: presets inside the caps, plus the caps default. */
export function ttlOptions(caps) {
  const { min, max, default: fallback } = caps.idleTtlSeconds;
  const inRange = TTL_PRESETS.filter((value) => value >= min && value <= max);
  return [...new Set([...inRange, fallback])].sort((a, b) => a - b);
}

export function canUseNetwork(caps) {
  return caps.networkModes.includes("full");
}

/** True when EE offers named sizes (any of them may be unavailable). */
export function hasSizes(caps) {
  return Array.isArray(caps.sizes) && caps.sizes.length > 0;
}

/** True when EE lists curated templates. */
export function hasTemplates(caps) {
  return Array.isArray(caps.templates) && caps.templates.length > 0;
}

export function offeredSizes(caps) {
  return hasSizes(caps) ? caps.sizes.filter((size) => size.available) : [];
}

/** Whether free-form limits may be sent (older caps: always, there are no sizes). */
export function canUseCustomLimits(caps) {
  return !hasSizes(caps) || caps.customLimits !== false;
}

/** Images on the allowed list that are not a curated template's own image. */
export function extraImages(caps) {
  const templateImages = new Set(
    (caps.templates || []).map((template) => template.image),
  );
  return caps.images.filter((image) => !templateImages.has(image));
}

export function sizeRank(id) {
  return SIZE_ORDER.indexOf(id);
}

export function findTemplate(caps, id) {
  return (caps.templates || []).find((template) => template.id === id) || null;
}

export function findSize(caps, id) {
  return (caps.sizes || []).find((size) => size.id === id) || null;
}

/** The sources this workspace can create from, in display order. */
export function sourceChoices(caps) {
  const choices = [];
  if (hasTemplates(caps)) choices.push("catalog");
  if (!hasTemplates(caps) || extraImages(caps).length > 0)
    choices.push("image");
  choices.push("environment");
  return choices;
}

/** A container template is an ACTIVE environment profile of kind container. */
export function templateChoices(environments) {
  return (environments || []).map((environment) => {
    let reason = null;
    if (environment.sandboxKind !== "container") {
      reason = "Runs on the shared local baseline, not in a container";
    } else if (environment.status !== "active") {
      reason = `This template is ${environment.status}`;
    }
    return {
      id: environment.id,
      name: environment.name || environment.id,
      disabled: reason !== null,
      reason,
    };
  });
}

function limitsOf(caps, sizeId) {
  const size = findSize(caps, sizeId);
  if (size) {
    return {
      cpus: size.cpus,
      memoryMb: size.memoryMb,
      pidsLimit: size.pidsLimit,
    };
  }
  return caps.limits.default;
}

export function initialForm(caps) {
  const catalog = hasTemplates(caps) && caps.defaultTemplate;
  const extras = extraImages(caps);
  const size =
    hasSizes(caps) && caps.defaultSize ? caps.defaultSize : CUSTOM_SIZE;
  const limits = limitsOf(caps, size);
  return {
    name: "",
    source: catalog ? "catalog" : "image",
    template: catalog ? caps.defaultTemplate : "",
    image: catalog ? extras[0] || "" : caps.defaultImage,
    environmentId: "",
    size,
    cpus: String(limits.cpus),
    memoryMb: String(limits.memoryMb),
    pidsLimit: String(limits.pidsLimit),
    network:
      caps.defaultNetwork === "full" && canUseNetwork(caps) ? "full" : "none",
    idleTtlSeconds: caps.idleTtlSeconds.default,
    // The size starts at the workspace's default; a template's suggestion
    // replaces it until the user picks a size (chooseTemplate, chooseSize).
    sizeChosen: false,
    // The size the user picked, kept while a template's minimum moves the
    // size up, so the next template that allows it goes back to it.
    sizePicked: null,
    sizeSuggested: null,
    sizeBumped: null,
  };
}

/** The custom-limit fields filled with a size's shape. */
function shapeOf(caps, sizeId) {
  const limits = limitsOf(caps, sizeId);
  return {
    cpus: String(limits.cpus),
    memoryMb: String(limits.memoryMb),
    pidsLimit: String(limits.pidsLimit),
  };
}

/**
 * Picking a template applies the size it suggests (`sizeHint`), unless the
 * user picked a size themselves or this workspace does not offer that size;
 * the patch says which (`sizeSuggested`). A size below the template's
 * minimum always moves up to the smallest offered size that fits
 * (`sizeBumped`), because EE refuses it otherwise. That move is measured
 * from the size the user picked (`sizePicked`), not from an earlier move:
 * passing over a larger template (with the arrow keys, say) on the way to
 * another leaves the user's own size in place.
 */
export function chooseTemplate(form, caps, templateId) {
  const template = findTemplate(caps, templateId);
  const patch = { template: templateId, sizeBumped: null, sizeSuggested: null };
  if (!template || form.size === CUSTOM_SIZE) return patch;
  const minRank = sizeRank(template.minSize || "small");
  const hint = findSize(caps, template.sizeHint);
  if (!form.sizeChosen && hint?.available && sizeRank(hint.id) >= minRank) {
    if (hint.id === form.size) return patch;
    return {
      ...patch,
      size: hint.id,
      ...shapeOf(caps, hint.id),
      sizeSuggested: hint.label,
    };
  }
  const picked = form.sizeChosen ? findSize(caps, form.sizePicked) : null;
  const from = picked?.available ? picked.id : form.size;
  if (sizeRank(from) >= minRank) {
    if (from === form.size) return patch;
    return { ...patch, size: from, ...shapeOf(caps, from) };
  }
  const fit = offeredSizes(caps).find((size) => sizeRank(size.id) >= minRank);
  return fit
    ? {
        ...patch,
        size: fit.id,
        ...shapeOf(caps, fit.id),
        sizeBumped: fit.label,
      }
    : patch;
}

/** Choosing a named size (the user's own pick) also fills the custom fields with its shape. */
export function chooseSize(caps, sizeId) {
  const picked = {
    sizeBumped: null,
    sizeSuggested: null,
    sizeChosen: true,
    sizePicked: sizeId,
  };
  if (sizeId === CUSTOM_SIZE) return { size: CUSTOM_SIZE, ...picked };
  return { size: sizeId, ...picked, ...shapeOf(caps, sizeId) };
}

function limitError(field, raw, caps) {
  const min = caps.limits.min[field.key];
  const max = caps.limits.max[field.key];
  const value = Number(raw);
  if (raw === "" || !Number.isFinite(value)) return "Enter a number";
  if (field.integer && !Number.isInteger(value)) return "Enter a whole number";
  if (value < min || value > max) return `Between ${min} and ${max}`;
  return null;
}

/**
 * Custom limits below the chosen template's minimum size, as EE checks them
 * (CPU and memory against that size's shape; Small when the template names
 * none), so the form refuses them before a create EE would refuse with 400.
 */
function templateMinimumErrors(form, caps, errors) {
  if (form.source !== "catalog" || errors.template) return {};
  const template = findTemplate(caps, form.template);
  const min = template && findSize(caps, template.minSize || "small");
  if (!min) return {};
  const found = {};
  if (!errors.cpus && Number(form.cpus) < min.cpus) {
    found.cpus = `${template.name} needs at least ${min.cpus}`;
  }
  if (!errors.memoryMb && Number(form.memoryMb) < min.memoryMb) {
    found.memoryMb = `${template.name} needs at least ${min.memoryMb}`;
  }
  return found;
}

/** -> { fieldName: message } for every invalid field; empty when valid. */
export function validateForm(form, caps) {
  const errors = {};
  const name = form.name.trim();
  if (name && new TextEncoder().encode(name).length > 120) {
    errors.name = "At most 120 UTF-8 bytes";
  }
  if (form.source === "catalog") {
    const template = findTemplate(caps, form.template);
    if (!template) errors.template = "Choose a template";
    else if (!template.available) errors.template = templateReason(template);
  }
  if (form.source === "image" && !caps.images.includes(form.image)) {
    errors.image = "Choose an image this deployment offers";
  }
  if (form.source === "environment" && !form.environmentId) {
    errors.environmentId = "Choose an environment";
  }
  if (form.size === CUSTOM_SIZE) {
    for (const field of LIMIT_FIELDS) {
      const message = limitError(field, form[field.key], caps);
      if (message) errors[field.key] = message;
    }
    Object.assign(errors, templateMinimumErrors(form, caps, errors));
  } else {
    const size = findSize(caps, form.size);
    if (!size || !size.available) errors.size = "Choose a size offered here";
    const template =
      form.source === "catalog" ? findTemplate(caps, form.template) : null;
    if (
      size &&
      template &&
      sizeRank(size.id) < sizeRank(template.minSize) &&
      !errors.template
    ) {
      const min = findSize(caps, template.minSize);
      errors.size = `${template.name} needs at least ${min ? min.label : template.minSize}`;
    }
  }
  return errors;
}

/** The POST /workspace/sandboxes body. Only fields the API accepts. */
export function buildCreatePayload(form) {
  const name = form.name.trim();
  const source =
    form.source === "catalog"
      ? { template: form.template }
      : form.source === "environment"
        ? { environmentId: form.environmentId }
        : { image: form.image };
  const shape =
    form.size === CUSTOM_SIZE
      ? {
          limits: {
            cpus: Number(form.cpus),
            memoryMb: Number(form.memoryMb),
            pidsLimit: Number(form.pidsLimit),
          },
        }
      : { size: form.size };
  return {
    ...(name ? { name } : {}),
    ...source,
    ...shape,
    network: form.network,
    idleTtlSeconds: form.idleTtlSeconds,
  };
}

/** "Small · 0.5 vCPU · 1 GB" for a size offer. */
export function sizeSummary(size) {
  const gb = size.memoryMb / 1024;
  const memory = Number.isInteger(gb) ? `${gb} GiB` : `${size.memoryMb} MiB`;
  return `${size.cpus} vCPU · ${memory}`;
}
