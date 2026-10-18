import { describe, expect, it } from "vitest";
import type { Artifact } from "@/lib/api/types";
import { buildPreview } from "@/lib/workflow/preview";

const file = (path: string, content: string): Artifact => ({
  id: path,
  workflow_id: "wf",
  task_id: null,
  path,
  content,
  content_type: "text/plain",
  version: 1,
  updated_at: "",
});

describe("buildPreview", () => {
  const page = file("site/index.html", '<html><head><link rel="stylesheet" href="styles.css"></head><body><script src="./app.js"></script></body></html>');
  const siblings = [page, file("site/styles.css", "body{color:red}"), file("site/app.js", "console.log(1)")];

  it("inlines sibling stylesheets and scripts", () => {
    const html = buildPreview(page, siblings);
    expect(html).toContain("<style>body{color:red}</style>");
    expect(html).toContain("<script>console.log(1)</script>");
    expect(html).not.toContain('href="styles.css"');
  });

  it("locks the page down before anything else loads", () => {
    const html = buildPreview(page, siblings);
    const policy = html.indexOf("Content-Security-Policy");
    expect(policy).toBeGreaterThan(-1);
    expect(policy).toBeLessThan(html.indexOf("<style>"));
    expect(html).toContain("default-src 'none'");
  });

  it("drops references to files it does not have rather than fetching them", () => {
    const html = buildPreview(file("index.html", '<head></head><script src="https://evil.example/x.js"></script>'), []);
    expect(html).not.toContain("evil.example");
  });

  it("cannot be broken out of with a closing tag inside a stylesheet", () => {
    const html = buildPreview(file("a.html", '<head><link rel="stylesheet" href="b.css"></head>'), [file("b.css", "</style><script>alert(1)</script>")]);
    expect(html).not.toContain("</style><script>");
  });
});
