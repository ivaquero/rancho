/** The locale registry — pure data, no DOM access.
 *
 * It lives apart from `../index.ts` (the runtime) so that Node-side code — the
 * Vite plugin that bakes the default language into `index.html` — can import
 * the dictionaries without touching `document`.
 */

import { en } from "./en";
import { es } from "./es";
import { zh, type Strings } from "./zh";

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

/** Language used when nothing is stored, or the stored value is unknown.
 *
 * Also the one the Vite plugin bakes into `index.html`, so the page has copy
 * before the bundle runs.
 */
export const DEFAULT_LANG: Lang = "zh";

/** `data-i18n-*` marker suffix → the attribute it writes.
 *
 * `data-i18n` covers element text; the rest exist for copy that lives in
 * attributes — icon-only buttons carry their name in `aria-label`, for one.
 * Adding an entry is all it takes to localise another attribute.
 */
export const ATTR_KEYS = {
  aria: "aria-label",
  title: "title",
  placeholder: "placeholder",
} as const;
