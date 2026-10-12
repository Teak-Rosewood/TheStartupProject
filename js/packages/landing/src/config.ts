// Everything a fork changes lives here or in environment variables. No secrets: this ships to browsers.
const env = import.meta.env;

export const site = {
  name: env.PUBLIC_SITE_NAME ?? "Your product",
  tagline: env.PUBLIC_TAGLINE ?? "One clear sentence about the problem you solve.",
  description:
    env.PUBLIC_DESCRIPTION ??
    "Describe what your product does and who it is for in one or two sentences, so search results and link previews say something useful.",
  url: (env.SITE_URL ?? "https://app.example.com").replace(/\/$/, ""),
  company: env.PUBLIC_COMPANY ?? "Your Company Private Limited",
  address: env.PUBLIC_ADDRESS ?? "1 Example Street, Bengaluru, Karnataka 560001, India",
  supportEmail: env.PUBLIC_SUPPORT_EMAIL ?? "support@example.com",
  securityEmail: env.PUBLIC_SECURITY_EMAIL ?? "security@example.com",
  // The sign-in page of your web app. Empty hides the "Sign in" link until the app exists.
  appUrl: env.PUBLIC_APP_URL ?? "",
  // Cloudflare Turnstile or hCaptcha site key (public). Empty disables the widget.
  captchaSiteKey: env.PUBLIC_CAPTCHA_SITE_KEY ?? "",
  captchaProvider: env.PUBLIC_CAPTCHA_PROVIDER ?? "turnstile",
  ogImage: env.PUBLIC_OG_IMAGE ?? "/og.png",
};

export const nav = [
  { href: "/pricing", label: "Pricing" },
  { href: "/blog", label: "Blog" },
];
