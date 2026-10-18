/**
 * A self-contained preview of a generated HTML page. Sibling CSS and JS
 * artifacts are inlined, and a strict policy is injected so the page cannot
 * load anything or reach any server — it is untrusted model output, shown in
 * a sandboxed frame with no access to this app.
 */

import type { Artifact } from "@/lib/api/types";

const LOCKDOWN =
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; ` +
  `script-src 'unsafe-inline'; img-src data:; font-src data:">`;

const escapeClosing = (text: string, tag: string) => text.replace(new RegExp(`</${tag}`, "gi"), `<\\/${tag}`);

export function buildPreview(page: Artifact, siblings: Artifact[]): string {
  const dir = page.path.includes("/") ? page.path.slice(0, page.path.lastIndexOf("/") + 1) : "";
  const find = (href: string) => {
    const clean = href.replace(/^\.\//, "").split(/[?#]/)[0] ?? "";
    return siblings.find((a) => a.path === dir + clean || a.path === clean);
  };

  let html = page.content
    .replace(/<link\b[^>]*rel=["']?stylesheet["']?[^>]*>/gi, (tag) => {
      const href = /href=["']([^"']+)["']/i.exec(tag)?.[1];
      const file = href ? find(href) : undefined;
      return file ? `<style>${escapeClosing(file.content, "style")}</style>` : "";
    })
    .replace(/<script\b([^>]*)\bsrc=["']([^"']+)["']([^>]*)>\s*<\/script>/gi, (_, before, src) => {
      const file = find(src);
      return file ? `<script${String(before).trimEnd()}>${escapeClosing(file.content, "script")}</script>` : "";
    });

  // The policy must come first, so it applies before anything else loads.
  html = /<head[^>]*>/i.test(html)
    ? html.replace(/<head[^>]*>/i, (head) => `${head}${LOCKDOWN}`)
    : `${LOCKDOWN}${html}`;
  return html;
}
