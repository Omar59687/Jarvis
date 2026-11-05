import test from "node:test";
import assert from "node:assert/strict";
import { NODES, EDGES, connectedNodeIds, searchNodeIds, classifyNode, createFrameDriver } from "../prototypes/m03_a/galaxy-model.mjs";

test("selecting a node highlights only that node and its direct connections", () => {
  assert.deepEqual([...connectedNodeIds(EDGES, "atlas")].sort(), ["atlas", "folder", "memory", "project"]);
  assert.deepEqual([...connectedNodeIds(EDGES, null)], []);
  assert.equal(connectedNodeIds(EDGES, "missing").has("missing"), true);
});

test("search filters only the synthetic graph in either supported language", () => {
  assert.deepEqual([...searchNodeIds(NODES, "folder", "en")].sort(), ["archive", "folder"]);
  assert.deepEqual([...searchNodeIds(NODES, "ذاكرة", "ar")], ["memory"]);
  assert.equal(searchNodeIds(NODES, "no such sample", "en").size, 0);
  assert.equal(searchNodeIds(NODES, "", "en").size, NODES.length);
});

test("details lookup only resolves known synthetic nodes", () => {
  assert.equal(classifyNode("memory").source.ar, "ذاكرة معتمدة اصطناعية");
  assert.equal(classifyNode("private-user-data"), null);
});

test("reduced-motion graph interactions redraw on demand without an animation loop", () => {
  let draws = 0;
  let nextFrame = 0;
  const pending = new Map();
  const driver = createFrameDriver(() => { draws += 1; }, {
    requestFrame(callback) { const id = ++nextFrame; pending.set(id, callback); return id; },
    cancelFrame(id) { pending.delete(id); }
  });

  driver.setReducedMotion(true);
  assert.equal(draws, 1);
  assert.equal(pending.size, 0);
  driver.invalidate(); // node drag, search filter, or localized label update
  assert.equal(draws, 2);
  assert.equal(pending.size, 0);

  driver.setReducedMotion(false);
  assert.equal(pending.size, 1);
  driver.invalidate();
  assert.equal(pending.size, 1);
  const [frameId, callback] = pending.entries().next().value;
  pending.delete(frameId);
  callback();
  assert.equal(draws, 4);
  assert.equal(pending.size, 1);

  driver.setReducedMotion(true);
  assert.equal(draws, 5);
  assert.equal(pending.size, 0);
  driver.invalidate();
  assert.equal(draws, 6);
  assert.equal(pending.size, 0);
  driver.dispose();
});
