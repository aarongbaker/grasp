import {
  ApiError,
  isBillingRecoverySessionResponse,
  isBillingRecoveryStatusResponse,
  isBillingSetupSessionResponse,
  isBillingSetupStatusResponse,
  isSessionRunBlockedResponse,
} from '../api/client';
import type {
  AuthoredRecipeValidationDetail,
  AuthoredRecipeValidationIssue,
  BillingRecoverySessionResponse,
  BillingRecoveryStatusResponse,
  BillingSetupSessionResponse,
  BillingSetupStatusResponse,
  SessionRunBlockedResponse,
} from '../types/api';

export function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.kind === 'startup-config') {
      return `The API responded with a startup/configuration failure: ${error.detail}`;
    }
    return error.detail;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function getSessionRunBlockedDetail(error: unknown): SessionRunBlockedResponse | null {
  if (!(error instanceof ApiError) || error.kind !== 'session-run-blocked') {
    return null;
  }

  return isSessionRunBlockedResponse(error.payload) ? error.payload : null;
}

export function getBillingSetupSessionDetail(error: unknown): BillingSetupSessionResponse | null {
  if (!(error instanceof ApiError)) {
    return null;
  }

  return isBillingSetupSessionResponse(error.payload) ? error.payload : null;
}

export function getBillingSetupStatusDetail(error: unknown): BillingSetupStatusResponse | null {
  if (!(error instanceof ApiError)) {
    return null;
  }

  return isBillingSetupStatusResponse(error.payload) ? error.payload : null;
}

export function getBillingRecoveryStatusDetail(error: unknown): BillingRecoveryStatusResponse | null {
  if (!(error instanceof ApiError)) {
    return null;
  }

  return isBillingRecoveryStatusResponse(error.payload) ? error.payload : null;
}

export function getBillingRecoverySessionDetail(error: unknown): BillingRecoverySessionResponse | null {
  if (!(error instanceof ApiError)) {
    return null;
  }

  return isBillingRecoverySessionResponse(error.payload) ? error.payload : null;
}

export function getAuthoredRecipeValidationDetail(error: unknown): AuthoredRecipeValidationDetail | null {
  if (!(error instanceof ApiError) || error.kind !== 'authored-validation') {
    return null;
  }

  const payload = error.payload;
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as { detail?: unknown }).detail)) {
    return null;
  }

  return payload as AuthoredRecipeValidationDetail;
}

export interface AuthoredRecipeFieldGuidance {
  path: string;
  message: string;
}

export interface AuthoredRecipeValidationGuidance {
  summary: string;
  fields: AuthoredRecipeFieldGuidance[];
}

function formatFieldPath(loc: Array<string | number>): string {
  return loc
    .slice(1)
    .map((segment) => String(segment))
    .join('.');
}

function summarizeDependencyTarget(message: string): string | null {
  const missingTarget = message.match(/depends on '([^']+)' which does not exist/i);
  if (missingTarget) {
    return missingTarget[1];
  }

  const selfReference = message.match(/depends on itself/i);
  if (selfReference) {
    return 'itself';
  }

  return null;
}

function translateAuthoredValidationIssue(issue: AuthoredRecipeValidationIssue): AuthoredRecipeFieldGuidance {
  const path = formatFieldPath(issue.loc);
  const location = issue.loc;
  const stepIndex = typeof location[2] === 'number' ? Number(location[2]) + 1 : null;
  const dependencyIndex = typeof location[4] === 'number' ? Number(location[4]) + 1 : null;
  const field = location[location.length - 1];

  if (location[1] === 'steps' && field === 'dependencies' && stepIndex) {
    return {
      path,
      message: `Step ${stepIndex} needs a clearer handoff before service. Double-check which earlier beat must finish first.`,
    };
  }

  if (location[1] === 'steps' && field === 'step_id' && stepIndex) {
    const dependencyTarget = summarizeDependencyTarget(issue.msg);
    const dependencyLabel = dependencyIndex ? `dependency ${dependencyIndex}` : 'one dependency';

    if (dependencyTarget === 'itself') {
      return {
        path,
        message: `Step ${stepIndex} cannot wait on itself. Link it to an earlier beat instead.`,
      };
    }

    if (dependencyTarget) {
      return {
        path,
        message: `Step ${stepIndex} points ${dependencyLabel} at a beat this draft cannot see yet. Re-link it to an earlier visible beat.`,
      };
    }

    return {
      path,
      message: `Step ${stepIndex} has a handoff that no longer lines up with the visible beats. Re-link it to an earlier step.`,
    };
  }

  if (location[1] === 'steps' && field === 'duration_max' && stepIndex) {
    return {
      path,
      message: `Step ${stepIndex} has an outer time edge that ends before the expected working time. Make the outer edge the same length or longer.`,
    };
  }

  if (location[1] === 'steps' && field === 'prep_ahead_window' && stepIndex) {
    return {
      path,
      message: `Step ${stepIndex} is marked as work you can do ahead, but the make-ahead window is missing. Say how far before service this beat can be handled.`,
    };
  }

  if (location[1] === 'steps' && field === 'prep_ahead_notes' && stepIndex) {
    return {
      path,
      message: `Step ${stepIndex} can be done ahead, but the recovery note is missing. Tell the next cook how to bring it back for service.`,
    };
  }

  if (location[1] === 'steps' && field === 'title' && stepIndex) {
    return {
      path,
      message: `Step ${stepIndex} still needs a beat name the kitchen can recognize at a glance.`,
    };
  }

  if (location[1] === 'steps' && field === 'instruction' && stepIndex) {
    return {
      path,
      message: `Step ${stepIndex} needs a clearer instruction so the handoff is workable on the line.`,
    };
  }

  if (location[1] === 'steps' && stepIndex) {
    return {
      path,
      message: `Step ${stepIndex} needs another pass before this draft can save cleanly. ${issue.msg.replace(/^Value error,\s*/i, '')}`,
    };
  }

  if (location[1] === 'ingredients') {
    const ingredientIndex = typeof location[2] === 'number' ? Number(location[2]) + 1 : null;
    return {
      path,
      message: ingredientIndex
        ? `Ingredient ${ingredientIndex} is incomplete. Add the missing kitchen detail before saving.`
        : 'One ingredient line is incomplete. Add the missing kitchen detail before saving.',
    };
  }

  if (field === 'title') {
    return { path, message: 'Give the dish a clear title before saving this draft.' };
  }

  if (field === 'description') {
    return { path, message: 'Describe how the dish should land on the pass before saving.' };
  }

  if (field === 'cuisine') {
    return { path, message: 'Name the cuisine or lens so the draft keeps its point of view.' };
  }

  if (String(field).includes('yield')) {
    return { path, message: 'The yield needs to read like a real service count before this recipe can save.' };
  }

  return {
    path,
    message: issue.msg.replace(/^Value error,\s*/i, ''),
  };
}

export function translateAuthoredRecipeValidationDetail(
  detail: AuthoredRecipeValidationDetail,
): AuthoredRecipeValidationGuidance {
  const fields = detail.detail.map(translateAuthoredValidationIssue);
  return {
    summary:
      fields.length === 1
        ? 'One part of the draft still needs kitchen detail before it can save.'
        : `${fields.length} parts of the draft need another pass before this recipe can save.`,
    fields,
  };
}
