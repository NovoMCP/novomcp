'use client';

import { useState, useEffect } from 'react';
import { useAuth } from '@/core/auth/provider';
import { useUserProfile, useUpdateUserProfile } from '@/core/api/admin-client';
import { User, Mail, Building2, Briefcase, Bell, Globe, Save, Edit2, Check } from 'lucide-react';

// Hosted-only account settings — profile, notifications, preferences. These
// persist through the managed backend (admin-client), so this whole surface is
// rendered only when NEXT_PUBLIC_REQUIRE_AUTH is on. In local single-user mode
// the parent page renders the local-environment card instead, and this
// component never mounts (so its managed-backend queries never fire).

interface UserPreferences {
  emailNotifications: boolean;
  weeklyDigest: boolean;
  theme: 'light' | 'dark' | 'system';
  language: string;
  timezone: string;
}

function joinName(first?: string | null, last?: string | null): string {
  return [first, last].filter(Boolean).join(' ').trim();
}

function splitName(full: string): { first_name: string; last_name: string } {
  const parts = full.trim().split(/\s+/);
  if (parts.length === 0 || parts[0] === '') return { first_name: '', last_name: '' };
  if (parts.length === 1) return { first_name: parts[0], last_name: '' };
  return { first_name: parts[0], last_name: parts.slice(1).join(' ') };
}

