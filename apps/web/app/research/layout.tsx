import type { ReactNode } from "react";

import RouteProviders from "@/components/RouteProviders";

/** Wraps every Research sub-route (Notebook today; Hypotheses/Graveyard later). */
export default function ResearchLayout({ children }: { children: ReactNode }) {
  return <RouteProviders>{children}</RouteProviders>;
}
