import type { ReactNode } from "react";

import RouteProviders from "@/components/RouteProviders";

/** Wraps the AI Copilot route (M9): the TanStack Query client. */
export default function AiLayout({ children }: { children: ReactNode }) {
  return <RouteProviders>{children}</RouteProviders>;
}
