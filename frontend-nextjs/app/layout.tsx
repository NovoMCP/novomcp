import type { Metadata } from "next";
import { Cormorant_Garamond, Inter } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/core/auth/provider";
import { QueryProvider } from "@/core/providers/QueryProvider";
import { ThemeProvider } from "@/core/providers/ThemeProvider";

const cormorant = Cormorant_Garamond({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-cormorant",
  display: "swap",
});

const inter = Inter({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-inter",
  display: "swap",
});

// Deployment-configurable public URL for SEO/OpenGraph metadata.
// Local OSS builds default to localhost; hosted deployments set
// NEXT_PUBLIC_SITE_URL to their public origin.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";

export const metadata: Metadata = {
  title: "NovoMCP",
  description:
    "The computational chemistry engine for drug discovery and materials science.",
  metadataBase: new URL(SITE_URL),
  // The app is an authenticated, non-public surface — keep it out of search
  // indexes even if a URL leaks via a link (robots.txt blocks the crawl; this
  // emits <meta name="robots" content="noindex,nofollow"> as a backstop).
  robots: { index: false, follow: false },
  icons: {
    icon: "/favicon.png",
    apple: "/apple-touch-icon.png",
  },
  openGraph: {
    title: "NovoMCP",
    description:
      "The computational chemistry engine for drug discovery and materials science.",
    siteName: "NovoMCP",
    type: "website",
    url: SITE_URL,
  },
  twitter: {
    card: "summary_large_image",
    title: "NovoMCP",
    description:
      "The computational chemistry engine for drug discovery and materials science.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${cormorant.variable} ${inter.variable}`} suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem('novomcp-theme');if(t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme:dark)').matches)){document.documentElement.classList.add('dark')}}catch(e){}})()`,
          }}
        />
      </head>
      <body className={`${inter.className} antialiased`}>
        <ThemeProvider>
          <AuthProvider>
            <QueryProvider>
              {children}
            </QueryProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
