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
