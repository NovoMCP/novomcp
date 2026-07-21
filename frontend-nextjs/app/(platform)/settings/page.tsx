'use client';

import { useState, useEffect } from 'react';
import { useAuth } from '@/core/auth/provider';
import {
  useUserProfile,
  useUpdateUserProfile,
} from '@/core/api/admin-client';
import LlmProviderCard from '@/components/settings/LlmProviderCard';
import { formatDistanceToNow } from 'date-fns';
import {
  User,
  Mail,
  Building2,
  Briefcase,
  Bell,
  Globe,
  Lock,
  Save,
  Edit2,
  Check,
} from 'lucide-react';

interface UserPreferences {
  emailNotifications: boolean;
  weeklyDigest: boolean;
  theme: 'light' | 'dark' | 'system';
  language: string;
  timezone: string;
}

// Combine first/last name into the single "Full Name" field the UI shows.
function joinName(first?: string | null, last?: string | null): string {
  return [first, last].filter(Boolean).join(' ').trim();
}

// Split a single "Full Name" back into first + last for the API. Lossy for
// multi-part surnames (van der Waals → first="van", last="der Waals") but
// matches the existing single-field UI shape. If a future PR splits the
// form into separate First/Last inputs, this helper goes away.
function splitName(full: string): { first_name: string; last_name: string } {
  const parts = full.trim().split(/\s+/);
  if (parts.length === 0 || parts[0] === '') return { first_name: '', last_name: '' };
  if (parts.length === 1) return { first_name: parts[0], last_name: '' };
  return { first_name: parts[0], last_name: parts.slice(1).join(' ') };
}

