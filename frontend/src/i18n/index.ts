/** Frontend internationalisation: a locale registry plus a few helpers.
 *
 * Text reaches the UI from two places:
 *
 * 1. Static framework copy (button labels, section titles, hints) lives in a
 *    `locales/*.ts` dictionary and is addressed by `t(key)`.  Elements carrying
 *    only such text are marked `data-i18n="key"` in the HTML and refreshed by
 *    `applyDataI18n()`.
 *
 * 2. Data-driven names (crop / scenario / channel / stage / weather) come from
 *    the server as `label` plus one optional variant per language — `label_en`,
 *    `label_es` — and the client picks the right one with `dn(source)`.  Adding
 *    a server-side language therefore needs no change here.
 *
 * **Adding a language**: write `locales/<code>.ts` exporting a `Strings`
 * object, then add one entry to `LOCALES` below.  Nothing else changes — the
 * picker, the persistence check and the `Lang` type are all derived from it.
 */

import { en } from "./locales/en";
import { es } from "./locales/es";
import { zh, type Strings } from "./locales/zh";

export type { Strings };

/** One registered language: its picker label, BCP-47 tag, and dictionary. */
export interface Locale {
  /** Endonym shown in the language picker — "中文", "English", … */
  label: string;
  /** Value for `<html lang>`; browsers use it for hyphenation and quotes. */
  htmlLang: string;
  strings: Strings;
}

export const LOCALES = {
  zh: { label: "中文", htmlLang: "zh-CN", strings: zh },
  en: { label: "English", htmlLang: "en", strings: en },
  es: { label: "Español", htmlLang: "es", strings: es },
} satisfies Record<string, Locale>;

/** Every supported language code — derived, so registering a locale is enough. */
export type Lang = keyof typeof LOCALES;

/** Locale used when nothing is stored, or the stored value is unknown. */
const FALLBACK: Lang = "zh";

const STORAGE_KEY = "rancho.lang";

function isLang(value: string | null): value is Lang {
  return !!value && Object.prototype.hasOwnProperty.call(LOCALES, value);
}

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (isLang(saved)) return saved;
  } catch {
    /* localStorage may be unavailable; fall back to the default. */
  }
  return FALLBACK;
}

let current: Lang = initialLang();
const listeners = new Set<() => void>();

document.documentElement.lang = LOCALES[current].htmlLang;

/** Registered languages, for building the picker. */
export function langs(): Array<{ code: Lang; label: string }> {
  return (Object.keys(LOCALES) as Lang[]).map((code) => ({
    code,
    label: LOCALES[code].label,
  }));
}

export function getLang(): Lang {
  return current;
}

export function setLang(lang: Lang): void {
  if (lang === current) return;
  current = lang;
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    /* ignore persistence failures */
  }
  document.documentElement.lang = LOCALES[lang].htmlLang;
  for (const cb of listeners) cb();
}

export function onLangChange(cb: () => void): void {
  listeners.add(cb);
}

function dict(lang: Lang): Strings {
  return LOCALES[lang].strings;
}

/** Translate a static key, optionally filling `{0}`…`{n}` placeholders.
 *
 * Unknown keys fall back to the baseline locale, then to the key itself so a
 * missing translation is visible on screen rather than blank.
 */
export function t(key: string, params?: Array<string | number>): string {
  let value: string | undefined = dict(current)[key] ?? dict(FALLBACK)[key];
  if (value === undefined) return key;
  if (params) {
    params.forEach((param, index) => {
      value = (value as string).replace(`{${index}}`, String(param));
    });
  }
  return value;
}

/** Like `t()`, but `undefined` when the key is absent in every locale.
 *
 * Used where a server-supplied value has to fall back to itself — enumerations
 * such as `soil.${value}` cannot know about values added later upstream.
 */
export function tOpt(key: string, params?: Array<string | number>): string | undefined {
  const value: string | undefined = dict(current)[key] ?? dict(FALLBACK)[key];
  if (value === undefined) return undefined;
  return params ? t(key, params) : value;
}

type Variants = Record<string, string | null | undefined>;

/** Resolve `field` / `field_<lang>` for the active language.
 *
 * Chinese is the base form; every other language tries its own variant, then
 * English, and only then falls back to the Chinese label — so a language the
 * server has not caught up with yet reads English rather than Chinese.
 */
function pickVariant(source: Variants, field: string): string | undefined {
  const base = source[field];
  if (current === "zh") return typeof base === "string" ? base : undefined;
  const localised = source[`${field}_${current}`];
  if (localised) return localised;
  const english = source[`${field}_en`];
  if (english) return english;
  return typeof base === "string" ? base : undefined;
}

/** Localised server name — reads `label`, `label_en`, `label_es`, … */
export function dn(source: {
  label: string;
  label_en?: string | null;
  label_es?: string | null;
}): string {
  return pickVariant(source as Variants, "label") ?? source.label;
}

/** Localised server blurb — reads `description`, `description_en`, … */
export function dnd(source: {
  description: string;
  description_en?: string | null;
  description_es?: string | null;
}): string {
  return pickVariant(source as Variants, "description") ?? source.description;
}

/** Which attribute each `data-i18n-*` marker writes to.
 *
 * `data-i18n` covers element text; the rest exist for copy that lives in
 * attributes — icon-only buttons carry their name in `aria-label`, for one.
 * Adding an entry is all it takes to localise another attribute.
 */
const ATTR_KEYS = {
  aria: "aria-label",
  title: "title",
  placeholder: "placeholder",
} as const;

/** Refresh every `data-i18n*` element under `root` from the active language. */
export function applyDataI18n(root: ParentNode = document): void {
  root.querySelectorAll<HTMLElement>("[data-i18n]").forEach((node) => {
    const key = node.getAttribute("data-i18n");
    if (key) node.textContent = t(key);
  });
  for (const [suffix, attribute] of Object.entries(ATTR_KEYS)) {
    root.querySelectorAll<HTMLElement>(`[data-i18n-${suffix}]`).forEach((node) => {
      const key = node.getAttribute(`data-i18n-${suffix}`);
      if (key) node.setAttribute(attribute, t(key));
    });
  }
}
