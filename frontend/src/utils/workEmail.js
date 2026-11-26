

export const NON_WORK_EMAIL_DOMAINS = [
  "gmail.com",
  "googlemail.com",
  "outlook.com",
  "hotmail.com",
  "live.com",
  "msn.com",
  "yahoo.com",
  "aol.com",
  "icloud.com",
  "me.com",
  "protonmail.com",
  "proton.me",
  "zoho.com",
  "yandex.com",
  "mail.com",
  "gmx.com",
  "rediffmail.com",
  "qq.com",
  "foxmail.com",
  "rocketmail.com",
  "yandex.ru",
  "mailinator.com",
  "yopmail.com",
  "web-library.net",
  "example.com",
  "noreply.github.com",
  "github.com",
];

const NON_WORK_DOMAIN_SET = new Set(NON_WORK_EMAIL_DOMAINS);


export function isWorkEmail(email) {
  if (typeof email !== "string") return true;

  const normalized = email.trim().toLowerCase();
  const [localPart, domain, ...rest] = normalized.split("@");
  if (!localPart || !domain || rest.length) return true;

  return !NON_WORK_DOMAIN_SET.has(domain);
}
