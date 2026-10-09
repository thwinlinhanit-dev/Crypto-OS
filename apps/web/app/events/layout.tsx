import type { ReactNode } from "react";

import RouteProviders from "@/components/RouteProviders";

export default function EventsLayout({ children }: { children: ReactNode }) {
  return <RouteProviders>{children}</RouteProviders>;
}
