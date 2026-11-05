export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== "GET") {
    headers.set("X-Requested-With", "tts-workspace");
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError("无法连接服务，请检查网络后重试。", 0, "network_error");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const messages: Record<string, string> = {
      unauthorized: "管理员密钥不正确，请重新输入。",
      request_too_large: "请求内容过大，请缩小参考音频或拆分文稿后重试。",
      invalid_request: "填写内容有误，请检查必填项和参数格式。",
    };
    throw new ApiError(
      messages[body.error] || body.message || "请求失败，请稍后重试。",
      response.status,
      body.error || "request_failed",
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生错误，请重试。";
}
