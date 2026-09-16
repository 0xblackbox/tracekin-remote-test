// Tracekin plugin for OpenCode (https://opencode.ai/docs/plugins/).
//
// OpenCode has no stdin-JSON hook commands; plugins are ESM modules run by
// Bun. This one translates OpenCode's hooks and events into the same
// payloads the other harnesses send and hands them to scripts/tracekin.py,
// which owns consent, session controls, the queue and delivery. No npm
// dependencies: only node:child_process, which Bun provides.
//
// Every export of a plugin module must be a plugin function, so this file
// exports exactly one.
import { spawn } from "node:child_process"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const SCRIPT = join(HERE, "..", "scripts", "tracekin.py")
const PYTHON = process.env.TRACEKIN_PYTHON || "python3"

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

export const TracekinPlugin = async ({ directory, worktree }) => {
  const assistantMessages = new Map() // sessionID -> Set(messageID)
  const lastAssistantText = new Map() // sessionID -> text
  const base = (sessionID, cwd) => ({ harness: "opencode", session_id: sessionID, cwd: cwd || directory, worktree })

  return {
    event: async ({ event }) => {
      const props = (event && event.properties) || {}
      if (event.type === "session.created") {
        const info = props.info || {}
        if (info.parentID) return // subagent sessions share the parent's project binding
        await run("start", { ...base(info.id, info.directory), hook_event_name: "SessionStart", source: "startup" })
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
        const payload = { ...base(sessionID), hook_event_name: "Stop" }
        if (lastAssistantText.has(sessionID)) payload.last_assistant_message = lastAssistantText.get(sessionID)
        await run("hook", payload)
      } else if (event.type === "session.deleted") {
        const sessionID = (props.info && props.info.id) || props.sessionID
        assistantMessages.delete(sessionID)
        lastAssistantText.delete(sessionID)
      }
    },
    "chat.message": async (input, output) => {
      // Runs before the model sees the prompt, so `tracekin off` is applied first.
      await run("hook", { ...base(input.sessionID), hook_event_name: "UserPromptSubmit", prompt: textOf(output && output.parts), message_id: input.messageID })
    },
    "tool.execute.after": async (input, output) => {
      await run("hook", {
        ...base(input.sessionID),
        hook_event_name: "PostToolUse",
        tool_name: input.tool,
        tool_use_id: input.callID,
        tool_input: input.args,
        tool_response: output ? output.output : undefined,
        tool_title: output ? output.title : undefined,
      })
    },
  }
}
