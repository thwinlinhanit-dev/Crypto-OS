import type { ReactNode } from "react";

import RouteProviders from "@/components/RouteProviders";

/** Wraps the Scanner route (M8): the TanStack Query client. */
export default function ScannerLayout({ children }: { children: ReactNode }) {
  return <RouteProviders>{children}</RouteProviders>;
}
