import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { EvidenceGradeBadge } from "./EvidenceGradeBadge";

function render(props: {
  grade: string | null;
  nEff: number;
  ci: [number, number] | null;
  dataScope: string;
}): string {
  return renderToStaticMarkup(createElement(EvidenceGradeBadge, props));
}

describe("EvidenceGradeBadge", () => {
  it("renders the allowed grade-B wording with the triple", () => {
    const html = render({ grade: "B", nEff: 60, ci: [0.1, 0.5], dataScope: "DEVELOPMENT" });
    expect(html).toContain("Evidence Grade");
    expect(html).toContain("Meets pre-defined evidence criteria (grade B)");
    expect(html).toContain("n_eff 60");
    expect(html).toContain("DEVELOPMENT");
  });

  it("renders the grade-D wording", () => {
    const html = render({ grade: "D", nEff: 12, ci: [-0.2, 0.4], dataScope: "DEVELOPMENT" });
    expect(html).toContain("Insufficient evidence");
    expect(html).not.toContain("Meets pre-defined evidence criteria");
  });

  it("renders the HOLDOUT wording with null grade", () => {
    const html = render({ grade: null, nEff: 10, ci: [0.1, 0.3], dataScope: "HOLDOUT" });
    expect(html).toContain("n/a — HOLDOUT scope is judged by pre-registered criteria");
    expect(html).toContain("HOLDOUT");
  });
});
