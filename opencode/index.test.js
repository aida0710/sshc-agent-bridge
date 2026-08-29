import assert from "node:assert/strict"
import test from "node:test"

import { createSshcAgentBridge } from "./index.js"

function decode(frame) {
  assert.ok(frame.startsWith("\u001b]6973;"))
  assert.ok(frame.endsWith("\u001b\\"))
  const payload = frame.slice(7, -2)
  return JSON.parse(Buffer.from(payload, "base64url").toString("utf8"))
}

test("reports lifecycle state and keeps the OpenCode title", async () => {
  const frames = []
  let seq = 10
  const plugin = createSshcAgentBridge({
    write: (frame) => frames.push(frame),
    nextSequence: () => seq++,
  })
  const hooks = await plugin({ directory: "/workspace" })

  await hooks.event({
    event: {
      type: "session.created",
      properties: { info: { id: "ses_123", title: "API cleanup", directory: "/repo" } },
    },
  })
  await hooks["chat.message"]({
    sessionID: "ses_123",
    model: { providerID: "openai", modelID: "gpt-5.6-sol" },
  })
  await hooks.event({
    event: { type: "permission.asked", properties: { sessionID: "ses_123" } },
  })
  await hooks.event({
    event: { type: "session.idle", properties: { sessionID: "ses_123" } },
  })

  const events = frames.map(decode)
  assert.deepEqual(events.map((event) => event.event), ["ready", "working", "attention", "ready"])
  assert.equal(events.at(-1).name, "API cleanup")
  assert.equal(events.at(-1).cwd, "/repo")
  assert.equal(events[1].model, "openai/gpt-5.6-sol")
  assert.deepEqual(events.map((event) => event.seq), [10, 11, 12, 13])
})

test("ignores child session metadata", async () => {
  const frames = []
  const plugin = createSshcAgentBridge({ write: (frame) => frames.push(frame), nextSequence: () => 1 })
  const hooks = await plugin({ directory: "/workspace" })
  await hooks.event({
    event: {
      type: "session.created",
      properties: { info: { id: "child", parentID: "root", title: "subagent" } },
    },
  })
  assert.equal(frames.length, 0)
})
