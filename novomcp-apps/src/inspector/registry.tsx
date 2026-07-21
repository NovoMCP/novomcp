/**
 * Registry of everything the inspector can mount.
 *
 * Two kinds:
 *   - "provider": our reusable interaction components (Approval/FormInput/Choice)
 *   - "viewer": full MCP viewer components fed mock ViewProps
 *
 * Each entry carries its own render function so fixtures can pass tailored
 * props / mock config.
 */
import type { ReactNode } from "react";
import type { LogSink, MockConfig } from "./mock-view-props.ts";

export type EntryKind = "provider" | "viewer";

export interface FixtureContext {
  log: LogSink;
  mockConfig: MockConfig;
}

export interface RegistryEntry {
  id: string;
  kind: EntryKind;
  title: string;
  subtitle?: string;
  fixtures: Fixture[];
}

export interface Fixture {
  id: string;
  label: string;
  notes?: string;
  render: (ctx: FixtureContext) => ReactNode;
}

import { approvalFixtures } from "./fixtures/approval-fixtures.tsx";
import { formInputFixtures } from "./fixtures/form-input-fixtures.tsx";
import { choiceFixtures } from "./fixtures/choice-fixtures.tsx";
import { validateTargetFixtures } from "./fixtures/validate-target-fixtures.tsx";
import { dockingViewerFixtures } from "./fixtures/docking-viewer-fixtures.tsx";
import { stratifyPatientsFixtures } from "./fixtures/stratify-patients-fixtures.tsx";
import { targetDiscoveryFixtures } from "./fixtures/target-discovery-fixtures.tsx";

export const REGISTRY: RegistryEntry[] = [
  {
    id: "approval",
    kind: "provider",
    title: "ApprovalPrompt",
    subtitle: "primitive — no current consumer (§11.2)",
    fixtures: approvalFixtures,
  },
  {
    id: "form-input",
    kind: "provider",
    title: "FormInput",
    subtitle: "primitive — no current consumer (§11.3)",
    fixtures: formInputFixtures,
  },
  {
    id: "choice",
    kind: "provider",
    title: "Choice",
    subtitle: "wired to target_discovery",
    fixtures: choiceFixtures,
  },
  {
    id: "validate-target",
    kind: "viewer",
    title: "validate_target",
    subtitle: "adversarial target checkpoint",
    fixtures: validateTargetFixtures,
  },
  {
    id: "docking-viewer",
    kind: "viewer",
    title: "dock_molecules",
    subtitle: "two-phase estimate + results",
    fixtures: dockingViewerFixtures,
  },
  {
    id: "stratify-patients",
    kind: "viewer",
    title: "stratify_patients",
    subtitle: "pharmacogenomic stratification",
    fixtures: stratifyPatientsFixtures,
  },
  {
    id: "target-discovery",
    kind: "viewer",
    title: "target_discovery",
    subtitle: "Choice-driven next-step handoff",
    fixtures: targetDiscoveryFixtures,
  },
];
