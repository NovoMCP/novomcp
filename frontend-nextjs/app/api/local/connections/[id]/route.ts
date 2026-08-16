import { NextResponse } from 'next/server';
import { removeConnection } from '@/core/secrets/connectorStore';

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const ok = await removeConnection(id);
  return NextResponse.json({ ok }, { status: ok ? 200 : 404 });
}
