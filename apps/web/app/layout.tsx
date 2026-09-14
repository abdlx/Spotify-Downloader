import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lilt — Local Music Library",
  description: "Build your local music library from Spotify metadata and authorized sources.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

