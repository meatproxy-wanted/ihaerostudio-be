// LOCAL INTEGRATION HARNESS ONLY. This file is not part of the FE repository.
import { ApiError } from "./errors";
import type { ApiClient } from "./types";

export function createHttpApi(): ApiClient {
  async function request<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
    const multipart = body instanceof FormData;
    const response = await fetch(`http://127.0.0.1:8100/api/studio${path}`, {
      method, signal, cache: "no-store",
      headers: { Authorization: "Bearer dev-only-change-me", ...(body === undefined || multipart ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new ApiError(response.status === 404 ? "not-found" : "failed", error?.detail?.message ?? "서버 요청에 실패했어요.");
    }
    return response.status === 204 ? undefined as T : response.json();
  }
  const p = (id: string) => `/projects/${encodeURIComponent(id)}`;
  const assist = <T>(id: string, action: string, body: unknown, signal?: AbortSignal) => request<T>(`${p(id)}/assist/${action}`, "POST", body, signal);
  return {
    projects: {
      list: () => request("/projects"), get: (id) => request(p(id)),
      create: (input, options) => {
        if (input.source.kind === "text") return request("/projects/text", "POST", { text: input.source.text, settings: input.settings }, options?.signal);
        const form = new FormData(); form.set("file", input.source.file); form.set("settings", JSON.stringify(input.settings));
        return request("/projects/pdf", "POST", form, options?.signal);
      },
      rename: (id, title) => request(`${p(id)}/title`, "PATCH", { title }),
      updateSettings: (id, settings) => request(`${p(id)}/settings`, "PUT", settings),
      remove: (id) => request(p(id), "DELETE"),
    },
    source: { get: (id) => request(`${p(id)}/source`) },
    structure: { get: (id) => request(`${p(id)}/structure`), save: (id, structure) => request(`${p(id)}/structure`, "PUT", structure) },
    document: {
      get: (id) => request(`${p(id)}/document`),
      generate: (id, options) => request(`${p(id)}/document/generate`, "POST", undefined, options?.signal),
      save: (id, document) => request(`${p(id)}/document`, "PUT", document),
    },
    assist: {
      simplify: (id, input, options) => assist(id, "simplify", input, options?.signal),
      split: (id, input, options) => assist(id, "split", input, options?.signal),
      termCandidates: (id, input, options) => assist(id, "terms", input, options?.signal),
      explainTerm: (id, input, options) => assist(id, "explain", input, options?.signal),
      imageCandidates: (id, input, options) => assist(id, "images", input, options?.signal),
      uploadImage: (id, input, options) => {
        const form = new FormData(); form.set("file", input.file); form.set("alt", input.alt); form.set("meaning", input.meaning);
        return assist(id, "upload-image", form, options?.signal);
      },
    },
    review: {
      latest: (id) => request(`${p(id)}/review`),
      run: (id, options) => request(`${p(id)}/review/run`, "POST", undefined, options?.signal),
      dismiss: (id, input) => request(`${p(id)}/review/dismiss`, "POST", input),
      dismissAll: (id, input) => request(`${p(id)}/review/dismiss-all`, "POST", input),
      restore: (id, input) => request(`${p(id)}/review/restore`, "POST", input),
      complete: (id, input) => request(`${p(id)}/review/complete`, "POST", input),
    },
    publications: {
      list: (id) => request(`${p(id)}/publications`),
      get: (id, publicationId) => request(`${p(id)}/publications/${encodeURIComponent(publicationId)}`),
      publish: (id) => request(`${p(id)}/publications`, "POST"),
      setPublic: (id, publicationId) => request(`${p(id)}/public`, "PUT", { publicationId }),
    },
    reader: { get: (id) => request(`/reader/${encodeURIComponent(id)}`) },
    demo: { sampleText: () => request("/demo/sample-text"), reset: () => request("/demo/reset", "POST") },
  };
}
