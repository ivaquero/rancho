import { defineConfig, type Plugin } from "vite";

import { ATTR_KEYS, DEFAULT_LANG, LOCALES } from "./src/i18n/locales";

// The client talks to the FastAPI service on :8000 during development; in
// production `rancho serve` mounts `dist/` at the same origin, so no proxy is
// needed and `/api` stays relative. `rancho dev` injects RANCHO_API so that
// `--api-port` still lands on the right backend.
declare const process: { env: Record<string, string | undefined> };
const apiTarget = process.env.RANCHO_API ?? "http://127.0.0.1:8000";

/** Bake `DEFAULT_LANG` copy into `index.html`.
 *
 * The markup ships with `data-i18n` keys and empty elements, so no language is
 * hard-coded in the source and copy lives only in `src/i18n/locales/`.  This
 * plugin fills in the default language at build time, which means the page has
 * readable text before the bundle runs.  The bundle then repaints — in the
 * stored language — from `applyDataI18n()`.
 *
 * Two substitution forms, both plain string matches so the markup stays free to
 * reformat:
 *   `data-i18n="key"></`           → element text
 *   `data-i18n-aria="key"`         → `aria-label` (also title, placeholder)
 *   `{{htmlLang}}` / `{{langMap}}` → the inline anti-flash script
 */
function i18nInject(): Plugin {
  const dict = LOCALES[DEFAULT_LANG].strings;
  const langMap = JSON.stringify(
    Object.fromEntries(Object.entries(LOCALES).map(([code, locale]) => [code, locale.htmlLang])),
  );

  return {
    name: "rancho-i18n-inject",
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        let out = html;
        const missing = new Set<string>();

        for (const [key, value] of Object.entries(dict)) {
          if (value === undefined) continue;
          out = out.split(`data-i18n="${key}"></`).join(`data-i18n="${key}">${value}</`);
          for (const [suffix, attribute] of Object.entries(ATTR_KEYS)) {
            const marker = `data-i18n-${suffix}="${key}"`;
            if (out.includes(marker)) out = out.split(marker).join(`${marker} ${attribute}="${value}"`);
          }
        }

        out = out
          .split("{{htmlLang}}")
          .join(LOCALES[DEFAULT_LANG].htmlLang)
          .split("{{defaultLang}}")
          .join(DEFAULT_LANG)
          .split("{{langMap}}")
          .join(langMap);

        // A key in the markup with no translation would render literally.
        for (const match of out.matchAll(/data-i18n(?:-\w+)?="([^"]+)"/g)) {
          if (!(match[1] in dict)) missing.add(match[1]);
        }
        if (missing.size) this.warn(`i18n: unknown keys in index.html — ${[...missing].join(", ")}`);

        return out;
      },
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [i18nInject()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
});
