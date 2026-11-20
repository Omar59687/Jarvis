import test from "node:test";
import assert from "node:assert/strict";
import { chatInputPlaceholder, memoryProposalPayload, memoryProposalError } from "../prototypes/m03_b1/chat-ui-state.mjs";

test("chat placeholder follows pairing, general chat, workspace, and Arabic state", () => {
  assert.match(chatInputPlaceholder({ authenticated: false, workspaceId: null, language: "en" }), /Pair this browser/);
  assert.match(chatInputPlaceholder({ authenticated: true, workspaceId: null, language: "en" }), /general question/);
  assert.match(chatInputPlaceholder({ authenticated: true, workspaceId: "ws-a", language: "en" }), /selected workspace/);
  assert.match(chatInputPlaceholder({ authenticated: true, workspaceId: "ws-a", language: "ar" }), /مساحة العمل/);
});

test("memory proposal payload sends the exact selected D label and evidence handle", () => {
  assert.deepEqual(memoryProposalPayload({
    content: "Atlas Lantern launches April 12, 2031.",
    userAuthored: false,
    workspaceId: "ws-atlas-acceptance",
    evidenceRequestId: "opaque-handle",
    citationLabels: ["D1"],
  }), {
    content: "Atlas Lantern launches April 12, 2031.",
    user_authored: false,
    expected_workspace_id: "ws-atlas-acceptance",
    evidence_request_id: "opaque-handle",
    citation_labels: ["D1"],
  });
  assert.equal(memoryProposalPayload({
    content: "My note.", userAuthored: true, workspaceId: "ws-a",
    evidenceRequestId: "ignored", citationLabels: ["D1"],
  }).evidence_request_id, undefined);
});

test("memory proposal errors are actionable and localized", () => {
  assert.match(memoryProposalError("authentication_required", "en"), /Pair this browser/);
  assert.match(memoryProposalError("evidence_request_expired", "ar"), /انتهت صلاحية/);
  assert.match(memoryProposalError("evidence_source_changed", "en"), /Ask Jarvis again/);
  assert.equal(memoryProposalError("unknown_internal_error", "en"), null);
});
