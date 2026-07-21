import type { MetadataRoute } from "next";

// app.novomcp.com is the authenticated product (dashboard + the /studio SPA) —
// there is nothing here for search engines to index. Block all crawlers at the
// host root. Next serves this at /robots.txt. The marketing site (novomcp.com)
// is a separate host and is unaffected.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", disallow: "/" }],
  };
}
