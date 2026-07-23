import { NextRequest, NextResponse } from 'next/server';

const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL || 'http://localhost:8018';

// Public, same-origin resolver for the hosted upload page. The page can't carry
// the presigned URL in its link fragment — the ~700-char SigV4 URL gets truncated
// by LLMs when they surface the link (this broke uploads after the Azure→AWS move).
// So the page fetches a fresh presigned URL here by file_id. Proxies to novomcp's
// public GET /files/{file_id}/upload-url so the browser avoids a cross-origin call.
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ fileId: string }> }
) {
  const { fileId } = await params;
  if (!/^f-[a-f0-9]{6,}$/i.test(fileId)) {
    return NextResponse.json({ error: 'Invalid file ID' }, { status: 400 });
  }
  try {
    const res = await fetch(`${NOVOMCP_ENGINE_URL}/files/${fileId}/upload-url`, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
    });
    const data = await res.text();
    return new NextResponse(data, {
      status: res.status,
      headers: { 'Content-Type': res.headers.get('Content-Type') || 'application/json' },
    });
  } catch (e) {
    console.error('upload-url proxy error:', e);
    return NextResponse.json({ error: 'Upstream service unavailable' }, { status: 502 });
  }
}
