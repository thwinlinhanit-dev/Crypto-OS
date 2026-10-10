import type { ReactNode } from "react";

import RouteProviders from "@/components/RouteProviders";

/** Wraps the Validation dashboard route (M7 T7.4): the TanStack Query client. */
export default function ValidationLayout({ children }: { children: ReactNode }) {
  return <RouteProviders>{children}</RouteProviders>;
}
