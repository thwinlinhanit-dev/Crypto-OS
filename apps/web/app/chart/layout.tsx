import type { ReactNode } from "react";

import ChartProviders from "./providers";

export default function ChartLayout({ children }: { children: ReactNode }) {
  return <ChartProviders>{children}</ChartProviders>;
}
