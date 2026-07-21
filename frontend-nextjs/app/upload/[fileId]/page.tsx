'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams } from 'next/navigation';
import { Upload, CheckCircle2, Copy, Clock, AlertCircle, FileUp } from 'lucide-react';

type UploadState = 'loading' | 'ready' | 'uploading' | 'confirming' | 'done' | 'error' | 'expired';

export default function UploadPage() {
  const { fileId } = useParams<{ fileId: string }>();
  const [state, setState] = useState<UploadState>('loading');
  const [uploadUrl, setUploadUrl] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [fileName, setFileName] = useState('');
  const [fileSize, setFileSize] = useState(0);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Extract the S3 presigned upload URL from the hash fragment.
  // The engine embeds it as: /upload/f-xxx#u=<base64-encoded URL>
  const getSasUrl = useCallback((): string | null => {
    if (typeof window === 'undefined') return null;
    const hash = window.location.hash;
    if (!hash.includes('u=')) return null;
    try {
      const encoded = hash.split('u=')[1];
      return atob(decodeURIComponent(encoded));
    } catch {
      return null;
    }
  }, []);

  // Resolve the presigned upload URL by file_id on load (short-link model). The
  // link no longer carries the URL in its fragment — that ~700-char SigV4 URL got
  // truncated by LLMs surfacing the link (broke uploads after Azure→AWS). Falls
  // back to the hash fragment for any legacy links still in flight.
  useEffect(() => {
    if (!fileId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/upload-url/${fileId}`, { cache: 'no-store' });
        if (res.ok) {
          const data = await res.json();
          if (!cancelled && data?.upload_url) {
            setUploadUrl(data.upload_url);
            setState('ready');
            return;
          }
        }
      } catch {
        // fall through to the hash fallback
      }
      const fromHash = getSasUrl();
      if (cancelled) return;
      if (fromHash) {
        setUploadUrl(fromHash);
        setState('ready');
      } else {
        setState('error');
        setError('Upload link not found or already used. Go back to your chat and request a new upload link.');
      }
    })();
    return () => { cancelled = true; };
  }, [fileId, getSasUrl]);

  const handleFile = useCallback(async (file: File) => {
    const sasUrl = uploadUrl ?? getSasUrl();
    if (!sasUrl) {
      setState('error');
      setError('Upload URL missing or expired. Go back to your chat and request a new upload link.');
      return;
    }

    setFileName(file.name);
    setFileSize(file.size);
    setState('uploading');
    setProgress(0);

    try {
      // Upload with progress tracking via XMLHttpRequest.
      // S3 presigned PUT URLs don't need (and reject any) header that wasn't
      // pinned at signing time. The URL is generated with only Bucket+Key in
      // Params (core/file_intelligence.py), so we send no extra headers —
      // the bucket's default server-side encryption applies.
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('PUT', sasUrl, true);

        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            setProgress(Math.round((e.loaded / e.total) * 100));
          }
        };

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve();
          } else if (xhr.status === 403) {
            reject(new Error('Upload URL has expired. Request a new link from your chat.'));
          } else {
            reject(new Error(`Upload failed (HTTP ${xhr.status})`));
          }
        };

        xhr.onerror = () => reject(new Error('Network error — check your connection and try again.'));
        xhr.send(file);
      });

      setState('confirming');
      setProgress(100);

      // Brief pause for S3 to register the upload before get_file_status
      // can flip pending → uploaded (the backend HEADs the key on poll).
      await new Promise((r) => setTimeout(r, 1500));
      setState('done');
    } catch (e) {
      setState('error');
      setError(e instanceof Error ? e.message : 'Upload failed');
    }
  }, [uploadUrl, getSasUrl]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const onFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const copyFileId = useCallback(() => {
    navigator.clipboard.writeText(fileId);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [fileId]);

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--text)] flex flex-col items-center justify-center p-6">
      <div className="w-full max-w-lg">
        {/* Header */}
        <div className="mb-8 text-center">
          <p className="text-[10px] font-medium uppercase tracking-widest text-[var(--text-muted)] mb-1">
            Novo<span className="text-[var(--accent)]">MCP</span>
          </p>
          <h1 className="text-2xl font-semibold text-[var(--text)]">File Upload</h1>
          <p className="text-sm text-[var(--text-soft)] mt-2">
            Upload your file to use in NovoMCP tools
          </p>
        </div>

        {/* File ID display */}
        <div className="mb-6 flex items-center justify-center gap-2">
          <span className="text-xs text-[var(--text-muted)]">File ID:</span>
          <code className="font-mono text-sm text-[var(--accent)] bg-[var(--bg-warm)] px-3 py-1">
            {fileId}
          </code>
          <button
            onClick={copyFileId}
            className="p-1 text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
            title="Copy file ID"
          >
            {copied ? <CheckCircle2 className="h-4 w-4 text-[var(--success)]" /> : <Copy className="h-4 w-4" />}
          </button>
        </div>

        {/* Upload states */}
        {state === 'loading' && (
          <div className="border border-[var(--border)] bg-[var(--bg-warm)] p-8 text-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--accent)] mx-auto mb-4" />
            <p className="text-sm text-[var(--text-soft)]">Preparing your upload&hellip;</p>
          </div>
        )}

        {state === 'ready' && (
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            className="border-2 border-dashed border-[var(--border)] hover:border-[var(--accent)] bg-[var(--bg-warm)] p-12 text-center cursor-pointer transition-colors"
          >
            <Upload className="h-10 w-10 text-[var(--text-muted)] mx-auto mb-4" />
            <p className="text-sm text-[var(--text-soft)] mb-1">
              Drag and drop your file here, or click to browse
            </p>
            <p className="text-xs text-[var(--text-muted)]">
              QM logs (.log, .out) &middot; PDB (.pdb, .cif) &middot; Libraries (.sdf, .csv)
            </p>
            <input
              ref={inputRef}
              type="file"
              onChange={onFileSelect}
              className="hidden"
            />
          </div>
        )}

        {state === 'uploading' && (
          <div className="border border-[var(--border)] bg-[var(--bg-warm)] p-8">
            <div className="flex items-center gap-3 mb-4">
              <FileUp className="h-5 w-5 text-[var(--accent)] animate-pulse" />
              <div className="flex-1">
                <p className="text-sm font-medium text-[var(--text)]">{fileName}</p>
                <p className="text-xs text-[var(--text-muted)]">{formatSize(fileSize)}</p>
              </div>
              <span className="font-mono text-sm text-[var(--accent)]">{progress}%</span>
            </div>
            <div className="h-1.5 bg-[var(--bg)]">
              <div
                className="h-full bg-[var(--accent)] transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-xs text-[var(--text-muted)] mt-3">
              Uploading directly to secure storage...
            </p>
          </div>
        )}

        {state === 'confirming' && (
          <div className="border border-[var(--border)] bg-[var(--bg-warm)] p-8 text-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--accent)] mx-auto mb-4" />
            <p className="text-sm text-[var(--text-soft)]">Confirming upload...</p>
          </div>
        )}

        {state === 'done' && (
          <div className="border border-[var(--success)]/30 bg-[var(--success)]/5 p-8">
            <div className="text-center mb-6">
              <CheckCircle2 className="h-12 w-12 text-[var(--success)] mx-auto mb-3" />
              <h2 className="text-lg font-semibold text-[var(--text)]">Upload Complete</h2>
              <p className="text-sm text-[var(--text-soft)] mt-1">
                {fileName} &middot; {formatSize(fileSize)}
              </p>
            </div>

            {/* Copy section */}
            <div className="bg-[var(--bg)] border border-[var(--border)] p-4 mb-4">
              <p className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2">
                Paste this file ID back in your chat
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 font-mono text-base text-[var(--accent)] select-all">
                  {fileId}
                </code>
                <button
                  onClick={copyFileId}
                  className="px-3 py-1.5 bg-[var(--accent)] text-white text-xs font-medium hover:bg-[var(--accent)]/90 transition-colors"
                >
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
            </div>

            <p className="text-xs text-[var(--text-muted)] text-center">
              Your AI assistant will use this file ID to process the uploaded data.
              The file is securely stored and linked to your account.
            </p>
          </div>
        )}

        {state === 'error' && (
          <div className="border border-[var(--destructive)]/30 bg-[var(--destructive)]/5 p-8">
            <div className="flex items-start gap-3">
              <AlertCircle className="h-5 w-5 text-[var(--destructive)] mt-0.5 shrink-0" />
              <div>
                <h3 className="text-sm font-medium text-[var(--text)]">Upload Failed</h3>
                <p className="text-sm text-[var(--text-soft)] mt-1">{error}</p>
                <button
                  onClick={() => { setState('ready'); setError(''); }}
                  className="mt-3 px-4 py-1.5 bg-[var(--accent)] text-white text-xs font-medium hover:bg-[var(--accent)]/90"
                >
                  Try Again
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="mt-8 flex items-center justify-center gap-4 text-[10px] text-[var(--text-muted)]">
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" /> Upload link expires in 30 minutes
          </span>
          <span>&middot;</span>
          <span>Encrypted in transit and at rest</span>
        </div>
      </div>
    </div>
  );
}