export default function HostedAccountSettings() {
  const { user } = useAuth();
  const { data: profile } = useUserProfile();
  const updateProfile = useUpdateUserProfile();
  const [isEditingProfile, setIsEditingProfile] = useState(false);
  const [isSavingPrefs, setIsSavingPrefs] = useState(false);
  const [saveMessage, setSaveMessage] = useState('');
  const [saveError, setSaveError] = useState('');

  const [profileData, setProfileData] = useState({
    name: '',
    email: '',
    title: '',
    department: '',
    organization: user?.orgId || '',
  });

  const [preferences, setPreferences] = useState<UserPreferences>({
    emailNotifications: true,
    weeklyDigest: true,
    theme: 'light',
    language: 'en',
    timezone: 'UTC',
  });

  useEffect(() => {
    if (!profile) return;
    const p = profile as Record<string, unknown>;
    setProfileData({
      name: joinName(p.first_name as string, p.last_name as string) || user?.name || '',
      email: (p.email as string) || user?.email || '',
      title: (p.job_title as string) || '',
      department: (p.department as string) || '',
      organization: user?.orgId || '',
    });
    setPreferences((prev) => ({
      ...prev,
      language: (p.language_preference as string) || prev.language,
      timezone: (p.timezone as string) || prev.timezone,
    }));
  }, [profile, user?.name, user?.email, user?.orgId]);

  const flashMessage = (text: string) => {
    setSaveMessage(text);
    setTimeout(() => setSaveMessage(''), 3000);
  };

  const handleProfileSave = async () => {
    setSaveError('');
    const { first_name, last_name } = splitName(profileData.name);
    try {
      await updateProfile.mutateAsync({
        first_name,
        last_name,
        job_title: profileData.title,
        department: profileData.department,
      });
      setIsEditingProfile(false);
      flashMessage('Profile updated');
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Failed to update profile');
    }
  };

  const handlePreferencesSave = async () => {
    setIsSavingPrefs(true);
    setSaveError('');
    try {
      await updateProfile.mutateAsync({ timezone: preferences.timezone });
      flashMessage('Preferences saved');
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Failed to save preferences');
    } finally {
      setIsSavingPrefs(false);
    }
  };

  const inputClass =
    'w-full px-3 py-2 bg-[var(--bg-warm)] border border-[var(--border)] rounded text-[var(--text)] focus:outline-none focus:border-[var(--accent)] transition-colors text-sm';
  const readOnlyClass = 'flex items-center gap-3 px-3 py-2 bg-[var(--bg-warm)] border border-[var(--border)] rounded';

  return (
    <>
      {(saveMessage || saveError) && (
        <div
          className={`flex items-center gap-2 px-4 py-2 rounded ${
            saveError
              ? 'bg-[var(--destructive)]/10 border border-[var(--destructive)]/20 text-[var(--destructive)]'
              : 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-500'
          }`}
        >
          {!saveError && <Check className="h-4 w-4" />}
          <span className="text-sm">{saveError || saveMessage}</span>
        </div>
      )}

      {/* Profile */}
      <Panel icon={<User className="h-3.5 w-3.5" />} title="Profile"
        action={
          !isEditingProfile ? (
            <button onClick={() => setIsEditingProfile(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-[var(--accent)] rounded text-[var(--accent)] hover:bg-[var(--accent)]/5 transition-colors">
              <Edit2 className="h-3 w-3" /> Edit
            </button>
          ) : undefined
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Field label="Full name">
            {isEditingProfile ? (
              <input value={profileData.name} onChange={(e) => setProfileData({ ...profileData, name: e.target.value })} className={inputClass} />
            ) : (
              <div className={readOnlyClass}><User className="h-4 w-4 text-[var(--text-muted)]" /><span className="text-sm text-[var(--text)]">{profileData.name}</span></div>
            )}
          </Field>
          <Field label="Email">
            <div className={readOnlyClass}>
              <Mail className="h-4 w-4 text-[var(--text-muted)]" />
              <span className="text-sm text-[var(--text-soft)]">{profileData.email}</span>
            </div>
          </Field>
          <Field label="Title">
            {isEditingProfile ? (
              <input value={profileData.title} onChange={(e) => setProfileData({ ...profileData, title: e.target.value })} className={inputClass} />
            ) : (
              <div className={readOnlyClass}><Briefcase className="h-4 w-4 text-[var(--text-muted)]" /><span className="text-sm text-[var(--text)]">{profileData.title}</span></div>
            )}
          </Field>
          <Field label="Department">
            {isEditingProfile ? (
              <input value={profileData.department} onChange={(e) => setProfileData({ ...profileData, department: e.target.value })} placeholder="e.g., Computational Biology" className={inputClass} />
            ) : (
              <div className={readOnlyClass}><Building2 className="h-4 w-4 text-[var(--text-muted)]" /><span className="text-sm text-[var(--text)]">{profileData.department}</span></div>
            )}
          </Field>
        </div>
        {isEditingProfile && (
          <div className="flex gap-3 pt-4">
            <button onClick={handleProfileSave} disabled={updateProfile.isPending}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] rounded hover:brightness-105 transition disabled:opacity-50">
              <Save className="h-4 w-4" /> {updateProfile.isPending ? 'Saving…' : 'Save changes'}
            </button>
            <button onClick={() => { setIsEditingProfile(false); setSaveError(''); }} disabled={updateProfile.isPending}
              className="px-4 py-2 text-sm text-[var(--text-soft)] border border-[var(--border)] rounded hover:bg-[var(--bg-warm)] transition-colors disabled:opacity-50">
              Cancel
            </button>
          </div>
        )}
      </Panel>

      {/* Notifications */}
      <Panel icon={<Bell className="h-3.5 w-3.5" />} title="Notifications"
        action={<span className="text-[10px] font-medium px-2 py-0.5 rounded bg-[var(--bg-warm)] text-[var(--text-muted)]">Coming soon</span>}>
        <div className="space-y-3 opacity-50 pointer-events-none">
          {[
            { key: 'emailNotifications', label: 'Email notifications', description: 'Receive notifications via email' },
            { key: 'weeklyDigest', label: 'Weekly digest', description: 'Summary of usage and activity' },
          ].map(({ key, label, description }) => (
            <div key={key} className="flex items-center justify-between py-2">
              <div>
                <p className="text-sm text-[var(--text)]">{label}</p>
                <p className="text-xs text-[var(--text-muted)] mt-0.5">{description}</p>
              </div>
              <span className={`relative inline-flex h-6 w-11 items-center rounded-full ${preferences[key as keyof UserPreferences] ? 'bg-[var(--accent)]' : 'bg-[var(--border)]'}`}>
                <span className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${preferences[key as keyof UserPreferences] ? 'translate-x-6' : 'translate-x-1'}`} />
              </span>
            </div>
          ))}
        </div>
      </Panel>

      {/* Preferences */}
      <Panel icon={<Globe className="h-3.5 w-3.5" />} title="Preferences">
        <Field label="Timezone">
          <select value={preferences.timezone} onChange={(e) => setPreferences({ ...preferences, timezone: e.target.value })} className={inputClass}>
            <option value="America/New_York">Eastern Time (ET)</option>
            <option value="America/Chicago">Central Time (CT)</option>
            <option value="America/Denver">Mountain Time (MT)</option>
            <option value="America/Los_Angeles">Pacific Time (PT)</option>
            <option value="Europe/London">London (GMT)</option>
            <option value="Europe/Paris">Paris (CET)</option>
            <option value="Asia/Tokyo">Tokyo (JST)</option>
            <option value="UTC">UTC</option>
          </select>
        </Field>
        <div className="flex justify-end pt-4">
          <button onClick={handlePreferencesSave} disabled={isSavingPrefs}
            className="flex items-center gap-2 px-5 py-2 text-sm font-medium text-white bg-[var(--accent)] rounded hover:brightness-105 transition disabled:opacity-50">
            <Save className="h-4 w-4" /> {isSavingPrefs ? 'Saving…' : 'Save preferences'}
          </button>
        </div>
      </Panel>
    </>
  );
}

function Panel({ icon, title, action, children }: { icon: React.ReactNode; title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg overflow-hidden">
      <div className="px-6 py-3.5 border-b border-[var(--border)] flex items-center justify-between">
        <h2 className="text-[11px] uppercase tracking-wider text-[var(--text-muted)] flex items-center gap-2">
          <span className="text-[var(--text-muted)]">{icon}</span>
          {title}
        </h2>
        {action}
      </div>
      <div className="px-6 py-5">{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-[var(--text-soft)] mb-1.5">{label}</label>
      {children}
    </div>
  );
}
