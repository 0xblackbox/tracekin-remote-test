// Tracekin plugin for OpenCode (https://opencode.ai/docs/plugins/).
//
// OpenCode has no stdin-JSON hook commands; plugins are ESM modules run by
// Bun. This one translates OpenCode's hooks and events into the same
// payloads the other harnesses send and hands them to scripts/tracekin.py,
// which owns consent, session controls, the queue and delivery. No npm
// dependencies: only node:child_process, which Bun provides.
//
// Subagents run in child sessions with their own sessionID. Their activity
// is reported under the root session so that `tracekin off` in the session
// the user is typing in also covers everything its subagents do, and the
// task text the model writes for a subagent is never treated as a user
// prompt (or as a control command).
//
// Every export of a plugin module must be a plugin function, so this file
// exports exactly one.
import { spawn } from "node:child_process"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const SCRIPT = join(HERE, "..", "scripts", "tracekin.py")
const PYTHON = process.env.TRACEKIN_PYTHON || "python3"
const MAX_DEPTH = 8

// Run one hook invocation; never throws and never blocks OpenCode for long.
function run(action, payload) {
  return new Promise((resolve) => {
    let child
    try {
      child = spawn(PYTHON, [SCRIPT, action, "--harness", "opencode"], { stdio: ["pipe", "ignore", "ignore"] })
    } catch {
      resolve()
      return
    }
    const timer = setTimeout(() => {
      try { child.kill() } catch {}
      resolve()
    }, 5000)
    child.on("error", () => { clearTimeout(timer); resolve() })
    child.on("exit", () => { clearTimeout(timer); resolve() })
    try {
      child.stdin.end(JSON.stringify(payload))
    } catch {
      clearTimeout(timer)
      resolve()
    }
  })
}

function textOf(parts) {
  return (parts || [])
    .filter((part) => part && part.type === "text" && !part.synthetic && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n")
}

export const TracekinPlugin = async ({ client, directory, worktree }) => {
  const parents = new Map() // sessionID -> parentID, or null for a root session
  const assistantMessages = new Map() // sessionID -> Set(messageID)
  const lastAssistantText = new Map() // sessionID -> text
  const base = (sessionID, cwd) => ({ harness: "opencode", session_id: sessionID, cwd: cwd || directory, worktree })

  const learn = (info) => {
    if (info && typeof info.id === "string") parents.set(info.id, typeof info.parentID === "string" && info.parentID ? info.parentID : null)
  }

  // A session first seen mid-life (OpenCode restarted, session resumed): ask
  // the server once. Subagent sessions are created while the plugin runs, so
  // an id that cannot be looked up is treated as a root session.
  const lookup = async (sessionID) => {
    try {
      const response = await client.session.get({ path: { id: sessionID } })
      const info = response && response.data ? response.data : response
      if (info && info.id === sessionID) {
        learn(info)
        return
      }
    } catch {}
    parents.set(sessionID, null)
  }

  const rootOf = async (sessionID) => {
    let current = sessionID
    const seen = new Set()
    for (let depth = 0; depth < MAX_DEPTH && typeof current === "string" && current; depth++) {
      if (seen.has(current)) break
      seen.add(current)
      if (!parents.has(current)) await lookup(current)
      const parent = parents.get(current)
      if (!parent) return current
      current = parent
    }
    return current || sessionID
  }

  return {
    event: async ({ event }) => {
      const props = (event && event.properties) || {}
      if (event.type === "session.created" || event.type === "session.updated") {
        const info = props.info || {}
        learn(info)
        if (event.type === "session.created" && !info.parentID) {
          await run("start", { ...base(info.id, info.directory), hook_event_name: "SessionStart", source: "startup" })
        }
      } else if (event.type === "message.updated") {
        const info = props.info || {}
        if (info.role === "assistant" && info.sessionID && info.id) {
          if (!assistantMessages.has(info.sessionID)) assistantMessages.set(info.sessionID, new Set())
          assistantMessages.get(info.sessionID).add(info.id)
        }
      } else if (event.type === "message.part.updated") {
        const part = props.part || {}
        const ids = assistantMessages.get(part.sessionID)
        if (part.type === "text" && ids && ids.has(part.messageID) && typeof part.text === "string") {
          lastAssistantText.set(part.sessionID, part.text)
        }
      } else if (event.type === "session.idle") {
        const sessionID = props.sessionID
        if (!sessionID) return
        // A subagent going idle is not the end of the user's turn.
        if ((await rootOf(sessionID)) !== sessionID) return
        const payload = { ...base(sessionID), hook_event_name: "Stop" }
        if (lastAssistantText.has(sessionID)) payload.last_assistant_message = lastAssistantText.get(sessionID)
        await run("hook", payload)
      } else if (event.type === "session.deleted") {
        const sessionID = (props.info && props.info.id) || props.sessionID
        parents.delete(sessionID)
        assistantMessages.delete(sessionID)
        lastAssistantText.delete(sessionID)
      }
    },
    "chat.message": async (input, output) => {
      // Runs before the model sees the prompt, so `tracekin off` is applied first.
      // In a subagent session the "prompt" is task text written by the model: skip it.
      if ((await rootOf(input.sessionID)) !== input.sessionID) return
      await run("hook", { ...base(input.sessionID), hook_event_name: "UserPromptSubmit", prompt: textOf(output && output.parts), message_id: input.messageID })
    },
    "tool.execute.after": async (input, output) => {
      const root = await rootOf(input.sessionID)
      await run("hook", {
        ...base(root),
        hook_event_name: "PostToolUse",
        subagent: root !== input.sessionID,
        tool_name: input.tool,
        tool_use_id: input.callID,
        tool_input: input.args,
        tool_response: output ? output.output : undefined,
        tool_title: output ? output.title : undefined,
      })
    },
  }
}
