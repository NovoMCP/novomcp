import { NextResponse } from 'next/server';

// Local/self-host BFF shim.
//
// The dashboard reads everything it needs in local mode directly from the
// engine via /api/local/*. The admin/data endpoints this route used to proxy
// require a managed backend that isn't part of the self-host build, so any
// call here returns a structured 503 that the client-side hooks degrade on
// (empty state instead of a hung spinner). A self-host deployment that wires
// its own backend can replace this route with a real proxy.
async function handler() {
  return NextResponse.json(
    {
      error: 'service_unavailable',
      detail:
        'This feature requires a managed backend that is not configured in local mode.',
    },
    { status: 503 }
  );
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const DELETE = handler;
export const PATCH = handler;
