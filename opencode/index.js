import { closeSync, openSync, writeSync } from "node:fs"

const MAX_TEXT = 160
const MAX_ENCODED_PAYLOAD = 4096
let lastSequence = 0

function sequence() {
  const candidate = Number(process.hrtime.bigint() / 1000n)
  lastSequence = Math.max(candidate, lastSequence + 1)
  return lastSequence
}

function clean(value, limit = MAX_TEXT) {
  if (typeof value !== "string") return ""
  return [...value]
    .filter((character) => {
      const code = character.codePointAt(0)
      return code >= 0x20 && code !== 0x7f
    })
    .slice(0, limit)
    .join("")
    .trim()
}

function encode(event) {
  const payload = Buffer.from(JSON.stringify(event), "utf8").toString("base64url")
  if (payload.length > MAX_ENCODED_PAYLOAD) return undefined
  return `\u001b]6973;${payload}\u001b\\`
}

function writeTTY(frame) {
  if (process.platform === "win32") return false
  let descriptor
  try {
    descriptor = openSync("/dev/tty", "w")
    writeSync(descriptor, frame)
    return true
  } catch {
    return false
  } finally {
    if (descriptor !== undefined) {
      try {
        closeSync(descriptor)
      } catch {
        // The bridge is advisory and must never disturb OpenCode.
      }
    }
  }
}

function sessionID(properties) {
  return clean(properties?.sessionID ?? properties?.info?.id, 256)
}

export function createSshcAgentBridge({ write = writeTTY, nextSequence = sequence } = {}) {
  return async ({ directory }) => {
    const sessions = new Map()

    const report = (id, state, details = {}) => {
      const reference = clean(id, 256)
      if (!reference) return
      const previous = sessions.get(reference) ?? {}
      const current = {
        state,
        name: clean(details.name ?? previous.name),
        cwd: clean(details.cwd ?? previous.cwd ?? directory),
        model: clean(details.model ?? previous.model),
      }
      sessions.set(reference, current)
      const event = {
        v: 1,
        agent: "opencode",
        event: state,
        session: reference,
        seq: nextSequence(),
      }
      if (current.cwd) event.cwd = current.cwd
      if (current.model) event.model = current.model
      if (current.name) event.name = current.name
      const frame = encode(event)
      if (frame) write(frame)
    }

    const updateInfo = (info) => {
      if (!info || info.parentID) return
      const id = clean(info.id, 256)
      if (!id) return
      const previous = sessions.get(id)
      report(id, previous?.state ?? "ready", {
        name: info.title,
        cwd: info.directory,
      })
    }

    return {
      "chat.message": async (input) => {
        const model = input.model
          ? `${clean(input.model.providerID)}/${clean(input.model.modelID)}`
          : ""
        report(input.sessionID, "working", { model })
      },
      event: async ({ event }) => {
        const properties = event?.properties ?? {}
        switch (event?.type) {
          case "session.created":
          case "session.updated":
            updateInfo(properties.info)
            break
          case "session.status": {
            const status = properties.status?.type
            report(properties.sessionID, status === "idle" ? "ready" : "working")
            break
          }
          case "session.idle":
            report(properties.sessionID, "ready")
            break
          case "permission.asked":
          case "permission.updated":
            report(sessionID(properties), "attention")
            break
          case "permission.replied":
            report(properties.sessionID, "working")
            break
          case "session.error":
            report(properties.sessionID, "attention")
            break
          case "session.deleted": {
            const id = sessionID(properties)
            report(id, "ended", { name: properties.info?.title, cwd: properties.info?.directory })
            sessions.delete(id)
            break
          }
          default:
            break
        }
      },
    }
  }
}

export const SshcAgentBridge = createSshcAgentBridge()
