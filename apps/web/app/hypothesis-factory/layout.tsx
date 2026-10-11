import type { ReactNode } from "react";

import RouteProviders from "@/components/RouteProviders";

/** Wraps the Hypothesis Factory route (spec §24 Phase 2 manual workflow). */
export default function HypothesisFactoryLayout({ children }: { children: ReactNode }) {
  return <RouteProviders>{children}</RouteProviders>;
}
