import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Quant War Room",
  description: "Algoritmik Hedge Fon — Real-time Sinyal & Risk Dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="tr">
      <body>{children}</body>
    </html>
  );
}
