import type { ReactNode } from "react";

import RouteProviders from "@/components/RouteProviders";

/** Wraps the Strategy Lab route (M6 T6.4): the TanStack Query client. */
export default function StrategyLayout({ children }: { children: ReactNode }) {
  return <RouteProviders>{children}</RouteProviders>;
}
