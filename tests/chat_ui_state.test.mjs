import test from "node:test";
import assert from "node:assert/strict";
import { chatInputPlaceholder } from "../prototypes/m03_b1/chat-ui-state.mjs";

test("chat placeholder follows pairing, general chat, workspace, and Arabic state", () => {
  assert.match(chatInputPlaceholder({ authenticated: false, workspaceId: null, language: "en" }), /Pair this browser/);
  assert.match(chatInputPlaceholder({ authenticated: true, workspaceId: null, language: "en" }), /general question/);
  assert.match(chatInputPlaceholder({ authenticated: true, workspaceId: "ws-a", language: "en" }), /selected workspace/);
  assert.match(chatInputPlaceholder({ authenticated: true, workspaceId: "ws-a", language: "ar" }), /مساحة العمل/);
});