export default function SettingsPage() {
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
    organization: user?.orgId || ''
  });

  const [preferences, setPreferences] = useState<UserPreferences>({
    emailNotifications: true,
    weeklyDigest: true,
    theme: 'light',
    language: 'en',
    timezone: 'UTC'
  });

  // Sync profile + preferences state from the server response once it loads.
  // Without this effect the page rendered hardcoded defaults regardless of
  // what the user actually had stored — the original "title reverts on save"
  // bug had two roots: nothing loaded, and nothing saved.
  useEffect(() => {
    if (!profile) return;
    const p = profile as Record<string, any>;
    setProfileData({
      name: joinName(p.first_name, p.last_name) || user?.name || '',
      email: p.email || user?.email || '',
      title: p.job_title || '',
      department: p.department || '',
      organization: user?.orgId || '',
    });
    setPreferences((prev) => ({
      ...prev,
      language: p.language_preference || prev.language,
      timezone: p.timezone || prev.timezone,
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
    } catch (e: any) {
      setSaveError(e?.message || 'Failed to update profile');
    }
  };

  // Key management (Novo Core + Novo Compute) lives on /keys as two
  // distinct top sections — keeps Settings focused on profile and
  // preferences, restores the original two-section layout users had.

  const handlePreferencesSave = async () => {
    setIsSavingPrefs(true);
    setSaveError('');
    try {
      // Language is gated as "Coming Soon" so we only persist timezone for
      // now. Sending the field that's actually editable keeps the request
      // minimal and aligns with what the user can see.
      await updateProfile.mutateAsync({ timezone: preferences.timezone });
      flashMessage('Preferences saved');
    } catch (e: any) {
      setSaveError(e?.message || 'Failed to save preferences');
    } finally {
      setIsSavingPrefs(false);
    }
  };

  const passwordLastChanged = profile?.updated_at
    ? formatDistanceToNow(new Date(profile.updated_at), { addSuffix: true })
    : null;

  const inputClass = "w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] focus:outline-none focus:border-[var(--accent)] transition-colors text-sm";
  const readOnlyClass = "flex items-center gap-3 px-3 py-2 bg-[var(--bg)] border border-[var(--border)]";
  const sectionClass = "bg-[var(--card)] border border-[var(--border)]";
  const sectionHeaderClass = "px-6 py-4 border-b border-[var(--border)] flex items-center";

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1
            className="text-2xl font-semibold text-[var(--text)]"
            style={{ fontFamily: 'var(--serif)' }}
          >
            Settings
          </h1>
          <p className="text-[var(--text-muted)] text-sm">Manage your profile and preferences</p>
        </div>
        {saveMessage && (
          <div className="flex items-center gap-2 px-4 py-2 bg-[var(--success)]/10 border border-[var(--success)]/20">
            <Check className="h-4 w-4 text-[var(--success)]" />
            <span className="text-sm text-[var(--success)]">{saveMessage}</span>
          </div>
        )}
        {saveError && (
          <div className="flex items-center gap-2 px-4 py-2 bg-[var(--destructive)]/10 border border-[var(--destructive)]/20">
            <span className="text-sm text-[var(--destructive)]">{saveError}</span>
          </div>
        )}
      </div>

      {/* Profile */}
      <div className={sectionClass}>
        <div className={`${sectionHeaderClass} justify-between`}>
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center bg-[var(--accent)] text-white">
              <User className="h-4 w-4" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-[var(--text)]">Profile Information</h2>
              <p className="text-xs text-[var(--text-muted)]">Your personal details and role</p>
            </div>
          </div>
          {!isEditingProfile && (
            <button
              onClick={() => setIsEditingProfile(true)}
              className="flex items-center gap-2 px-3 py-1.5 text-sm border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)]/5 transition-colors"
            >
              <Edit2 className="h-3.5 w-3.5" />
              Edit Profile
            </button>
          )}
        </div>

        <div className="p-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Full Name</label>
              {isEditingProfile ? (
                <input
                  type="text"
                  value={profileData.name}
                  onChange={(e) => setProfileData({ ...profileData, name: e.target.value })}
                  className={inputClass}
                />
              ) : (
                <div className={readOnlyClass}>
                  <User className="h-4 w-4 text-[var(--text-muted)]" />
                  <span className="text-sm text-[var(--text)]">{profileData.name}</span>
                </div>
              )}
            </div>
            <div>
              <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Email Address</label>
              <div className={readOnlyClass}>
                <Mail className="h-4 w-4 text-[var(--text-muted)]" />
                <span className="text-sm text-[var(--text-soft)]">{profileData.email}</span>
                <span className="ml-auto text-xs px-2 py-0.5 bg-[var(--bg-warm)] text-[var(--text-muted)]">Verified</span>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Professional Title</label>
              {isEditingProfile ? (
                <input
                  type="text"
                  value={profileData.title}
                  onChange={(e) => setProfileData({ ...profileData, title: e.target.value })}
                  className={inputClass}
                />
              ) : (
                <div className={readOnlyClass}>
                  <Briefcase className="h-4 w-4 text-[var(--text-muted)]" />
                  <span className="text-sm text-[var(--text)]">{profileData.title}</span>
                </div>
              )}
            </div>
            <div>
              <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Department</label>
              {isEditingProfile ? (
                // Free-text input with a datalist of common values as
                // suggestions. The backend column is VARCHAR(255) free-form,
                // so a hardcoded <select> would silently truncate any value
                // outside the option list to the first option ("Research")
                // on the next render and then overwrite the real DB value
                // on save. datalist preserves the discoverability of the
                // common choices without restricting input.
                <>
                  <input
                    type="text"
                    list="department-suggestions"
                    value={profileData.department}
                    onChange={(e) => setProfileData({ ...profileData, department: e.target.value })}
                    placeholder="e.g., Computational Biology"
                    className={inputClass}
                  />
                  <datalist id="department-suggestions">
                    <option value="Research" />
                    <option value="Chemistry" />
                    <option value="Computational Chemistry" />
                    <option value="Computational Biology" />
                    <option value="AI/ML" />
                    <option value="Clinical" />
                    <option value="Operations" />
                    <option value="Discovery" />
                    <option value="Translational" />
                  </datalist>
                </>
              ) : (
                <div className={readOnlyClass}>
                  <Building2 className="h-4 w-4 text-[var(--text-muted)]" />
                  <span className="text-sm text-[var(--text)]">{profileData.department}</span>
                </div>
              )}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Organization ID</label>
            <div className={readOnlyClass}>
              <Building2 className="h-4 w-4 text-[var(--text-muted)]" />
              <span className="text-sm text-[var(--text-soft)] font-mono">{profileData.organization}</span>
            </div>
          </div>

          {isEditingProfile && (
            <div className="flex gap-3 pt-4">
              <button
                onClick={handleProfileSave}
                disabled={updateProfile.isPending}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all duration-[400ms] disabled:opacity-50"
              >
                <Save className="h-4 w-4" />
                {updateProfile.isPending ? 'Saving...' : 'Save Changes'}
              </button>
              <button
                onClick={() => { setIsEditingProfile(false); setSaveError(''); }}
                disabled={updateProfile.isPending}
                className="px-4 py-2 text-sm font-medium text-[var(--text-soft)] border border-[var(--border)] hover:bg-[var(--bg-warm)] transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Workbench AI provider — only surfaced when NovoWorkbench is wired.
          v1.5.x roadmap: Workbench desktop DMG ships as the primary OSS UI.
          Until then this card requires the hosted /org/llm-config endpoint,
          which OSS installs don't run. Guarded so it doesn't render (and
          doesn't 503) in local single-user mode. */}
      {process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true' && <LlmProviderCard />}
      {/* Notifications */}
      <div className={sectionClass}>
        <div className={`${sectionHeaderClass} gap-3 justify-between`}>
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center bg-[var(--accent)] text-white">
              <Bell className="h-4 w-4" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-[var(--text)]">Notifications</h2>
              <p className="text-xs text-[var(--text-muted)]">Control what updates you receive</p>
            </div>
          </div>
          <span className="text-xs font-medium px-2 py-1 bg-[var(--bg-warm)] text-[var(--text-muted)]">Coming Soon</span>
        </div>

        <div className="p-6 space-y-4 opacity-50 pointer-events-none">
          {[
            { key: 'emailNotifications', label: 'Email Notifications', description: 'Receive notifications via email' },
            { key: 'weeklyDigest', label: 'Weekly Digest', description: 'Summary of usage and activity' },
          ].map(({ key, label, description }) => (
            <div key={key} className="flex items-center justify-between py-3 border-b border-[var(--border)] last:border-b-0">
              <div>
                <p className="text-sm font-medium text-[var(--text)]">{label}</p>
                <p className="text-xs text-[var(--text-muted)] mt-1">{description}</p>
              </div>
              <button
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  preferences[key as keyof UserPreferences] ? 'bg-[var(--accent)]' : 'bg-[var(--border)]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    preferences[key as keyof UserPreferences] ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* General Preferences */}
      <div className={sectionClass}>
        <div className={`${sectionHeaderClass} gap-3`}>
          <div className="flex h-9 w-9 items-center justify-center bg-[var(--accent)] text-white">
            <Globe className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-[var(--text)]">General Preferences</h2>
            <p className="text-xs text-[var(--text-muted)]">Customize your experience</p>
          </div>
        </div>

        <div className="p-6 space-y-4">
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-sm font-medium text-[var(--text)]">Language</label>
              <span className="text-xs font-medium px-2 py-0.5 bg-[var(--bg-warm)] text-[var(--text-muted)]">Coming Soon</span>
            </div>
            <select
              value={preferences.language}
              disabled
              className={`${inputClass} opacity-50 cursor-not-allowed`}
            >
              <option value="en">English</option>
              <option value="es">Español</option>
              <option value="fr">Français</option>
              <option value="de">Deutsch</option>
              <option value="zh">中文</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Timezone</label>
            <select
              value={preferences.timezone}
              onChange={(e) => setPreferences({ ...preferences, timezone: e.target.value })}
              className={inputClass}
            >
              <option value="America/New_York">Eastern Time (ET)</option>
              <option value="America/Chicago">Central Time (CT)</option>
              <option value="America/Denver">Mountain Time (MT)</option>
              <option value="America/Los_Angeles">Pacific Time (PT)</option>
              <option value="Europe/London">London (GMT)</option>
              <option value="Europe/Paris">Paris (CET)</option>
              <option value="Asia/Tokyo">Tokyo (JST)</option>
            </select>
          </div>
        </div>
      </div>

      {/* Security — password / MFA. Hosted-only; OSS local single-user mode
          has no login flow, so nothing to secure at this layer. */}
      {process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true' && (
      <div className={sectionClass}>
        <div className={`${sectionHeaderClass} gap-3`}>
          <div className="flex h-9 w-9 items-center justify-center bg-[var(--accent)] text-white">
            <Lock className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-[var(--text)]">Security</h2>
            <p className="text-xs text-[var(--text-muted)]">Manage your account security</p>
          </div>
        </div>

        <div className="p-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between py-3">
            <div>
              <p className="text-sm font-medium text-[var(--text)]">Password</p>
              <p className="text-xs text-[var(--text-muted)] mt-1">
                {passwordLastChanged ? `Last changed ${passwordLastChanged}` : 'Password set'}
              </p>
            </div>
            <button className="px-4 py-2 text-sm font-medium border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)]/5 transition-colors">
              Change Password
            </button>
          </div>
        </div>
      </div>
      )}

      {/* Save */}
      <div className="flex justify-end">
        <button
          onClick={handlePreferencesSave}
          disabled={isSavingPrefs}
          className="flex items-center gap-2 px-6 py-2.5 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all duration-[400ms] disabled:opacity-50"
        >
          <Save className="h-4 w-4" />
          {isSavingPrefs ? 'Saving...' : 'Save Preferences'}
        </button>
      </div>
    </div>
  );
}
