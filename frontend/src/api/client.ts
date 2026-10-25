import { consumeEventStream, type StreamEvent, type StreamOptions } from "./sse.ts";
export type { StreamEvent } from "./sse.ts";

const API_BASE = "/api";

export async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      const detail = body.detail;
      message = typeof detail === "string" ? detail : Array.isArray(detail)
        ? detail.map((item: { msg?: string }) => item.msg ?? JSON.stringify(item)).join("；")
        : detail ? JSON.stringify(detail) : message;
    } catch {
      // 保留默认错误文本。
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function streamRequest(
  path: string, onEvent: (event: StreamEvent) => void,
  options: StreamOptions = {},
): Promise<void> {
  return consumeEventStream(
    `${API_BASE}${path}`, {}, onEvent, options,
  );
}
