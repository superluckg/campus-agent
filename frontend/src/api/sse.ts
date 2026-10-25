export interface StreamEvent {
  id: number;
  type: string;
  data: Record<string, unknown>;
}

/** 按行解析 SSE；保留跨网络分片的 CRLF 和不完整帧。 */
export class SSEParser {
  private buffer = "";
  private id = 0;
  private type = "message";
  private data: string[] = [];

  private readonly onEvent: (event: StreamEvent) => void;

  constructor(onEvent: (event: StreamEvent) => void) { this.onEvent = onEvent; }

  push(text: string): void {
    this.buffer += text;
    while (true) {
      const index = this.buffer.search(/[\r\n]/);
      if (index < 0) return;
      if (this.buffer[index] === "\r" && index === this.buffer.length - 1) return;
      const length = this.buffer[index] === "\r" && this.buffer[index + 1] === "\n" ? 2 : 1;
      const line = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + length);
      this.line(line);
    }
  }

  private line(line: string): void {
    if (!line) {
      const { id, type, data } = this;
      this.id = 0;
      this.type = "message";
      this.data = [];
      if (!data.length) return;
      const value: unknown = JSON.parse(data.join("\n"));
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new Error("流式事件的数据格式无效。");
      }
      this.onEvent({ id, type, data: value as Record<string, unknown> });
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    if (field === "event") this.type = value || "message";
    if (field === "id" && /^\d+$/.test(value) && Number.isSafeInteger(Number(value))) {
      this.id = Number(value);
    }
  }
}

export class StreamHttpError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(status === 410 ? "事件重放已过期，请读取已保存的消息。" : `无法建立流式连接（${status}）。`);
    this.status = status;
  }
}

export interface StreamOptions {
  signal?: AbortSignal;
  after?: number;
  retryDelays?: readonly number[];
  onRetry?: (attempt: number) => void;
}

const terminalTypes = new Set(["response.completed", "request.failed", "stream.expired"]);

function abortCheck(signal?: AbortSignal): void {
  if (signal?.aborted) throw signal.reason ?? new DOMException("连接已关闭。", "AbortError");
}

async function retryWait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  abortCheck(signal);
  await new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(signal?.reason ?? new DOMException("连接已关闭。", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

/** 只重连 GET 订阅，绝不重新提交聊天请求。 */
export async function consumeEventStream(
  url: string,
  headers: HeadersInit,
  onEvent: (event: StreamEvent) => void,
  options: StreamOptions = {},
): Promise<void> {
  let cursor = options.after ?? 0;
  const delays = options.retryDelays ?? [1000, 2000, 4000];
  for (let attempt = 0; ; attempt += 1) {
    abortCheck(options.signal);
    try {
      const target = new URL(url, globalThis.location?.origin ?? "http://localhost");
      target.searchParams.set("after", String(cursor));
      const response = await fetch(target, { headers, signal: options.signal });
      if (!response.ok || !response.body) throw new StreamHttpError(response.status);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let terminal = false;
      const parser = new SSEParser((event) => {
        if (terminal) return;
        if (event.id > 0 && event.id <= cursor) return;
        onEvent(event);
        cursor = Math.max(cursor, event.id);
        terminal = terminalTypes.has(event.type);
      });
      try {
        while (!terminal) {
          const { value, done } = await reader.read();
          if (done) {
            parser.push(decoder.decode());
            break;
          }
          parser.push(decoder.decode(value, { stream: true }));
        }
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
      if (terminal) return;
      throw new Error("流式连接提前断开。");
    } catch (error) {
      abortCheck(options.signal);
      if (error instanceof StreamHttpError && error.status < 500) throw error;
      if (attempt >= delays.length) throw error;
      options.onRetry?.(attempt + 1);
      await retryWait(delays[attempt], options.signal);
    }
  }
}
