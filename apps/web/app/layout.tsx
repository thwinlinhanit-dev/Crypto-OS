import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { Nav } from "./nav";

export const metadata: Metadata = {
  title: "CryptoOS",
  description: "CryptoOS — research terminal.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <span className="brand">CryptoOS</span>
          <Nav />
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
