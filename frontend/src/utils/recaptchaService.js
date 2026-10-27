import { GOOGLE_SITE_KEY } from "src/config-global";

let recaptchaExecutor = null;
// Callbacks waiting for the executor to become available.
let waiters = [];


export const RECAPTCHA_WAIT_MS = 8000;

export class RecaptchaNotReadyError extends Error {
  constructor() {
    super("recaptcha-not-ready");
    this.name = "RecaptchaNotReadyError";
  }
}

export const setRecaptchaExecutor = (executorFn) => {
  recaptchaExecutor = executorFn;

  const pending = waiters;
  waiters = [];
  pending.forEach((notify) => notify());
};

// Resolves once the executor is available, or rejects after `timeoutMs`.
const waitForExecutor = (timeoutMs) =>
  new Promise((resolve, reject) => {
    if (recaptchaExecutor) {
      resolve();
      return;
    }

    let settled = false;

    const onReady = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve();
    };

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      waiters = waiters.filter((w) => w !== onReady);
      reject(new RecaptchaNotReadyError());
    }, timeoutMs);

    waiters.push(onReady);
  });

export const getRecaptchaToken = async (action, timeoutMs = RECAPTCHA_WAIT_MS) => {
  if (!GOOGLE_SITE_KEY) return "";
  await waitForExecutor(timeoutMs);
  return recaptchaExecutor(action);
};
