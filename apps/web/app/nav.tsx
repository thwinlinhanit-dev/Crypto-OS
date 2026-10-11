"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/chart", label: "Chart" },
  { href: "/events", label: "Events" },
  // Spec nav 549 lists Research (Notebook, Hypotheses, Graveyard); M5 ships
  // the Notebook page only (PD-9: no pages for unbuilt modules).
  { href: "/research/notebook", label: "Research" },
  // Spec nav 549 "Experiments" — M5b T5b.3 ships the page under Research.
  { href: "/research/experiments", label: "Experiments" },
  // Hypothesis Factory — the manual research workflow (spec §24 Phase 2).
  { href: "/hypothesis-factory", label: "Hypothesis Factory" },
  // Spec nav 549 "Strategy Lab" — M6 T6.4 ships the page (backtests live
  // under experiments per D-041).
  { href: "/strategy", label: "Strategy Lab" },
  // Spec nav 549 "Validation" — M7 T7.4 ships the dashboard (three levels,
  // baseline distribution, overall status list).
  { href: "/validation", label: "Validation" },
  // Spec nav 549 "Scanner" — M8 ships the page (spec-560 columns, saved
  // scans, state preserved).
  { href: "/scanner", label: "Scanner" },
  // Spec nav 549 "AI Copilot" — M9 ships the page (five modes, the tool
  // registry, flagged placeholders).
  { href: "/ai", label: "AI Copilot" },
  { href: "/data", label: "Data Health" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="nav" aria-label="Primary">
      {LINKS.map((link) => {
        const active =
          link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? "page" : undefined}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
