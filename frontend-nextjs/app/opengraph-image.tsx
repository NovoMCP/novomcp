import { ImageResponse } from 'next/og';

export const runtime = 'edge';
export const alt = 'NovoMCP — The computational chemistry engine for drug discovery and materials science.';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export default async function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: '#1a1a1a',
          fontFamily: 'Inter, sans-serif',
        }}
      >
        {/* Logo */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="https://novomcp.com/icon.png"
          alt=""
          width={120}
          height={120}
          style={{ borderRadius: 24 }}
        />

        {/* Title */}
        <div
          style={{
            marginTop: 32,
            fontSize: 52,
            fontWeight: 700,
            color: '#ffffff',
            letterSpacing: '-0.02em',
          }}
        >
          NovoMCP
        </div>

        {/* Subtitle */}
        <div
          style={{
            marginTop: 12,
            fontSize: 22,
            color: '#a3a3a3',
            letterSpacing: '0.02em',
          }}
        >
          The Computational Chemistry Engine
        </div>
      </div>
    ),
    { ...size }
  );
}
