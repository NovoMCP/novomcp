/**
 * Choice — pick-one-from-list provider.
 *
 * Renders a ranked list of options as clickable cards. Each card has a
 * title, optional subtitle, metric, and tags. On click, the option's
 * `onSelect` receives the full option so the consumer can route (call a
 * tool, send a message, open a link, etc.).
 *
 * Usage:
 *   <Choice
 *     title="Which target should we validate?"
 *     options={targets.map((t, i) => ({
 *       id: t.gene,
 *       title: t.gene,
 *       subtitle: t.description,
 *       metric: { label: "Score", value: t.score.toFixed(2) },
 *       rank: i + 1,
 *       tags: t.known_drugs > 0 ? [{ label: `${t.known_drugs} drugs`, variant: "success" }] : [],
 *     }))}
 *     onSelect={(opt) => sendMessage({ role: "user", content: [{ type: "text", text: `Validate ${opt.id}.` }] })}
 *   />
 */
import { useState } from "react";

// =============================================================================
// Types
// =============================================================================

export type ChoiceTagVariant = "success" | "warning" | "danger" | "neutral";

export interface ChoiceTag {
  label: string;
  variant?: ChoiceTagVariant;
}

export interface ChoiceMetric {
  label: string;
  value: string | number;
  /** Tint the value (e.g. green for a good score). */
  tone?: "success" | "warning" | "danger" | "accent";
}

export interface ChoiceOption {
  /** Stable identifier — returned on select. */
  id: string;
  title: string;
  subtitle?: string;
  /** Optional ordinal shown as a subtle numeric prefix. */
  rank?: number;
  /** Primary numeric displayed on the right (score, confidence, etc.). */
  metric?: ChoiceMetric;
  /** Small chips below the title. */
  tags?: ChoiceTag[];
  /** Body text. */
  description?: string;
}

export interface ChoiceProps {
  title: string;
  description?: string;
  options: ChoiceOption[];
  onSelect: (option: ChoiceOption) => void | Promise<void>;
  /** Called when user wants to skip / dismiss. */
  onCancel?: () => void | Promise<void>;
  cancelLabel?: string;
  /** External resolution override (viewer replaces the UI after selection). */
  resolvedId?: string | null;
}

// =============================================================================
// Helpers
// =============================================================================

function tagColors(variant: ChoiceTagVariant = "neutral") {
  switch (variant) {
    case "success":
      return { bg: "var(--success-bg)", fg: "var(--success)" };
    case "warning":
      return { bg: "var(--warning-bg)", fg: "var(--warning)" };
    case "danger":
      return { bg: "var(--danger-bg)", fg: "var(--danger)" };
    default:
      return { bg: "var(--bg-warm)", fg: "var(--text-soft)" };
  }
}

function metricColor(tone?: ChoiceMetric["tone"]) {
  switch (tone) {
    case "success":
      return "var(--success)";
    case "warning":
      return "var(--warning)";
    case "danger":
      return "var(--danger)";
    case "accent":
      return "var(--accent)";
    default:
      return "var(--text)";
  }
}

// =============================================================================
// Card
// =============================================================================

function OptionCard({
  option,
  selected,
  disabled,
  onClick,
}: {
  option: ChoiceOption;
  selected: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const [hover, setHover] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        padding: 14,
        background: selected ? "var(--success-bg)" : "var(--bg-card)",
        border: `1px solid ${
          selected ? "var(--success)" : hover && !disabled ? "var(--accent)" : "var(--border)"
        }`,
        borderRadius: 2,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled && !selected ? 0.5 : 1,
        transition: "all 200ms var(--ease)",
        fontFamily: "var(--font-sans)",
        color: "inherit",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 2 }}>
            {option.rank !== undefined && (
              <span
                style={{
                  fontSize: 11,
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-muted)",
                  flexShrink: 0,
                }}
              >
                #{option.rank}
              </span>
            )}
            <div
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: "var(--text)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {option.title}
            </div>
          </div>

          {option.subtitle && (
            <div
              style={{
                fontSize: 11,
                color: "var(--text-muted)",
                marginBottom: option.tags?.length || option.description ? 8 : 0,
              }}
            >
              {option.subtitle}
            </div>
          )}

          {option.tags && option.tags.length > 0 && (
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: option.description ? 8 : 0 }}>
              {option.tags.map((t, i) => {
                const c = tagColors(t.variant);
                return (
                  <span
                    key={i}
                    style={{
                      fontSize: 10,
                      padding: "2px 8px",
                      background: c.bg,
                      color: c.fg,
                      borderRadius: 2,
                      fontWeight: 500,
                    }}
                  >
                    {t.label}
                  </span>
                );
              })}
            </div>
          )}

          {option.description && (
            <div style={{ fontSize: 12, color: "var(--text-soft)", lineHeight: 1.5 }}>
              {option.description}
            </div>
          )}
        </div>

        {option.metric && (
          <div style={{ textAlign: "right", flexShrink: 0 }}>
            <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
              {option.metric.label}
            </div>
            <div
              style={{
                fontSize: 18,
                fontFamily: "var(--font-mono)",
                fontWeight: 600,
                color: metricColor(option.metric.tone),
                marginTop: 2,
              }}
            >
              {option.metric.value}
            </div>
          </div>
        )}
      </div>
    </button>
  );
}

// =============================================================================
// Component
// =============================================================================

export function Choice({
  title,
  description,
  options,
  onSelect,
  onCancel,
  cancelLabel = "Skip",
  resolvedId = null,
}: ChoiceProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const effectiveSelected = resolvedId ?? selectedId;

  const handleSelect = async (option: ChoiceOption) => {
    if (working) return;
    setSelectedId(option.id);
    setWorking(true);
    try {
      await onSelect(option);
    } catch {
      setSelectedId(null);
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">{title}</div>

      {description && (
        <div style={{ fontSize: 12, color: "var(--text-soft)", marginBottom: 12, lineHeight: 1.5 }}>
          {description}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {options.map((opt) => (
          <OptionCard
            key={opt.id}
            option={opt}
            selected={effectiveSelected === opt.id}
            disabled={working && effectiveSelected !== opt.id}
            onClick={() => handleSelect(opt)}
          />
        ))}
      </div>

      {onCancel && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: "1px solid var(--border)",
            display: "flex",
            justifyContent: "flex-end",
          }}
        >
          <button
            type="button"
            className="btn"
            onClick={() => void onCancel()}
            disabled={working}
          >
            {cancelLabel}
          </button>
        </div>
      )}
    </div>
  );
}
