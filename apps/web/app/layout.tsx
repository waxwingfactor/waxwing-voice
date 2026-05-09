import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Waxwing Voice - Hunter Property Management",
  description: "Property manager dashboard for Waxwing Voice."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
