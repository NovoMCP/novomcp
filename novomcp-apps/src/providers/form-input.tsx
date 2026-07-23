/**
 * FormInput — schema-driven form provider for parameter collection.
 *
 * Usage:
 *   <FormInput
 *     title="Stratify Patients"
 *     fields={[
 *       { name: "target_gene", label: "Target gene", type: "text", required: true, placeholder: "EGFR" },
 *       { name: "indication", label: "Indication", type: "text", placeholder: "NSCLC" },
 *       { name: "ancestry_focus", label: "Ancestry focus", type: "select",
 *         options: [{ value: "global", label: "Global" }, { value: "EAS", label: "East Asian" }] },
 *     ]}
 *     onSubmit={async (values) => { await callServerTool({ name: "stratify_patients", arguments: values }); }}
 *   />
 *
 * Keeps wire-format separate from UI: all values are strings until submitted,
 * then coerced by field type. Inline validation runs on blur + submit.
 */
import { useState, FormEvent } from "react";

// =============================================================================
// Types
// =============================================================================

export type FormFieldType = "text" | "number" | "select" | "multiline" | "smiles";

export interface FormSelectOption {
  value: string;
  label: string;
}

export interface FormField {
  name: string;
  label: string;
  type: FormFieldType;
  required?: boolean;
  placeholder?: string;
  help?: string;
  defaultValue?: string | number;
  /** For `select`. */
  options?: FormSelectOption[];
  /** For `number`. */
  min?: number;
  max?: number;
  step?: number;
  /** Custom validator. Return error string or null. */
  validate?: (raw: string) => string | null;
}

export interface FormInputProps {
  title: string;
  description?: string;
  fields: FormField[];
  onSubmit: (values: Record<string, string | number>) => void | Promise<void>;
  onCancel?: () => void | Promise<void>;
  submitLabel?: string;
  cancelLabel?: string;
  /** External resolution override — viewer replaces form with result UI. */
  resolved?: boolean;
}

// =============================================================================
// Validation + coercion
// =============================================================================

function validateField(field: FormField, raw: string): string | null {
  if (field.required && !raw.trim()) return `${field.label} is required`;
  if (!raw.trim() && !field.required) return null;

  if (field.type === "number") {
    const n = Number(raw);
    if (Number.isNaN(n)) return `${field.label} must be a number`;
    if (field.min !== undefined && n < field.min) return `${field.label} must be ≥ ${field.min}`;
    if (field.max !== undefined && n > field.max) return `${field.label} must be ≤ ${field.max}`;
  }

  if (field.validate) return field.validate(raw);
  return null;
}

function coerce(field: FormField, raw: string): string | number {
  if (field.type === "number") return Number(raw);
  return raw.trim();
}

// =============================================================================
// Field renderer
// =============================================================================

function FieldRenderer({
  field,
  value,
  error,
  onChange,
  onBlur,
}: {
  field: FormField;
  value: string;
  error: string | null;
  onChange: (v: string) => void;
  onBlur: () => void;
}) {
  const inputStyle = {
    width: "100%",
    padding: "8px 10px",
    fontSize: 13,
    fontFamily: field.type === "smiles" ? "var(--font-mono)" : "var(--font-sans)",
    background: "var(--bg-warm)",
    color: "var(--text)",
    border: `1px solid ${error ? "var(--danger)" : "var(--border)"}`,
    borderRadius: 2,
    outline: "none",
  };

  return (
    <div style={{ marginBottom: 12 }}>
      <label
        style={{
          display: "block",
          fontSize: 11,
          fontWeight: 500,
          color: "var(--text-soft)",
          marginBottom: 4,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        {field.label}
        {field.required && <span style={{ color: "var(--danger)", marginLeft: 4 }}>*</span>}
      </label>

      {field.type === "select" ? (
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          style={inputStyle}
        >
          <option value="">— Select —</option>
          {field.options?.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : field.type === "multiline" ? (
        <textarea
          value={value}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          rows={3}
          style={{ ...inputStyle, resize: "vertical" }}
        />
      ) : (
        <input
          type={field.type === "number" ? "number" : "text"}
          value={value}
          placeholder={field.placeholder}
          min={field.min}
          max={field.max}
          step={field.step}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          style={inputStyle}
        />
      )}

      {(error || field.help) && (
        <div
          style={{
            fontSize: 10,
            marginTop: 4,
            color: error ? "var(--danger)" : "var(--text-muted)",
          }}
        >
          {error || field.help}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Component
// =============================================================================

export function FormInput({
  title,
  description,
  fields,
  onSubmit,
  onCancel,
  submitLabel = "Submit",
  cancelLabel = "Cancel",
  resolved = false,
}: FormInputProps) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      fields.map((f) => [f.name, f.defaultValue !== undefined ? String(f.defaultValue) : ""])
    )
  );
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [working, setWorking] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const setField = (name: string, value: string) => {
    setValues((v) => ({ ...v, [name]: value }));
    if (errors[name]) setErrors((e) => ({ ...e, [name]: null }));
  };

  const validateOne = (field: FormField) => {
    const err = validateField(field, values[field.name] ?? "");
    setErrors((e) => ({ ...e, [field.name]: err }));
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const nextErrors: Record<string, string | null> = {};
    let hasError = false;
    for (const f of fields) {
      const err = validateField(f, values[f.name] ?? "");
      if (err) hasError = true;
      nextErrors[f.name] = err;
    }
    setErrors(nextErrors);
    if (hasError) return;

    const coerced: Record<string, string | number> = {};
    for (const f of fields) {
      const raw = (values[f.name] ?? "").trim();
      if (!raw) continue;
      coerced[f.name] = coerce(f, raw);
    }

    setWorking(true);
    setSubmitError(null);
    try {
      await onSubmit(coerced);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setWorking(false);
    }
  };

  if (resolved) return null;

  return (
    <form className="panel" onSubmit={handleSubmit}>
      <div className="panel-title">{title}</div>

      {description && (
        <div style={{ fontSize: 12, color: "var(--text-soft)", marginBottom: 14, lineHeight: 1.5 }}>
          {description}
        </div>
      )}

      {fields.map((f) => (
        <FieldRenderer
          key={f.name}
          field={f}
          value={values[f.name] ?? ""}
          error={errors[f.name] ?? null}
          onChange={(v) => setField(f.name, v)}
          onBlur={() => validateOne(f)}
        />
      ))}

      {submitError && (
        <div
          style={{
            padding: "8px 10px",
            background: "var(--danger-bg)",
            color: "var(--danger)",
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            borderRadius: 2,
            marginBottom: 10,
          }}
        >
          {submitError}
        </div>
      )}

      <div
        style={{
          paddingTop: 12,
          borderTop: "1px solid var(--border)",
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
        }}
      >
        {onCancel && (
          <button
            type="button"
            className="btn"
            onClick={() => void onCancel()}
            disabled={working}
          >
            {cancelLabel}
          </button>
        )}
        <button type="submit" className="btn active" disabled={working}>
          {working ? "Submitting…" : submitLabel}
        </button>
      </div>
    </form>
  );
}
