import { redirect } from 'next/navigation';

// Root entry point. The dashboard is the home surface, so send users straight
// there — a server redirect, with no client-side spinner. The (platform) layout
// owns the auth guard: in hosted mode it bounces unauthenticated users to
// /login, and in OSS single-user mode it auto-provisions a local user.
export default function RootPage() {
  redirect('/dashboard');
}
