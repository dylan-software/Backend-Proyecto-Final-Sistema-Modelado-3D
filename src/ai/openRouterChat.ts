export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface ToolDefinition {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

export interface OpenRouterConfig {
  apiKey: string;
  model: string;
  url: string;
}

/**
 * Minimal OpenRouter client. OpenRouter mirrors the OpenAI chat
 * completions request/response shape (including `tools`/`tool_calls`),
 * so this is a thin, provider-agnostic wrapper — swapping the model
 * string is enough to try a different one behind OpenRouter.
 */
export async function callOpenRouter(
  config: OpenRouterConfig,
  messages: ChatMessage[],
  tools?: ToolDefinition[]
): Promise<ChatMessage> {
  if (!config.apiKey) {
    throw new Error("OPENROUTER_API_KEY is not set — the chat assistant is not configured yet.");
  }

  const response = await fetch(config.url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${config.apiKey}`,
      // Optional but recommended by OpenRouter for attribution/rate-limit purposes.
      "HTTP-Referer": process.env.FRONTEND_URL ?? "http://localhost:3000",
      "X-Title": "3D Space Decoration System",
    },
    body: JSON.stringify({
      model: config.model,
      messages,
      ...(tools ? { tools, tool_choice: "auto" } : {}),
    }),
  });

  if (!response.ok) {
    const bodyText = await response.text().catch(() => "");
    throw new Error(`OpenRouter request failed with HTTP ${response.status}: ${bodyText.slice(0, 300)}`);
  }

  const json = (await response.json()) as { choices?: { message?: ChatMessage }[] };
  const choice = json.choices?.[0];
  if (!choice?.message) {
    throw new Error("OpenRouter response did not include a message");
  }
  return choice.message as ChatMessage;
}
