"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

/**
 * Route-scoped TanStack Query client shared by the research routes
 * (Events Explorer, Notebook). Same defaults as the chart's client so the
 * two routes behave identically.
 */
export default function RouteProviders({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
