// Handles the chat API call and the SSE response stream.
import { API_ENDPOINTS } from "./config.js?v=2";

// Helper to get or generate anonymous persistent Device ID
function getDeviceId() {
  let id = localStorage.getItem("anza-device-id");
  if (!id) {
    id = "dev-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem("anza-device-id", id);
  }
  return id;
}

// Backend sends chunks as server-sent events. Keep the callbacks small so
// the UI layer can decide how to render each token.
export async function streamChat({ message, history, onStatus, onToken, onDone, onError, signal }) {
  try {
    const response = await fetch(API_ENDPOINTS.CHAT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Device-Id": getDeviceId()
      },
      body: JSON.stringify({
        message,
        history: history || []
      }),
      signal
    });

    if (!response.ok) {
      let detail = "";
      try {
        const errJson = await response.json();
        detail = errJson.detail;
      } catch {}
      throw new Error(detail || `Server returned HTTP ${response.status} (${response.statusText})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop(); // Save the unfinished event for the next chunk.

      for (const evt of events) {
        const trimmed = evt.trim();
        if (!trimmed.startsWith("data: ")) continue;

        try {
          const data = JSON.parse(trimmed.slice(6));
          if (data.done) {
            if (onDone) onDone();
            return;
          }
          if (data.status && onStatus) {
            onStatus(data.status);
          }
          if (data.text && onToken) {
            onToken(data.text);
          }
        } catch {
          // Some streams may send raw text instead of JSON.
          const rawToken = trimmed.slice(6);
          if (rawToken && rawToken !== "[DONE]" && onToken) {
            onToken(rawToken);
          }
        }
      }
    }

    if (onDone) onDone();

  } catch (err) {
    if (err.name === "AbortError") {
      console.log("Chat stream aborted by user.");
      return;
    }
    if (onError) onError(err);
  }
}
